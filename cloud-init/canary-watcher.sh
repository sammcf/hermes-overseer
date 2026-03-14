#!/usr/bin/env bash
# Canary watcher: alerts operator if overseer stops touching the canary file.
# Runs via cron on the VPS. Uses hermes' Telegram bot (not overseer's).
# Self-contained: only depends on curl and stat.

CANARY_FILE="${CANARY_FILE:-/tmp/overseer-canary}"
STALE_THRESHOLD="${STALE_THRESHOLD:-3600}"  # seconds
TG_BOT_TOKEN="${CANARY_TG_BOT_TOKEN}"
TG_CHAT_ID="${CANARY_TG_CHAT_ID}"

if [ -z "$TG_BOT_TOKEN" ] || [ -z "$TG_CHAT_ID" ]; then
    echo "ERROR: CANARY_TG_BOT_TOKEN and CANARY_TG_CHAT_ID must be set" >&2
    exit 1
fi

if [ ! -f "$CANARY_FILE" ]; then
    MESSAGE="⚠️ CANARY ALERT: Overseer canary file missing ($CANARY_FILE). Overseer may be down."
    curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT_ID" \
        -d text="$MESSAGE" > /dev/null
    exit 0
fi

MTIME=$(stat -c %Y "$CANARY_FILE" 2>/dev/null)
NOW=$(date +%s)
AGE=$(( NOW - MTIME ))

if [ "$AGE" -gt "$STALE_THRESHOLD" ]; then
    MESSAGE="⚠️ CANARY ALERT: Overseer canary stale (${AGE}s old, threshold ${STALE_THRESHOLD}s). Overseer may be down or network severed."
    curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT_ID" \
        -d text="$MESSAGE" > /dev/null
fi
