"""Hermes-overseer bridge plugin.

Registers an `overseer_status` tool that lets the LLM check overseer state,
and a `post_tool_call` hook that monitors for excessive tool usage.

Deployed to ~/.hermes/plugins/hermes-overseer-bridge/ on the VPS.
No hermes-agent source modifications required.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    """Load plugin config from config.yaml next to this file."""
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        logger.warning("hermes-overseer-bridge: config.yaml not found, using defaults")
        return {}
    with config_path.open() as f:
        return yaml.safe_load(f) or {}


# Session-scoped tool call counter for the hook
_session_tool_counts: dict[str, int] = {}


def register(ctx) -> None:
    """Plugin entry point — called by hermes PluginManager."""
    from . import schemas, tools

    config = _load_config()
    overseer_url = config.get("overseer_url", "http://hermes-overseer:8900")
    warn_threshold = config.get("tool_call_warn_threshold", 150)

    # --- Tool: overseer_status ---
    ctx.register_tool(
        name="overseer_status",
        toolset="overseer",
        schema=schemas.OVERSEER_STATUS_SCHEMA,
        handler=lambda args, ctx: tools.handle_overseer_status(args, ctx, overseer_url),
        description="Check the current status of the overseer monitoring system",
    )

    # --- Hook: post_tool_call (anomaly observer) ---
    def _post_tool_hook(event_data: dict) -> None:
        session_id = event_data.get("session_id", "unknown")
        _session_tool_counts[session_id] = _session_tool_counts.get(session_id, 0) + 1
        count = _session_tool_counts[session_id]
        if count == warn_threshold:
            logger.warning(
                "hermes-overseer-bridge: session %s hit %d tool calls (threshold: %d)",
                session_id[:8],
                count,
                warn_threshold,
            )

    ctx.register_hook("post_tool_call", _post_tool_hook)
    logger.info("hermes-overseer-bridge plugin loaded (overseer: %s)", overseer_url)
