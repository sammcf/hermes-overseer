# Hermes Overseer — Handoff Document

## What This Project Is

An off-device overseer service that monitors a BinaryLane VPS running
[hermes-agent](https://github.com/NousResearch/hermes-agent) (Nous Research's
persistent personal AI agent). The overseer treats the VPS as ephemeral
infrastructure: it monitors for compromise indicators, alerts the operator, and
can suspend/destroy/rebuild the VPS automatically via the BinaryLane API.

The hermes VPS is accessed exclusively via Tailscale (no public SSH). The
overseer runs on a trusted host on the same Tailnet.

---

## Design Decisions (Already Made)

### Security Philosophy

**Blast radius minimization + observability**, not perimeter defense. The VPS is
cattle, not pet. All credentials deployed to it are use-case-specific and
trivially revocable. If compromise is suspected, the response is: suspend/nuke
the VPS, revoke keys, rebuild from a clean base, restore audited state.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  Overseer (trusted host, home network / Tailnet)    │
│                                                     │
│  ┌──────────┐  ┌───────────┐  ┌────────────────┐   │
│  │ Monitor  │──│ Responder │  │ Provisioner    │   │
│  │ (poll)   │  │ (act)     │  │ (rebuild)      │   │
│  └──────────┘  └───────────┘  └────────────────┘   │
│       │              │               │              │
│  Tailnet rsync  BinaryLane API  BinaryLane API      │
│  BL metrics     power_off       rebuild/create      │
│  BL thresholds  take_backup     cloud-init          │
└─────────────────────────────────────────────────────┘
         │                    │
    ┌────┴────┐          ┌───┴────┐
    │ Hermes  │          │ Alerts │
    │ VPS     │          │ (TG)   │
    │tag:hermes          └────────┘
    └─────────┘
