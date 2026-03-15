"""Tailscale API operations for device management."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from overseer.types import Err, Ok, Result

logger = logging.getLogger(__name__)

_TS_API_BASE = "https://api.tailscale.com/api/v2"


def _ts_client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=_TS_API_BASE,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15.0,
    )


def remove_devices_by_hostname(
    api_key: str,
    tailnet: str,
    hostname: str,
) -> Result[int]:
    """Remove all Tailscale devices matching the given hostname.

    Returns Ok(count_removed) or Err on API failure.
    This is a pre-rebuild cleanup step — stale devices with the same hostname
    cause Tailscale to append -1, -2 suffixes, breaking DNS resolution.
    """
    client = _ts_client(api_key)

    try:
        resp = client.get(f"/tailnet/{tailnet}/devices")
        if not resp.is_success:
            return Err(
                f"Tailscale list devices failed ({resp.status_code}): {resp.text}",
                source="tailscale",
            )

        devices: list[dict[str, Any]] = resp.json().get("devices", [])
        matching = [
            d for d in devices
            if d.get("hostname") == hostname or d.get("name", "").startswith(f"{hostname}.")
        ]

        if not matching:
            logger.info("No stale Tailscale devices found for hostname %s", hostname)
            return Ok(0)

        removed = 0
        for device in matching:
            device_id = device.get("id", device.get("nodeId"))
            if not device_id:
                continue
            del_resp = client.delete(f"/device/{device_id}")
            if del_resp.is_success:
                logger.info("Removed Tailscale device %s (id=%s)", hostname, device_id)
                removed += 1
            else:
                logger.warning(
                    "Failed to remove Tailscale device %s (id=%s): %s",
                    hostname, device_id, del_resp.text,
                )

        return Ok(removed)

    except httpx.RequestError as exc:
        return Err(f"Tailscale API request failed: {exc}", source="tailscale")
    finally:
        client.close()
