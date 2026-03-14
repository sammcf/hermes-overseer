# Hermes Overseer — Design Document

## What This Is

An off-device watchdog for a BinaryLane VPS running
[hermes-agent](https://github.com/NousResearch/hermes-agent). Overseer
monitors the VPS for compromise indicators, alerts the operator, and can
suspend or rebuild the VPS automatically.

The VPS is treated as ephemeral infrastructure — cattle, not pet. All
credentials deployed to it are use-case-specific and trivially revocable. If
something looks wrong, the bias is toward early suspension: minutes of downtime
is cheap; delayed response to a real problem is not.

## Why It Exists

Hermes-agent is a persistent AI agent with shell access, API keys, and internet
connectivity. It's useful, but the attack surface is real:

- Prompt injection via web content is an irreducible risk
- The agent actively searches for and uses any credentials it can find
- Docker backend provides containment, but it's not a full sandbox
- Cost runaway from model loops or manipulation is plausible

No amount of in-process sandboxing fully addresses these. The solution is
external observation + automated response from a trusted host that the agent
can never reach.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Overseer (distrobox on trusted host)       │
│                                             │
│  Monitor ──→ Evaluator ──→ Response Engine  │
│    │              │              │           │
│  SSH/rsync    Signal[]      power_off       │
│  BL metrics   → Tier        take_backup     │
│  BL alerts                  rebuild         │
│  ss -tnp                    alert           │
│  config diff                revoke_keys     │
│  cost check                                 │
└──────┬──────────────────────────┬───────────┘
       │ Tailscale SSH            │ BinaryLane API
       │                          │ Telegram API
  ┌────┴────┐               ┌────┴────┐
  │ Hermes  │               │ Alerts  │
  │ VPS     │               │ (TG+   │
  │         │               │  email) │
  └─────────┘               └─────────┘
```

**Overseer never runs on the VPS.** It runs on a home server on the same
Tailnet, inside a distrobox for portability and clean teardown.

## Core Design Decisions

### 1. Functional-first, railway-oriented

All domain logic is pure functions transforming inputs to outputs. No classes
with mutable state. The core type system:

- **`Signal`** — a frozen dataclass: source, tier, message, timestamp. Every
  monitor check produces zero or more signals.
- **`AlertTier`** — enum: YELLOW, ORANGE, RED. Ordered, comparable.
- **`Result[T]`** = `Ok[T] | Err` — railway-style error handling. Failed SSH
  doesn't crash the loop; it produces a signal.
- **`PollState`** — frozen dataclass tracking sustained unknown connection
  count and last poll time. Passed into and returned from functions, never
  mutated in place.

### 2. Three-tier response model

| Tier | Meaning | Automated Action |
|------|---------|-----------------|
| **YELLOW** | Anomaly worth noting | Alert only (human decides) |
| **ORANGE** | Likely problem | `power_off` + alert + backup |
| **RED** | Confirmed bad or multiple orange | backup + `rebuild` + revoke keys + alert |

Escalation rule: any single RED signal → RED. Two or more ORANGE signals →
RED. This prevents an attacker from staying just below individual orange
thresholds.

### 3. Overseer owns the canonical config

Hermes's `config.yaml` is checked against a version-controlled canonical copy
on every poll. Drift in security-relevant fields (model routing, provider
config, MCP servers, toolsets) is an orange-tier event. The operator is the
only one who changes the canonical config.

### 4. No hermes source modifications

Hermes runs stock. No fork, no patches. Docker backend is the containment
boundary; overseer monitors consequences externally. This avoids a maintenance
burden and lets hermes update freely.

### 5. Polling, not push

BinaryLane has no webhooks. Overseer polls every 2 minutes. Each poll cycle
runs five independent checks in sequence:

1. **BL metrics** — CPU >90%, memory >95% → YELLOW
2. **BL threshold alerts** — provider-side alerts → YELLOW
3. **File changes** — rsync pull + diff against last-known-good
4. **Connections** — `ss -tnp` via SSH, check against allowlist
5. **Config drift** — hermes config vs canonical for policy fields

### 6. Mutual heartbeat (no silent failure)

- Overseer touches a canary file on the VPS every 3 minutes via SSH
- A bash watcher on the VPS alerts the operator (via hermes's own Telegram
  bot) if the canary goes stale (>1 hour)
- Overseer sends its own Telegram heartbeat every 30 minutes
- If overseer can't reach the VPS, that itself is an orange signal

### 7. Distrobox deployment

The overseer runs in an Arch Linux distrobox rather than directly on the host.
This gives:

- **Idempotent standup/teardown** — `deploy.sh` / `deploy.sh --teardown`
- **Immutable OS compatibility** — no system packages needed on the host
- **Clean dependency isolation** — Python venv inside the container
- **Host network transparency** — Tailscale just works (shared namespace)

The host runs a systemd user service that `distrobox enter`s the container.

## Module Structure

```
overseer/
├── config.py              # Pydantic v2 config schema (all frozen models)
├── types.py               # AlertTier, Signal, Ok/Err, PollState
├── ssh.py                 # SSH command exec + rsync via subprocess
├── __main__.py            # CLI + main poll loop
├── binarylane/
│   ├── client.py          # httpx client, auth, exponential backoff + jitter
│   ├── queries.py         # Server status, metrics, alerts, backups
│   └── actions.py         # power_off, power_on, take_backup, rebuild
├── monitor/
│   ├── pipeline.py        # Composition root: wires all checks → signals
│   ├── metrics.py         # BL resource metrics evaluation
│   ├── files.py           # rsync pull + diff classification
│   ├── connections.py     # ss output parsing + allowlist check
│   ├── config_drift.py    # Hermes config vs canonical
│   └── cost.py            # Per-provider usage + budget signals
├── response/
│   ├── evaluator.py       # Signals → tier (pure function)
│   ├── actions.py         # Tier → action sequence execution
│   └── state_restore.py   # Post-rebuild: classify files for safe restore
├── alert/
│   ├── telegram.py        # Telegram Bot API (raw httpx)
│   ├── email.py           # SMTP alerts
│   └── __init__.py        # dispatch_alert → both channels
├── heartbeat/
│   ├── canary.py          # SSH touch file on VPS
│   └── pulse.py           # Telegram alive message
└── provision/
    └── builder.py         # Render cloud-init template for rebuilds
```

**Key principle:** modules communicate via frozen types. No module imports
another's internals. `monitor/pipeline.py` is the composition root.

## Secret Handling

Secrets are never stored in config files. The config schema uses `*_env`
fields (e.g., `api_token_env: "BL_API_TOKEN"`) that name environment
variables. At runtime, `resolve_secret()` reads from `os.environ`. The
systemd service loads these from `~/.config/hermes-overseer/env`.

## State Restore Policy (Post-Red)

After a rebuild, not everything can be blindly restored:

| Category | Files | Policy |
|----------|-------|--------|
| **Safe** | `state.db` | Restore directly (append-only conversation data) |
| **Audit** | `memories/`, `SOUL.md` | Diff against all prior backups before restoring |
| **Canonical** | `config.yaml` | Restore from overseer's version-controlled copy, never from backup |
| **Skip** | `.env` | Never restore — always provision fresh keys |
| **Unknown** | everything else | Flag for manual review |

## Known Gaps

- `monitor/cost.py`: `check_rolling_window_usage` is stubbed — needs
  per-provider API investigation for Anthropic/OpenAI/Gemini usage endpoints
- `response/actions.py`: `revoke_keys` is a placeholder — needs integration
  with each provider's key management API
- BL API token storage is plaintext in the env file — a future improvement
  would use basic encryption or a secrets manager
- Overseer needs its own dedicated Telegram bot (currently shares config
  placeholder with hermes)
- Tailscale ACLs (`tag:hermes`, `tag:overseer`) not yet configured

## Test Coverage

221 tests. All monitors, evaluator, response actions, alert formatting,
heartbeat, config loading, BL client retry/backoff are covered. External
calls (httpx, subprocess) are mocked via `respx` and `monkeypatch`.
