"""Tool handlers for the hermes-overseer bridge plugin."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


def handle_overseer_status(args: dict, ctx, overseer_url: str) -> str:
    """Call GET /status on the overseer API. Returns JSON status string.

    Uses urllib (stdlib) to avoid adding httpx as a plugin dependency.
    """
    token = os.environ.get("OVERSEER_API_TOKEN", "")
    url = f"{overseer_url}/status"

    try:
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        lines = [
            f"Overseer v{data.get('version', '?')}",
            f"Uptime: {data.get('uptime_seconds', 0) / 3600:.1f}h",
            f"Last poll: {data.get('last_poll_time', 'never')}",
            f"Unknown connections (sustained): {data.get('sustained_unknown_count', 0)}",
            f"VPS: {data.get('vps_hostname', '?')}",
        ]
        return "\n".join(lines)

    except urllib.error.HTTPError as e:
        return f"Overseer API error: HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"Overseer unreachable: {e.reason}"
    except Exception as e:
        return f"Overseer status check failed: {e}"