```

### Key Design Choices

1. **Overseer NEVER runs on the VPS.** All monitoring, alerting, and response
   logic runs off-device on a trusted Tailnet peer.

2. **BinaryLane `rebuild` preserves server ID + IP.** Preferred over
   destroy/create to keep billing identity stable. Tailscale re-auth is needed
   post-rebuild (use a pre-auth key in cloud-init).

3. **No hermes internal cron.** The agent's built-in cron system spawns full
   agent instances with all tools — too much privilege escalation surface.
   Disable it (remove/rename `tools/cronjob_tools.py` from install). Replace
   with system cron running specific scripts the agent writes.

4. **Docker backend for hermes.** Not local mode. The container is the real
   security boundary. Configure with explicit CPU/memory/disk limits and
   `network: false` for tasks that don't need internet.

5. **Email via dedicated Workspace address.** Create `hermes@domain` on Google
   Workspace. Server-side routing rules on primary account forward a filtered
   subset (excluding password resets, 2FA codes, financial, medical). Agent
   gets IMAP read-only for hermes@, no SMTP. If credentials are compromised,
   attacker only gets pre-filtered copies.

6. **Polling, not push.** BinaryLane has no webhooks. Overseer polls every 2-5
   minutes. Fine for this use case.

---

## Threat Model

### Vectors That Matter (Material Harm)

| # | Vector | Severity | Detection |
|---|--------|----------|-----------|
| 1 | API key abuse (cost) | Low-Med | Easy — provider dashboards + budget caps |
| 2 | Messaging impersonation (Telegram bot) | Low | Easy — message logs |
| 3 | Email read access exploitation | Med | Med — forwarding rule limits blast radius |
| 4 | Data exfiltration (conversations, memory) | Med | Hard — outbound traffic analysis |
| 5 | Memory/prompt poisoning (SOUL.md, MEMORY.md) | **High** | **Very hard** — behavioral, requires diffing |
| 6 | Outbound attack staging (legal liability) | Med | Med — connection target anomalies |
| 7 | Cost runaway (loops, manipulation) | Low-Med | Easy — budget alerts |
| 8 | Credential leakage via conversation history | Low-Med | Hard — state.db audit |

### Vectors Eliminated by Architecture

- **Lateral network movement** — Tailscale ACLs: `tag:hermes` has zero access
  to other Tailnet nodes.
- **Personal data theft** — dedicated empty VPS, nothing on it except hermes.
- **Persistent rootkit** — ephemeral rebuild strategy.
- **SSH brute force** — no public SSH, Tailscale-only.

### The Irreducible Risk

Prompt injection via web content the agent fetches. Can't be solved by
infrastructure — the agent needs internet access to be useful. Docker backend
contains blast radius. Observability layer detects consequences, not the
injection itself.

---

## Three-Tier Response Model

| Tier | Trigger Examples | Automated Action | Reversibility |
|------|-----------------|------------------|---------------|
| **Yellow** | Anomalous connection target; unexpected config diff; budget at 50% | Telegram alert only | N/A — human decides |
| **Orange** | Budget hard cap; sensitive file modified (.env, SOUL.md, config.yaml command_allowlist); sustained unknown outbound connections | `power_off` (instant) + Telegram alert + take backup | Resume via `power_on` if false alarm |
| **Red** | Multiple orange signals; large outbound to paste/file-share domains; agent modifying own .env via terminal | `take_backup` → `rebuild` from clean base + revoke all API keys + Telegram alert | Rebuild from last-known-good state |

Bias toward suspending early. Cost of false-positive suspend = minutes of
downtime. Cost of delayed response to real compromise = unbounded.

---

## BinaryLane API — Key Facts

- **Docs:** https://api.binarylane.com.au/reference/
- **CLI:** `pip install binarylane-cli` → `bl` command
- **Auth:** Bearer token, `BL_API_TOKEN` env var. **No token scoping** — full
  account access. Protect this token carefully.
- **No snapshots** — only backups. Use `take_backup` with
  `backup_type: "temporary"` (7-day retention).
- **No webhooks** — must poll for everything.
- **`rebuild` action** preserves server ID + IP. Accepts base image + options.
- **`power_off` action** is instant (hard power cut). Use for orange-tier.
- **Cloud-init** supported via `user_data` param on server create.
- **Threshold alerts** built-in: CPU, network in/out, data transfer, storage,
  memory. Queryable via `GET /v2/servers/threshold_alerts`.
- **Metrics** at 5-minute granularity via `GET /v2/samplesets/{server_id}/latest`.
- **Firewall rules** per-server via `change_advanced_firewall_rules` action.
  Full replacement semantics (send complete ruleset each time).
- **Backup before destroy** flow: `take_backup(temporary, oldest)` → poll
  action complete → `rebuild(base_image)`.
- **Rate limits:** Not documented. Implement defensive backoff.

### Key API Endpoints for This Project

```
GET    /v2/servers/{id}                    # Server status
POST   /v2/servers/{id}/actions            # All mutations (power_off, rebuild, take_backup, etc.)
GET    /v2/servers/{id}/actions            # Poll action status
GET    /v2/samplesets/{id}/latest          # Resource metrics
GET    /v2/servers/threshold_alerts        # Exceeded threshold alerts
GET    /v2/servers/{id}/advanced_firewall_rules
GET    /v2/servers/{id}/backups            # List backups
POST   /v2/servers                         # Create server
DELETE /v2/servers/{id}                    # Destroy server
```

---

## Hermes-Agent — Security-Relevant Code Facts

These are **verified against source code**, not just docs:

### What's Real

- **Docker backend security args:** Drops ALL caps (+3 back: DAC_OVERRIDE,
  CHOWN, FOWNER), `no-new-privileges`, PID limit 256, sized tmpfs mounts.
  File: `tools/environments/docker.py`, constant `_SECURITY_ARGS`.
- **MCP credential filtering:** Clean 8-key allowlist (PATH, HOME, USER, LANG,
  LC_ALL, TERM, SHELL, TMPDIR) + XDG_*. File: `tools/mcp_tool.py`,
  `_SAFE_ENV_KEYS` frozenset.
- **Dangerous command detection:** 27 regex patterns in `tools/approval.py`.
  Skipped entirely for Docker/Modal backends (by design — container IS the
  boundary).

### What's Weak

- **`HERMES_YOLO_MODE=1`** env var bypasses ALL dangerous command checks.
  Non-interactive contexts also auto-approve. An injected `export
  HERMES_YOLO_MODE=1` before a destructive command bypasses everything.
- **Regex evasion:** Variable expansion, base64 encoding, aliasing all bypass
  the dangerous command patterns.
- **Context file injection scanner:** Only 10 patterns in
  `agent/prompt_builder.py`. The skills scanner (`skills_guard.py`) has 80+.
  Trivially bypassed by rephrasing.
- **`command_allowlist` persistence:** "Always approve" stores coarse pattern
  keys (e.g., "rm" covers ALL recursive deletes). No expiry, no audit trail.
  Must manually edit config.yaml.
- **API keys in local mode:** `.env` loaded into `os.environ`. Agent can
  `cat ~/.hermes/.env` or `printenv`. Redaction is best-effort pattern matching
  (sk-, ghp_ prefixes). `file_tools` write-deny list for `.env` is trivially
  bypassed via `terminal_tool`.
- **No `--read-only` on Docker FS.** No `--network=none` by default. User
  volumes bind-mounted with no validation.

### Cron System (To Be Disabled)

Internal scheduler in `cron/scheduler.py`. Jobs stored as JSON in
`~/.hermes/cron/jobs.json`. Each tick spawns a **full new AIAgent** with
complete tool access. Prompt scanning is 10 shallow patterns. Disable by
removing `tools/cronjob_tools.py` from the hermes install.

### Teknium's Own Setup (From Twitter Thread, March 2026)

The lead dev:
- Runs hermes on a **dedicated empty Linux machine**
- Uses **local mode** (not Docker) for self-development
- Acknowledges API keys must coexist with agent code access
- Acknowledges prompt injection risk from web research
- When asked "How about safety?" answered **"Modal"** (cloud sandbox)
- Says "If you keep a lot of personal stuff on the machine...I would be
  concerned"

Community report (@jbentley): agent autonomously found and used Slack API keys
on an EC2 instance. Benign in context, but demonstrates the agent actively
searches for and uses any credentials it can find.

---

## Build Order

| Priority | Component | Description |
|----------|-----------|-------------|
| 1 | **Provisioning script** | Cloud-init / bash script for VPS setup. Installs Tailscale, Docker, ufw, hermes. Creates hermes user. Configures docker backend + messaging. Idempotent. |
| 2 | **BinaryLane API client** | Thin wrapper around: `power_off`, `power_on`, `take_backup`, `rebuild`, `server get`, `threshold_alerts`, `server create`. Shell or Python. |
| 3 | **Backup puller + differ** | Cron on overseer. rsync over Tailnet pulls `~/.hermes/` key files. Diffs sensitive files against last-known-good. Outputs change report. |
| 4 | **Monitor loop** | Combines: BL metrics poll + BL threshold alerts + file diffs + connection audit (SSH `ss -tnp` pull). Produces yellow/orange/red scoring. |
| 5 | **Alert + response** | Telegram bot for alerts. Tier-based automated response (power_off, take_backup, rebuild). Kill switch CLI command. |

### State Restore Policy (Post-Red Event)

- `state.db`: safe to restore (append-only conversation data)
- `memories/`, `SOUL.md`: **diff every backup against every previous backup**
  for unexpected mutations before restoring
- `config.yaml`: restore from version-controlled canonical copy, NEVER from
  backup
- `cron/`, `skills/`: restore only explicitly recognized items
- `.env`: **never restore** — always provision fresh keys

---

## Files the Overseer Monitors (rsync Pull List)

| Path | Why | Orange Trigger |
|------|-----|----------------|
| `config.yaml` | command_allowlist additions, backend changes, new MCP servers | Any unexpected diff |
| `.env` | API key additions or modifications | Any diff at all |
| `SOUL.md` | Identity/persona manipulation (prompt poisoning) | Any unexpected diff |
| `memories/MEMORY.md` | Behavioral manipulation | Large or suspicious diff |
| `memories/USER.md` | Behavioral manipulation | Large or suspicious diff |
| `cron/jobs.json` | Scheduled task changes (if cron not fully disabled) | Any new job |
| `skills/` | New agent-created or installed skills | Any new file |
| `logs/` | Error and gateway logs | Informational (yellow at most) |
| `sessions/` | Conversation history | Informational for post-incident audit |

---

## External Accounts / Services Needed

- **BinaryLane** — VPS hosting + API token
- **Tailscale** — Tailnet with ACLs. Pre-auth key for `tag:hermes`.
- **OpenRouter** — LLM provider. Set hard budget cap.
- **Anthropic** — Claude API key. Set workspace spend limit.
- **Telegram** — Bot token for hermes gateway + separate bot (or same bot) for
  overseer alerts.
- **Google Workspace** — `hermes@domain` account with IMAP. Server-side routing
  rules on primary account. Exclude: password resets, 2FA, financial, medical.
- **Other LLM providers** (Codex, etc.) — use-case-specific keys with budget caps.

---

## Resolved Design Decisions

### 1. Overseer Host

Home server running Fedora immutable. Always-on, on the Tailnet. Immutable
base OS resists host drift.

### 2. Alert Channels

Two independent channels, neither depends on hermes infrastructure:
- **Telegram** — dedicated overseer bot (separate token/chat from hermes bot)
- **Email** — to a non-hermes address

### 3. Connection Allowlist

**Allowlist-only alerting** with hard lockdown as an escalation option.
Anything not on the list flags yellow; sustained unknown connections flag
orange. Hard firewall lockdown available as a manual or automated escalation.

**Core (always needed):**
- `api.openrouter.ai` — primary LLM router
- `api.anthropic.com` — Claude API
- `api.openai.com` — OpenAI / Codex
- `generativelanguage.googleapis.com` — Google Gemini
- `api.telegram.org` — messaging gateway
- `imap.gmail.com` — hermes@ email (IMAP read-only)
- `login.tailscale.com` + DERP relays — Tailnet connectivity
- `registry-1.docker.io`, `ghcr.io` — Docker image pulls
- `github.com`, `api.github.com` — skills, updates
- OS package mirrors (apt/deb)

**Likely needed (enable per use case):**
- `firecrawl.dev` — web scraping
- `api.browserbase.com` — browser automation
- `fal.ai` — image generation
- `elevenlabs.io` — TTS
- `app.honcho.dev` — cross-session memory
- `discord.com/api` — Discord gateway

**Excluded (not needed for this deployment):**
- Z.ai, Kimi, MiniMax (Chinese LLM providers)
- Modal, Daytona (alternative backends — using Docker)
- Home Assistant, Slack, Signal, WhatsApp
- Tinker-Atropos, W&B (ML training)

List lives in a config file, tuned during settling-in period.

### 4. Hermes Source Modifications

**None.** No fork, no post-install patching. Run hermes stock. The cron
system stays in place — Docker backend is the real containment boundary, and
the overseer's monitoring layer catches any concerning consequences. The
original motivation for disabling cron was reliability of scheduled tasks, not
meaningful attack surface reduction.

### 5. Cost & Usage Governance

Per-provider usage governance, enforced by the overseer:

- **Anthropic, OpenAI, Gemini** — OAuth accounts with rolling window limits.
  Overseer monitors usage against those windows. Alert as limits approach so
  hermes degrades gracefully to cheaper models rather than hitting a wall.
- **OpenRouter** — prepaid wallet. Alert at $10 remaining, then $5. No
  auto-topup.
- **All other metered APIs** — structured usage telemetry. Surface consumption
  data, spot anomalies, set thresholds over time.
- **Dispatch strategy** — aggressively route to free/cheap OpenRouter models
  for tasks that don't need frontier capability. Throttle/downshift when any
  provider's limits approach.

**Critical:** the overseer owns the dispatch/routing policy as the
authoritative source. Hermes consumes it. Any drift in hermes config from the
overseer's canonical version is an **orange-tier event**. These controls NEVER
change without direct operator input.

### 6. Rebuild Speed

Non-metric. Containment is instant via `power_off`; rebuild speed is
convenience only. **Hybrid approach:** provision from scratch as the default
path, periodic snapshots of known-good state for faster recovery when desired.

### 7. Tailscale ACLs

SSH-only, expand later as needed:
```json
{
  "acls": [
    {"action": "accept", "src": ["tag:hermes"], "dst": ["autogroup:internet:*"]},
    {"action": "accept", "src": ["tag:overseer"], "dst": ["tag:hermes:22"]}
  ]
}
```
- `tag:hermes` → outbound internet only, zero Tailnet access to other nodes
- `tag:overseer` → SSH (port 22) to hermes only (rsync + connection audits)
- Additional ports/tags added as needed

### 8. Overseer Self-Management

No silent failure mode. Three interlocking mechanisms:

1. **Overseer → Telegram heartbeat**: periodic "alive" message (e.g., every
   30min). Absence of messages = overseer is down.
2. **Overseer → VPS canary file**: overseer touches a heartbeat file on the
   VPS via SSH on a short interval (every few minutes). Doubles as a
   connectivity canary.
3. **VPS → operator alert**: lightweight daemon on the VPS watches the
   heartbeat file mtime. If stale (>1hr), alerts operator directly via
   Telegram (using hermes' own bot, since the overseer's bot may be down for
   the same reason). Operator then decides: check overseer logs, restart
   overseer, or manually suspend VPS.
4. **Overseer side**: if it can't touch the heartbeat file, automatic
   **orange alert** (VPS unreachable). Can suspend via BinaryLane API (public
   internet, independent of Tailscale) if needed.
