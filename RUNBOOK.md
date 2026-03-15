# Hermes Overseer — Deployment Runbook

Overseer runs in a distrobox (Arch Linux) on the host machine, managed as a
systemd user service. The container shares the host's network namespace, so
Tailscale access to the VPS works transparently.

---

## Prerequisites

- **Host OS:** Fedora (immutable or workstation) with `distrobox` and `podman`
- **Tailscale:** running on the host, joined to the same Tailnet as the hermes VPS
- **Source:** this repo cloned locally

## File Layout

```
~/.config/hermes-overseer/
├── overseer.yaml              # Config (copied from example on first deploy)
├── hermes-canonical.yaml      # Authoritative hermes config (copied from repo)
└── env                        # Secrets (you create this)

~/.local/share/hermes-overseer/
├── .venv/                     # Python virtualenv (inside distrobox)
├── state/                     # Last-known-good file copies, poll state
└── logs/

~/.config/systemd/user/
└── hermes-overseer.service    # Host-side systemd unit
```

---

## Deploy (Fresh or Update)

```bash
./scripts/deploy.sh
```

This is idempotent. It:

1. Creates (or replaces) the `hermes-overseer` distrobox from `distrobox/overseer.ini`
2. Enters the container and runs `distrobox/setup.sh` — installs uv, creates venv, installs overseer from source
3. Copies the systemd unit to `~/.config/systemd/user/` and reloads
4. Enables user lingering (so the service survives logout)

### First-time: create secrets file

```bash
cat > ~/.config/hermes-overseer/env << 'EOF'
BL_API_TOKEN=<binarylane api token>
OVERSEER_TG_BOT_TOKEN=<overseer telegram bot token>
OVERSEER_EMAIL_PASSWORD=<smtp password>
TS_HERMES_AUTH_KEY=<tailscale pre-auth key for hermes — reusable, auto-approve, tag:hermes>
TS_API_KEY=<tailscale api key — needed for pre-rebuild device cleanup>
OPENROUTER_API_KEY=<openrouter api key — deployed to hermes .env>
TELEGRAM_BOT_TOKEN=<hermes telegram bot token — deployed to hermes .env>
TELEGRAM_ALLOWED_USERS=<comma-separated telegram user IDs for hermes>
FIRECRAWL_API_KEY=<firecrawl api key — deployed to hermes .env>
EOF
chmod 600 ~/.config/hermes-overseer/env
```

### First-time: edit config

```bash
$EDITOR ~/.config/hermes-overseer/overseer.yaml
```

Key fields to set:
- `alerts.telegram.chat_id` — your Telegram user/group ID
- `alerts.email.from_address` / `to_address` — real email addresses
- `vps.server_id` — BinaryLane server ID (592953 for current instance)

### Validate config without starting

```bash
distrobox enter hermes-overseer -- \
    ~/.local/share/hermes-overseer/.venv/bin/python -m overseer \
    --config ~/.config/hermes-overseer/overseer.yaml --validate-only
```

---

## Start / Stop / Status

```bash
# Start and enable on boot
systemctl --user enable --now hermes-overseer

# Check status
systemctl --user status hermes-overseer

# View logs
journalctl --user -u hermes-overseer -f

# Stop
systemctl --user stop hermes-overseer

# Restart (e.g. after config change)
systemctl --user restart hermes-overseer
```

---

## Update Code

After pulling new changes to the repo:

```bash
# Re-run deploy (recreates container + reinstalls)
./scripts/deploy.sh

# Restart the service to pick up changes
systemctl --user restart hermes-overseer
```

The venv uses `pip install -e` (editable install), so for pure Python changes
you can just restart the service without redeploying. Redeploy if dependencies
change.

---

## Teardown

```bash
./scripts/deploy.sh --teardown
```

This stops the service, disables it, and removes the distrobox container.
Config and data directories are preserved — delete manually if desired:

```bash
rm -rf ~/.config/hermes-overseer
rm -rf ~/.local/share/hermes-overseer
```

---

## Troubleshooting

### Container won't start

```bash
# Check container exists
distrobox list

# Try entering manually
distrobox enter hermes-overseer

# If corrupted, force recreate
distrobox rm --force hermes-overseer
./scripts/deploy.sh
```

### Service fails immediately

```bash
# Check logs
journalctl --user -u hermes-overseer --no-pager -n 50

# Common causes:
# - Missing env file → create ~/.config/hermes-overseer/env
# - Bad config → run --validate-only (see above)
# - Container not running → distrobox enter hermes-overseer first
```

### Can't reach VPS

```bash
# Verify Tailscale is up on host
tailscale status

# Test SSH through distrobox
distrobox enter hermes-overseer -- ssh hermes@hermes-vps "echo ok"
```

### Manual rebuild

```bash
# Load secrets
set -a && source ~/.config/hermes-overseer/env && set +a

# Validate only (no rebuild)
uv run python scripts/run_rebuild.py --dry-run

# Full rebuild (~4 minutes to operational hermes-agent)
uv run python scripts/run_rebuild.py
```

The rebuild pipeline: removes stale Tailscale devices → renders cloud-init →
BinaryLane rebuild → waits for SSH via Tailscale → waits for cloud-init
completion → pushes `.env` and `config.yaml` → starts hermes-gateway service.

### Emergency: manual VPS shutdown

If overseer is down and you need to kill the VPS immediately:

```bash
# Via kill-switch script (needs BL_API_TOKEN in env)
BL_API_TOKEN=<token> ./scripts/kill-switch.sh 592953

# Or via BinaryLane dashboard
# https://home.binarylane.com.au/servers/592953
```

---

## Distrobox Internals

The container is defined in `distrobox/overseer.ini`:

- **Image:** `archlinux:latest`
- **No init:** runs as a regular container (no systemd inside)
- **Packages:** `python python-pip openssh rsync` (installed at container creation)
- **Network:** shares host namespace (Tailscale, DNS all inherited)
- **Filesystem:** host home directory is bind-mounted (source code, config, data all accessible)

The host-side systemd unit runs `distrobox enter hermes-overseer -- <command>`,
which exec's the Python process inside the container. Environment variables set
via `EnvironmentFile=` are passed through automatically.
