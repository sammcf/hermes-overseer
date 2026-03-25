"""Periodic Telegram alive-pulse for the overseer."""

from __future__ import annotations

import httpx

from overseer.types import Err, Ok, Result


async def send_pulse(bot_token: str, chat_id: str, status_summary: str) -> Result[dict]:  # type: ignore[type-arg]
    """POST a heartbeat pulse to the Telegram Bot API.

    Args:
        bot_token: Telegram bot token (resolved from env by the caller).
        chat_id: Target Telegram chat / channel ID.
        status_summary: Human-readable summary, e.g.
            "Overseer alive. Last poll: 2026-03-14T12:00:00Z. VPS: OK"

    Returns:
        Ok(response_json) on success, Err on any failure.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    message = f"<b>Hermes Overseer — Heartbeat</b>\n{status_summary}"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=15)
        data: dict = response.json()  # type: ignore[type-arg]
        if not data.get("ok"):
            description = data.get("description", "unknown error")
            return Err(f"Telegram API error: {description}", source="pulse")
        return Ok(data)
    except httpx.TimeoutException:
        return Err("Telegram pulse request timed out", source="pulse")
    except httpx.HTTPError as exc:
        return Err(f"Telegram pulse HTTP error: {exc}", source="pulse")
