# Hermes Overseer — Record of Work

Append-only log of actions, decisions, and rationale. Newest entries at bottom.

---

## 2026-03-13 — Initial implementation

- Built full overseer: config, BL client, monitors (metrics, files, connections,
  config drift, cost), evaluator, response actions, alerting (Telegram + email),
  heartbeat (canary + pulse). 221 tests.
- Functional-first, railway-oriented (`Result[T] = Ok | Err`), frozen Pydantic
  config, no mutable state.

## 2026-03-13 — VPS provisioned (Phase 2)

- Provisioned BinaryLane VPS (592953, std-1vcpu, Melbourne, Ubuntu 24.04).
- Installed hermes-agent v0.2.0 with Docker backend, Telegram gateway.
- Locked down: UFW deny all except tailscale0, no public SSH.
- Captured canonical config to `config/hermes-canonical.yaml`.

## 2026-03-14 — Distrobox deployment + operational docs

- Deployed overseer in Arch Linux distrobox on home server (Fedora immutable).
- Systemd user service via `distrobox enter`. Host network namespace = Tailscale
  transparent.
- Created RUNBOOK.md, DESIGN.md, HANDOFF.md.

## 2026-03-15 — Post-rebuild provisioning pipeline

**Goal:** `_action_rebuild` produces a fully operational hermes-agent VPS with
zero manual intervention.

**Built:**
- `provision/provisioner.py` — 10-step railway-style pipeline: TS cleanup ->
  cloud-init render -> BL rebuild -> poll -> wait SSH -> wait cloud-init ->
  push .env & config -> start service -> verify.
- `tailscale.py` — pre-rebuild stale device removal via Tailscale API.
- `ssh.py` — `push_file_content()` (pipe via stdin), `wait_for_ssh()` (polling).
- `config.py` — `HermesSecretsConfig` for hermes .env mapping, VpsConfig gains
  `tailscale_api_key_env`, `tailscale_tailnet`, `ssh_public_key_path`,
  `docker_image`.
- `scripts/run_rebuild.py` — manual trigger with `--dry-run`.
- Cloud-init template: Tailscale background install workaround, systemd service
  file, docker pull, hermes-agent clone + venv.

**Bugs found & fixed during end-to-end testing (5 rebuild attempts):**
1. BL API ignores top-level `user_data` in rebuild action — must nest under
   `options.user_data`. Discovered by inspecting `/var/lib/cloud/instance/user-data.txt`
   on the VPS and finding old cloud-init instead of ours.
2. Tailscale reusable auth keys create duplicate devices with `-1` suffix when
   the old device record still exists. Fix: pre-rebuild cleanup via TS API.
