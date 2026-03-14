"""Telegram Bot API alert channel."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from overseer.config import TelegramConfig, resolve_secret
from overseer.types import AlertTier, Err, Ok, Result, Signal

_TIER_LABEL: dict[AlertTier, str] = {
    AlertTier.YELLOW: "&#x26A0;&#xFE0F; YELLOW",
    AlertTier.ORANGE: "&#x1F7E0; ORANGE",
    AlertTier.RED: "&#x1F534; RED",
}


def format_alert(signals: list[Signal], tier: AlertTier) -> str:
    """Format signals into an HTML Telegram message."""
    label = _TIER_LABEL[tier]
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"<b>Hermes Overseer — {label} Alert</b>",
        f"<i>{ts}</i>",
        "",
    ]
    for sig in signals:
        lines.append(f"<b>[{sig.source.upper()}]</b> {sig.message}")
        sig_ts = sig.timestamp.strftime("%H:%M:%SZ")
        lines.append(f"  <i>@ {sig_ts}</i>")
    return "\n".join(lines)


def send_telegram(bot_token: str, chat_id: str, message: str) -> Result[dict]:  # type: ignore[type-arg]
    """POST a message to the Telegram Bot API. Returns Ok(response_json) or Err."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        response = httpx.post(url, json=payload, timeout=15)
        data: dict = response.json()  # type: ignore[type-arg]
        if not data.get("ok"):
            description = data.get("description", "unknown error")
            return Err(f"Telegram API error: {description}", source="telegram")
        return Ok(data)
    except httpx.TimeoutException:
        return Err("Telegram request timed out", source="telegram")
    except httpx.HTTPError as exc:
        return Err(f"Telegram HTTP error: {exc}", source="telegram")


def send_alert(
    config: TelegramConfig, signals: list[Signal], tier: AlertTier
) -> Result[dict]:  # type: ignore[type-arg]
    """Resolve credentials, format, and send a Telegram alert."""
    try:
        bot_token = resolve_secret(config.bot_token_env)
    except RuntimeError as exc:
        return Err(str(exc), source="telegram")
    message = format_alert(signals, tier)
    return send_telegram(bot_token, config.chat_id, message)
