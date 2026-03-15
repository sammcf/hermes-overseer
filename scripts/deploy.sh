#!/usr/bin/env bash
# Deploy hermes-overseer via distrobox (Arch Linux).
# Idempotent: safe to re-run. Use --teardown to remove.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINER_NAME="hermes-overseer"
SERVICE_NAME="hermes-overseer.service"

# ── Teardown mode ──────────────────────────────────────────────
if [[ "${1:-}" == "--teardown" ]]; then
    echo "=== Tearing down hermes-overseer ==="
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
    distrobox rm --force "$CONTAINER_NAME" 2>/dev/null || true
    echo "Done. Container and service removed."
    echo "(Config and data dirs preserved — delete manually if needed)"
    exit 0
fi

# ── Create / replace distrobox ─────────────────────────────────
echo "=== Creating distrobox container ==="
distrobox assemble create --replace --file "$PROJECT_DIR/distrobox/overseer.ini"

# ── Run setup inside container ─────────────────────────────────
echo "=== Running setup inside container ==="
distrobox enter "$CONTAINER_NAME" -- bash "$PROJECT_DIR/distrobox/setup.sh"

# ── Sync hermes canonical config to live config dir ────────────
echo "=== Syncing hermes-canonical.yaml ==="
mkdir -p "$HOME/.config/hermes-overseer"
cp "$PROJECT_DIR/config/hermes-canonical.yaml" "$HOME/.config/hermes-overseer/hermes-canonical.yaml"

# ── Install systemd user service on host ───────────────────────
echo "=== Installing systemd user service ==="
mkdir -p "$HOME/.config/systemd/user"
cp "$PROJECT_DIR/systemd/hermes-overseer.service" "$HOME/.config/systemd/user/$SERVICE_NAME"
systemctl --user daemon-reload

# Enable lingering so user services survive logout
if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "yes"; then
    echo "Enabling lingering for $USER..."
    sudo loginctl enable-linger "$USER"
fi

echo ""
echo "=== Deployment complete ==="
echo "Next steps:"
echo "  1. Edit ~/.config/hermes-overseer/overseer.yaml"
echo "  2. Create ~/.config/hermes-overseer/env with secrets:"
echo "       BL_API_TOKEN=..."
echo "       OVERSEER_TG_BOT_TOKEN=..."
echo "       OVERSEER_EMAIL_PASSWORD=..."
echo "       TS_HERMES_AUTH_KEY=..."
echo "  3. systemctl --user enable --now $SERVICE_NAME"
