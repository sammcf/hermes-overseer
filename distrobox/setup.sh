#!/usr/bin/env bash
# Setup script — runs INSIDE the hermes-overseer distrobox container.
# Installs uv, creates venv, installs overseer from source.
set -euo pipefail

OVERSEER_SRC="${OVERSEER_SRC:-/var/mnt/stuff/development/hermes-overseer}"
DATA_DIR="${OVERSEER_DATA_DIR:-$HOME/.local/share/hermes-overseer}"
CONFIG_DIR="${OVERSEER_CONFIG_DIR:-$HOME/.config/hermes-overseer}"

echo "=== hermes-overseer distrobox setup ==="
echo "Source:  $OVERSEER_SRC"
echo "Data:    $DATA_DIR"
echo "Config:  $CONFIG_DIR"

# Install uv if not present
if ! command -v uv &>/dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Create directories
mkdir -p "$DATA_DIR"/{state,logs}
mkdir -p "$CONFIG_DIR"

# Create venv and install
echo "Creating venv and installing overseer..."
cd "$OVERSEER_SRC"
uv venv "$DATA_DIR/.venv"
uv pip install --python "$DATA_DIR/.venv/bin/python" -e "$OVERSEER_SRC"

# Copy example config if no config exists
if [ ! -f "$CONFIG_DIR/overseer.yaml" ]; then
    cp "$OVERSEER_SRC/config/overseer.example.yaml" "$CONFIG_DIR/overseer.yaml"
    echo "Copied example config to $CONFIG_DIR/overseer.yaml — EDIT BEFORE STARTING"
fi

# Copy canonical hermes config
cp "$OVERSEER_SRC/config/hermes-canonical.yaml" "$CONFIG_DIR/hermes-canonical.yaml"

echo ""
echo "=== Setup complete ==="
echo "Next: create $CONFIG_DIR/env with secrets, then start the service"
