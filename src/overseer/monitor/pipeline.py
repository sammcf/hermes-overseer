"""Poll-cycle composition root — wires all monitor checks into a single pass."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx

from overseer.config import Config
from overseer.monitor.config_drift import check_config_drift
from overseer.monitor.connections import check_connections, evaluate_sustained_unknowns
from overseer.monitor.files import evaluate_file_changes, pull_watched_files
from overseer.monitor.metrics import check_bl_metrics, check_bl_threshold_alerts
from overseer.response.actions import execute_actions, get_action_sequence
from overseer.response.evaluator import evaluate
from overseer.types import AlertTier, Err, Ok, PollState, Result, Signal

logger = logging.getLogger(__name__)


def _guard(source: str, fn: Any, *args: Any, **kwargs: Any) -> list[Signal]:
    """Call fn(*args, **kwargs) → list[Signal], catching all exceptions.

    Any unhandled exception becomes a YELLOW signal rather than crashing the pipeline.
    """
    try:
        result: list[Signal] = fn(*args, **kwargs)
        return result
    except Exception as exc:
        logger.exception("Unhandled exception in monitor check %r", source)
        return [
            Signal.now(
                source=source,
                tier=AlertTier.YELLOW,
                message=f"Monitor check failed: {exc}",
            )
        ]


def run_poll_cycle(
    config: Config,
    bl_client: httpx.Client,
    poll_state: PollState,
) -> tuple[list[Signal], PollState]:
    """Run all monitor checks for one poll cycle.

    Sequence:
    1. BL metrics check
    2. BL threshold alerts
    3. File change evaluation (pull via rsync first)
    4. Connection check
    5. Config drift check

    Each check is wrapped so failures produce signals rather than crashes.
    Returns (all_signals, updated_poll_state).
    """
    signals: list[Signal] = []

    # 1. BL metrics
    signals.extend(
        _guard("metrics", check_bl_metrics, bl_client, config.vps.server_id)
    )

    # 2. BL threshold alerts
    signals.extend(
        _guard("metrics", check_bl_threshold_alerts, bl_client)
    )

    # 3. File changes — pull first, then evaluate
    def _file_check() -> list[Signal]:
        pull_result = pull_watched_files(
            hostname=config.vps.tailscale_hostname,
            user=config.vps.ssh_user,
            hermes_home=config.vps.hermes_home,
            watched_files=config.monitor.watched_files,
            state_dir=config.overseer.data_dir,
        )
        if isinstance(pull_result, Err):
            return [
                Signal.now(
                    source="files",
                    tier=AlertTier.YELLOW,
                    message=f"rsync pull failed: {pull_result.error}",
                )
            ]
        return evaluate_file_changes(
            hermes_home=config.vps.hermes_home,
            watched_files=config.monitor.watched_files,
            state_dir=config.overseer.data_dir,
        )

    signals.extend(_guard("files", _file_check))

    # 4. Connection check — drives sustained_unknown_count state
    connection_signals: list[Signal] = []
    try:
        conn_result = check_connections(
            hostname=config.vps.tailscale_hostname,
            user=config.vps.ssh_user,
            allowlist=config.monitor.connection_allowlist,
        )
        if isinstance(conn_result, Ok):
            connection_signals = conn_result.value
        else:
            connection_signals = [
                Signal.now(
                    source="connections",
                    tier=AlertTier.YELLOW,
                    message=f"Connection check failed: {conn_result.error}",
                )
            ]
    except Exception as exc:
        logger.exception("Unhandled exception in connection check")
        connection_signals = [
            Signal.now(
                source="connections",
                tier=AlertTier.YELLOW,
                message=f"Monitor check failed: {exc}",
            )
        ]

    unknown_count = len(connection_signals)
    new_sustained = poll_state.sustained_unknown_count + 1 if unknown_count > 0 else 0

    sustained_tier = evaluate_sustained_unknowns(
        unknown_count=new_sustained,
        threshold=config.monitor.sustained_unknown_threshold,
    )
    if sustained_tier is AlertTier.ORANGE:
        signals.append(
            Signal.now(
                source="connections",
                tier=AlertTier.ORANGE,
                message=(
                    f"Sustained unknown connections for {new_sustained} consecutive"
                    f" poll cycles (threshold={config.monitor.sustained_unknown_threshold})"
                ),
            )
        )

    signals.extend(connection_signals)

    # 5. Config drift
    def _drift_check() -> list[Signal]:
        hermes_rel = config.vps.hermes_home.lstrip("/")
        current_cfg_path = f"{config.overseer.data_dir}/current/{hermes_rel}/config.yaml"
        canonical_cfg_path = config.cost.canonical_hermes_config
        return check_config_drift(
            current_config_path=current_cfg_path,
            canonical_config_path=canonical_cfg_path,
            policy_fields=config.cost.dispatch_policy_fields,
        )

    signals.extend(_guard("config_drift", _drift_check))

    updated_state = replace(
        poll_state,
        sustained_unknown_count=new_sustained,
        last_poll_time=datetime.now(UTC),
    )
    return signals, updated_state


def run_response_cycle(
    signals: list[Signal],
    config: Config,
    bl_client: httpx.Client,
) -> list[Result[Any]]:
    """Evaluate signals, determine action sequence, execute actions.

    Returns the list of action results (Ok or Err per action).
    Returns an empty list if there is nothing to respond to.
    """
    tier = evaluate(signals)
    if tier is None:
        return []

    actions = get_action_sequence(tier, config.response)
    return execute_actions(
        actions=actions,
        server_id=config.vps.server_id,
        bl_client=bl_client,
        alerts_config=config.alerts,
        signals=signals,
        tier=tier,
        config=config,
    )
