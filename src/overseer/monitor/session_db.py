"""Session DB monitors: activity anomalies and token budget enforcement.

Queries hermes state.db via SSH + sqlite3. WAL-mode reads don't block hermes writes.
"""

from __future__ import annotations

import json
import logging

from overseer.config import SessionThresholds, TokenBudgetConfig
from overseer.ssh import run_ssh_command
from overseer.types import AlertTier, Err, Signal

logger = logging.getLogger(__name__)


def _query_db(
    hostname: str,
    user: str,
    db_path: str,
    sql: str,
    timeout: int = 15,
) -> dict | list | None:
    """Run a sqlite3 query via SSH, parse JSON output.

    Returns parsed JSON on success, None on failure (after logging).
    """
    # -json not available on all sqlite3 builds; use json_object/json_array in SQL instead
    cmd = f'sqlite3 {db_path} "{sql}"'
    result = run_ssh_command(hostname, user, cmd, timeout=timeout)
    if isinstance(result, Err):
        return None
    try:
        return json.loads(result.value.strip())
    except (json.JSONDecodeError, ValueError):
        logger.warning("Malformed JSON from sqlite3: %s", result.value[:200])
        return None


def check_session_activity(
    hostname: str,
    user: str,
    hermes_db_path: str,
    thresholds: SessionThresholds,
) -> list[Signal]:
    """Detect anomalous session patterns via state.db queries."""
    window_secs = thresholds.window_hours * 3600
    inactivity_secs = thresholds.inactivity_alert_hours * 3600

    sql = (
        "SELECT json_object("
        f"'recent_sessions', (SELECT COUNT(*) FROM sessions WHERE started_at > unixepoch() - {inactivity_secs}),"
        f"'max_tool_calls', (SELECT COALESCE(MAX(tool_call_count), 0) FROM sessions WHERE started_at > unixepoch() - {window_secs}),"
        f"'total_tokens', (SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM sessions WHERE started_at > unixepoch() - {window_secs}),"
        "'longest_active_hours', (SELECT COALESCE(MAX((unixepoch() - started_at) / 3600.0), 0) FROM sessions WHERE ended_at IS NULL)"
        ");"
    )

    data = _query_db(hostname, user, hermes_db_path, sql)
    if data is None:
        return [Signal.now(
            source="session_db",
            tier=AlertTier.YELLOW,
            message="Failed to query hermes session DB",
        )]

    signals: list[Signal] = []

    # Inactivity check
    if data.get("recent_sessions", 1) == 0:
        signals.append(Signal.now(
            source="session_db",
            tier=AlertTier.YELLOW,
            message=f"No sessions in last {thresholds.inactivity_alert_hours}h — hermes may be down",
        ))

    # Runaway tool calls
    max_tc = data.get("max_tool_calls", 0)
    if max_tc > thresholds.max_tool_calls_per_session:
        signals.append(Signal.now(
            source="session_db",
            tier=AlertTier.YELLOW,
            message=f"Session with {max_tc} tool calls (threshold: {thresholds.max_tool_calls_per_session})",
        ))

    # Token spike
    total_tok = data.get("total_tokens", 0)
    if total_tok > thresholds.max_tokens_per_window:
        signals.append(Signal.now(
            source="session_db",
            tier=AlertTier.ORANGE,
            message=f"Token spike: {total_tok:,} tokens in {thresholds.window_hours}h window (threshold: {thresholds.max_tokens_per_window:,})",
        ))

    # Hung session
    longest = data.get("longest_active_hours", 0.0)
    if longest > thresholds.max_session_duration_hours:
        signals.append(Signal.now(
            source="session_db",
            tier=AlertTier.YELLOW,
            message=f"Active session running {longest:.1f}h (threshold: {thresholds.max_session_duration_hours}h)",
        ))

    return signals


def check_token_budget(
    hostname: str,
    user: str,
    hermes_db_path: str,
    budget: TokenBudgetConfig,
) -> list[Signal]:
    """Check cumulative token usage against rolling-window budget."""
    window_secs = budget.window_hours * 3600

    sql = (
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) as total "
        f"FROM sessions WHERE started_at > unixepoch() - {window_secs};"
    )

    data = _query_db(hostname, user, hermes_db_path, sql)
    if data is None:
        return [Signal.now(
            source="token_budget",
            tier=AlertTier.YELLOW,
            message="Failed to query hermes token budget",
        )]

    # sqlite3 without -json returns just the value for a single scalar
    total = data if isinstance(data, int) else data.get("total", 0) if isinstance(data, dict) else 0

    if total >= budget.critical_tokens:
        return [Signal.now(
            source="token_budget",
            tier=AlertTier.ORANGE,
            message=f"Token budget critical: {total:,} tokens in {budget.window_hours}h (limit: {budget.critical_tokens:,})",
        )]

    if total >= budget.warn_tokens:
        return [Signal.now(
            source="token_budget",
            tier=AlertTier.YELLOW,
            message=f"Token budget warning: {total:,} tokens in {budget.window_hours}h (limit: {budget.warn_tokens:,})",
        )]

    return []
