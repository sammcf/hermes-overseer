#!/usr/bin/env bash
# Emergency kill switch: power off VPS immediately via BinaryLane API.
# Usage: ./kill-switch.sh [server_id]
set -euo pipefail

SERVER_ID="${1:-${BL_SERVER_ID}}"
BL_TOKEN="${BL_API_TOKEN}"

if [ -z "$SERVER_ID" ] || [ -z "$BL_TOKEN" ]; then
    echo "Usage: BL_API_TOKEN=xxx ./kill-switch.sh <server_id>"
    echo "  or: BL_API_TOKEN=xxx BL_SERVER_ID=xxx ./kill-switch.sh"
    exit 1
fi

echo "Powering off server $SERVER_ID..."
RESPONSE=$(curl -s -X POST \
    "https://api.binarylane.com.au/v2/servers/${SERVER_ID}/actions" \
    -H "Authorization: Bearer $BL_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"type": "power_off"}')

echo "Response: $RESPONSE"
echo ""
echo "Server $SERVER_ID power_off initiated."
echo "Verify at: https://home.binarylane.com.au/servers/$SERVER_ID"
