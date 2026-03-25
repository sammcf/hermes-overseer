"""Tier → action sequence: mapping and execution."""

from __future__ import annotations

import logging
from typing import Any

from overseer.config import Config, ResponseConfig
from overseer.types import AlertTier, Err, Ok, Result, Signal

logger = logging.getLogger(__name__)


def get_action_sequence(tier: AlertTier, response_config: ResponseConfig) -> list[str]:
    """Return the configured action list for the given alert tier."""
    tier_config = {
        AlertTier.YELLOW: response_config.yellow,
        AlertTier.ORANGE: response_config.orange,
        AlertTier.RED: response_config.red,
    }
    return list(tier_config[tier].actions)


async def execute_actions(
    actions: list[str],
    server_id: int,
    bl_client: Any,
    alerts_config: Any,
    signals: list[Signal],
    tier: AlertTier,
    config: Config | None = None,
) -> list[Result[Any]]:
    """Execute each action in sequence, collecting results.

    Known actions:
    - "alert"       — dispatch a Telegram/email alert via overseer.alert
    - "power_off"   — BinaryLane power_off
    - "take_backup" — BinaryLane take_backup
    - "rebuild"     — BinaryLane rebuild (or full provision pipeline if config provided)
    - "revoke_keys" — placeholder: logs a warning, returns Ok

    Continues executing even if earlier actions fail.
    Dependencies (bl_client, alerts_config) are passed in to keep this
    function testable without live infrastructure.
    """
    results: list[Result[Any]] = []

    for action in actions:
        result = await _execute_one(
            action, server_id, bl_client, alerts_config, signals, tier, config=config
        )
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _execute_one(
    action: str,
    server_id: int,
    bl_client: Any,
    alerts_config: Any,
    signals: list[Signal],
    tier: AlertTier,
    config: Config | None = None,
) -> Result[Any]:
    if action == "alert":
        return await _action_alert(alerts_config, signals, tier)
    if action == "power_off":
        return _action_power_off(bl_client, server_id)
    if action == "take_backup":
        return _action_take_backup(bl_client, server_id)
    if action == "rebuild":
        return _action_rebuild(bl_client, server_id, config=config)
    if action == "revoke_keys":
        return _action_revoke_keys()
    logger.warning("Unknown action %r — skipping", action)
    return Err(f"Unknown action: {action!r}", source="actions")


async def _action_alert(alerts_config: Any, signals: list[Signal], tier: AlertTier) -> Result[Any]:
    try:
        from overseer.alert import dispatch_alert

        results = await dispatch_alert(alerts_config, signals, tier)
        return Ok(results)
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


def _action_rebuild(
    bl_client: Any, server_id: int, config: Config | None = None
) -> Result[Any]:
    if config is not None:
        try:
            from overseer.provision.provisioner import provision_after_rebuild

            return provision_after_rebuild(config, bl_client)
        except ImportError as exc:
            return Err(f"provisioner unavailable: {exc}", source="actions")
    try:
        from overseer.binarylane.actions import rebuild

        return rebuild(bl_client, server_id, image_id="ubuntu-24.04")
    except ImportError as exc:
        return Err(f"binarylane unavailable: {exc}", source="actions")


def _action_revoke_keys() -> Result[str]:
    logger.warning("revoke_keys action is not yet implemented — placeholder only")
    return Ok("revoke_keys: placeholder — no operation performed")
