#!/usr/bin/env bash
# Deploy hermes-overseer on Fedora immutable.
# Creates user, dirs, installs from source, enables systemd service.
set -euo pipefail

INSTALL_DIR="/opt/hermes-overseer"
DATA_DIR="/var/lib/hermes-overseer"
CONFIG_DIR="/etc/hermes-overseer"
SERVICE_USER="overseer"

echo "=== Hermes Overseer Deployment ==="

# Create service user (no login shell)
if ! id "$SERVICE_USER" &>/dev/null; then
    sudo useradd --system --shell /usr/sbin/nologin --home-dir "$DATA_DIR" "$SERVICE_USER"
    echo "Created user: $SERVICE_USER"
fi

# Create directories
sudo mkdir -p "$DATA_DIR"/{state,logs}
sudo mkdir -p "$CONFIG_DIR"
sudo mkdir -p "$INSTALL_DIR"

# Copy source
sudo cp -r . "$INSTALL_DIR/"

# Create venv and install
cd "$INSTALL_DIR"
sudo -u "$SERVICE_USER" python3 -m venv "$DATA_DIR/.venv" 2>/dev/null || python3 -m venv "$DATA_DIR/.venv"
sudo "$DATA_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

# Copy example config if no config exists
if [ ! -f "$CONFIG_DIR/overseer.yaml" ]; then
    sudo cp "$INSTALL_DIR/config/overseer.example.yaml" "$CONFIG_DIR/overseer.yaml"
    echo "Copied example config to $CONFIG_DIR/overseer.yaml — EDIT BEFORE STARTING"
fi

# Install systemd service
sudo cp "$INSTALL_DIR/systemd/hermes-overseer.service" /etc/systemd/system/
sudo systemctl daemon-reload

# Set ownership
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"

echo ""
echo "=== Deployment complete ==="
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/overseer.yaml"
echo "  2. Create $CONFIG_DIR/env with required secrets"
echo "  3. sudo systemctl enable --now hermes-overseer"
