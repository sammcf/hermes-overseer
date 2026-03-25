"""Overseer alerts gateway hook.

Deployed to ~/.hermes/hooks/overseer-alerts/ on the VPS.

Events:
  gateway:startup — Check overseer /health on boot, log warning if unreachable.
  agent:end — Lightweight logging of session metrics (future: report back to overseer).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

_OVERSEER_URL = os.environ.get("OVERSEER_URL", "http://hermes-overseer:8900")


def handle(event_type: str, context: dict) -> None:
    """Gateway hook entry point."""
    if event_type == "gateway:startup":
        _on_startup(context)
    elif event_type == "agent:end":
        _on_agent_end(context)


def _on_startup(context: dict) -> None:
    """Check overseer health on gateway startup."""
    try:
        req = urllib.request.Request(f"{_OVERSEER_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "ok":
                logger.info("overseer-alerts: overseer is healthy")
            else:
                logger.warning("overseer-alerts: overseer returned unexpected status: %s", data)
    except urllib.error.URLError as e:
        logger.warning("overseer-alerts: overseer unreachable on startup: %s", e.reason)
    except Exception as e:
        logger.warning("overseer-alerts: health check failed: %s", e)


def _on_agent_end(context: dict) -> None:
    """Log session metrics on agent end. Non-blocking, informational only."""
    session_id = context.get("session_id", "unknown")
    tool_calls = context.get("tool_call_count", 0)
    tokens = context.get("total_tokens", 0)
    logger.debug(
        "overseer-alerts: agent session %s ended — %d tool calls, %d tokens",
        session_id[:8] if len(session_id) > 8 else session_id,
        tool_calls,
        tokens,
    )
