"""Telegram getUpdates polling — fetches pending bot commands without long-polling."""

from __future__ import annotations

import httpx

from overseer.types import Err, Ok, Result

_GETUPDATE_TIMEOUT = 10  # seconds; timeout=0 means non-blocking on Telegram's side


async def fetch_updates(bot_token: str, offset: int) -> Result[list[dict]]:  # type: ignore[type-arg]
    """Call getUpdates with timeout=0 (non-blocking, returns immediately).

    offset: pass last_update_id + 1 to acknowledge previously processed updates.
    Returns Ok(list[update]) — may be empty — or Err on network failure.
    """
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"offset": offset, "timeout": 0, "limit": 100}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=_GETUPDATE_TIMEOUT)
        data: dict = response.json()  # type: ignore[type-arg]
        if not data.get("ok"):
            return Err(
                f"Telegram getUpdates error: {data.get('description', 'unknown')}",
                source="bot_poller",
            )
        return Ok(data.get("result", []))
    except httpx.TimeoutException:
        return Err("getUpdates timed out", source="bot_poller")
    except httpx.HTTPError as exc:
        return Err(f"getUpdates HTTP error: {exc}", source="bot_poller")
