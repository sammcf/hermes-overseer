"""Tier → action sequence: mapping and execution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from overseer.config import ResponseConfig
from overseer.types import AlertTier, Err, Ok, Result, Signal

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def get_action_sequence(tier: AlertTier, response_config: ResponseConfig) -> list[str]:
    """Return the configured action list for the given alert tier."""
    tier_config = {
        AlertTier.YELLOW: response_config.yellow,
        AlertTier.ORANGE: response_config.orange,
        AlertTier.RED: response_config.red,
    }
    return list(tier_config[tier].actions)


def execute_actions(
    actions: list[str],
    server_id: int,
    bl_client: Any,
    alerts_config: Any,
    signals: list[Signal],
    tier: AlertTier,
) -> list[Result[Any]]:
    """Execute each action in sequence, collecting results.

    Known actions:
    - "alert"       — dispatch a Telegram/email alert via overseer.alert
    - "power_off"   — BinaryLane power_off
    - "take_backup" — BinaryLane take_backup
    - "rebuild"     — BinaryLane rebuild
    - "revoke_keys" — placeholder: logs a warning, returns Ok

    Continues executing even if earlier actions fail.
    Dependencies (bl_client, alerts_config) are passed in to keep this
    function testable without live infrastructure.
    """
    results: list[Result[Any]] = []

    for action in actions:
        result = _execute_one(action, server_id, bl_client, alerts_config, signals, tier)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _execute_one(
    action: str,
    server_id: int,
    bl_client: Any,
    alerts_config: Any,
    signals: list[Signal],
    tier: AlertTier,
) -> Result[Any]:
    if action == "alert":
        return _action_alert(alerts_config, signals, tier)
    if action == "power_off":
        return _action_power_off(bl_client, server_id)
    if action == "take_backup":
        return _action_take_backup(bl_client, server_id)
    if action == "rebuild":
        return _action_rebuild(bl_client, server_id)
    if action == "revoke_keys":
        return _action_revoke_keys()
    logger.warning("Unknown action %r — skipping", action)
    return Err(f"Unknown action: {action!r}", source="actions")


def _action_alert(alerts_config: Any, signals: list[Signal], tier: AlertTier) -> Result[Any]:
    try:
        from overseer.alert import dispatch_alert  # type: ignore[attr-defined]

        return dispatch_alert(alerts_config, signals, tier)
    except ImportError as exc:
        return Err(f"alert dispatch unavailable: {exc}", source="actions")


def _action_power_off(bl_client: Any, server_id: int) -> Result[Any]:
    try:
        from overseer.binarylane.actions import power_off

        return power_off(bl_client, server_id)
    except ImportError as exc:
        return Err(f"binarylane unavailable: {exc}", source="actions")


def _action_take_backup(bl_client: Any, server_id: int) -> Result[Any]:
    try:
        from overseer.binarylane.actions import take_backup

        return take_backup(bl_client, server_id)
    except ImportError as exc:
        return Err(f"binarylane unavailable: {exc}", source="actions")


def _action_rebuild(bl_client: Any, server_id: int) -> Result[Any]:
    try:
        from overseer.binarylane.actions import rebuild

        return rebuild(bl_client, server_id, image_id="ubuntu-24.04")
    except ImportError as exc:
        return Err(f"binarylane unavailable: {exc}", source="actions")


def _action_revoke_keys() -> Result[str]:
    logger.warning("revoke_keys action is not yet implemented — placeholder only")
    return Ok("revoke_keys: placeholder — no operation performed")