3. `cloud-init status --wait` exits code 2 on "degraded done" (deprecation
   warnings in BL's default config). Fix: suppress exit code, we only need to
   know it finished.
4. SSH available mid-cloud-init (when Tailscale joins) but hermes not yet
   installed. Fix: added `cloud-init status --wait` step before pushing config.
5. Cloud-init `~` doesn't expand in runcmd (runs as root sh). Fix: use explicit
   `/home/$user/...` paths.
6. `string.Template` interprets `$(seq 1 120)` as variable. Fix: escape as
   `$$(seq 1 120)`.
7. Tailscale deb postinst runs `tailscale up` interactively, blocking cloud-init.
   Fix: background `apt-get install`, poll for daemon, pkill the blocker, then
   explicit `--authkey` auth.

**Also fixed 5 pre-existing mypy errors** across `connections.py`, `actions.py`,
`pipeline.py`, `__main__.py`, `alert/__init__.py`.

**Result:** End-to-end rebuild stable. ~4 minutes from trigger to active
hermes-gateway service. 246 tests, mypy clean, ruff clean.

## 2026-03-15 — State preservation + local terminal backend (WU-001)

**Goal:** Rebuild is a non-event — zero loss of continuity for hermes.

**Built:**
- `src/overseer/backup/snapshot.py` — `take_snapshot`, `restore_snapshot`,
  `find_latest_snapshot`, `prune_snapshots`. SSH tar + rsync pull/push. All
  code dirs excluded from archive (hermes-agent, sandboxes, bin, image_cache,
  document_cache).
- `src/overseer/ssh.py` — added `rsync_push` (for restore) and `rsync_pull_file`
  (for snapshot download, no --relative).
- `config.py` — `OverseerConfig` gains `backup_interval_seconds` (14400),
  `backup_retention_count` (24), `backup_dir`, `secrets_dir` with path
  expansion. `HermesSecretsConfig` gains `file_secrets` for Google OAuth files
  (`google_token.json`, `google_client_secret.json`).
- `provision/provisioner.py` — Step 5c: restore latest snapshot before pushing
  config. Best-effort (failure logs warning, pipeline continues). Step 8b: push
  file-based secrets from `secrets_dir` alongside `.env` and `config.yaml`.
- `__main__.py` — backup interval check in main loop (same `time.monotonic()`
  deadline pattern as canary/heartbeat). Runs `take_snapshot` then
  `prune_snapshots` every `backup_interval_seconds`.
- `config/hermes-canonical.yaml` — switched `terminal.backend: docker` →
  `local`, removed `container_*` and `docker_image` fields.
- `cloud-init/hermes-vps.yaml` — removed `docker pull ${docker_image}`, added
  Google Workspace Python deps (`google-api-python-client google-auth-oauthlib
  google-auth-httplib2`).
- `config/overseer.example.yaml` — backup config fields, `file_secrets`,
  `google_token.json`/`google_client_secret.json` in `watched_files`.

**32 new tests.** 278 total, mypy clean, ruff clean.

**Next: bootstrap Google OAuth files** — copy from current VPS docker sandbox
to `~/.config/hermes-overseer/` as source of truth for future rebuilds.

## 2026-03-15 — mini-swe-agent + Claude Code in cloud-init

- `mini-swe-agent` is a git submodule not included in hermes's `packages.find`.
  Docker terminal backend fails without it. Added `git submodule update --init`
  + `pip install -e mini-swe-agent` to cloud-init.
- Added Claude Code installation (`curl ... install.sh | bash`).
- Added hermes venv + `~/.local/bin` to PATH in `.bashrc`.

## 2026-03-17 — Group chat support for Telegram alerts

**Goal:** Route low-severity alerts and heartbeats to a Telegram group chat,
keeping RED alerts in the operator's DM for immediate attention.

**Changes:**
- `config.py` — renamed `TelegramConfig.chat_id` → `dm_chat_id`, added optional
  `group_chat_id`. Three new helpers: `command_chat_ids` (frozenset of both),
  `alert_chat_ids(tier)` (tier-based routing), `heartbeat_chat_id` (group
  preferred).
- `alert/telegram.py` — `send_alert()` now returns `list[Result]`, iterating
  over `config.alert_chat_ids(tier)` to fan out per-tier.
- `alert/__init__.py` — `dispatch_alert` uses `extend()` for telegram results.
- `bot/commands.py` — auth check uses `command_chat_ids` frozenset (accepts
  commands from both DM and group).
- `__main__.py` — heartbeat uses `heartbeat_chat_id` property.
- `config/overseer.example.yaml` — updated with `dm_chat_id` + `group_chat_id`.
- Tests updated across 4 files; new test for group chat command acceptance.

**Routing logic:**

| Tier | Destination |
|------|-------------|
| YELLOW / ORANGE | `group_chat_id` only (falls back to `dm_chat_id` if unset) |
| RED | Both `dm_chat_id` and `group_chat_id` |
| Heartbeat | `group_chat_id` (falls back to `dm_chat_id`) |
| Bot commands | Accepted from either chat |

**Backwards compatible:** `group_chat_id` defaults to `None`; existing configs
with only `dm_chat_id` (formerly `chat_id`) behave identically to before.

**319 tests pass** (4 pre-existing SSH failures in CI env). No new dependencies.
