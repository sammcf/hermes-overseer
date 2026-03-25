"""Alert dispatch: fan out to all configured channels independently."""

from __future__ import annotations

import asyncio
from typing import Any

from overseer.alert import email as email_channel
from overseer.alert import telegram as telegram_channel
from overseer.config import AlertsConfig
from overseer.types import AlertTier, Result, Signal


async def dispatch_alert(
    alerts_config: AlertsConfig, signals: list[Signal], tier: AlertTier
) -> list[Result[Any]]:
    """Send alerts to all channels. Both are attempted regardless of individual failures."""
    results: list[Result[Any]] = []
    results.extend(await telegram_channel.send_alert(alerts_config.telegram, signals, tier))
    # Email is synchronous (SMTP) — run in thread to avoid blocking
    email_result = await asyncio.to_thread(
        email_channel.send_alert, alerts_config.email, signals, tier
    )
    results.append(email_result)
    return results
