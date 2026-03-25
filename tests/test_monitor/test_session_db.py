"""Tests for overseer.monitor.session_db."""

from __future__ import annotations

import json
from unittest.mock import patch

from overseer.config import SessionThresholds, TokenBudgetConfig
from overseer.monitor.session_db import check_session_activity, check_token_budget
from overseer.types import AlertTier, Err, Ok

_HOST = "hermes-test"
_USER = "hermes"
_DB = "/home/hermes/.hermes/state.db"


def _mock_ssh(data: dict | int | str) -> Ok:
    """Build a mock SSH return value that simulates sqlite3 JSON output."""
    if isinstance(data, (dict, list)):
        return Ok(json.dumps(data))
    return Ok(str(data))


# ---------------------------------------------------------------------------
# check_session_activity
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLDS = SessionThresholds()


def test_session_activity_healthy() -> None:
    data = {
        "recent_sessions": 5,
        "max_tool_calls": 50,
        "total_tokens": 500_000,
        "longest_active_hours": 1.5,
    }
    with patch("overseer.monitor.session_db.run_ssh_command", return_value=_mock_ssh(data)):
        signals = check_session_activity(_HOST, _USER, _DB, _DEFAULT_THRESHOLDS)
    assert signals == []


def test_session_activity_inactivity() -> None:
    data = {
        "recent_sessions": 0,
        "max_tool_calls": 0,
        "total_tokens": 0,
        "longest_active_hours": 0,
    }
    with patch("overseer.monitor.session_db.run_ssh_command", return_value=_mock_ssh(data)):
        signals = check_session_activity(_HOST, _USER, _DB, _DEFAULT_THRESHOLDS)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "No sessions" in signals[0].message


def test_session_activity_runaway_tool_calls() -> None:
    data = {
        "recent_sessions": 3,
        "max_tool_calls": 500,
        "total_tokens": 100_000,
        "longest_active_hours": 0,
    }
    with patch("overseer.monitor.session_db.run_ssh_command", return_value=_mock_ssh(data)):
        signals = check_session_activity(_HOST, _USER, _DB, _DEFAULT_THRESHOLDS)
    assert any(s.tier == AlertTier.YELLOW and "tool calls" in s.message for s in signals)


def test_session_activity_token_spike() -> None:
    data = {
        "recent_sessions": 3,
        "max_tool_calls": 50,
        "total_tokens": 5_000_000,
        "longest_active_hours": 0,
    }
    with patch("overseer.monitor.session_db.run_ssh_command", return_value=_mock_ssh(data)):
        signals = check_session_activity(_HOST, _USER, _DB, _DEFAULT_THRESHOLDS)
    assert any(s.tier == AlertTier.ORANGE and "Token spike" in s.message for s in signals)


def test_session_activity_hung_session() -> None:
    data = {
        "recent_sessions": 3,
        "max_tool_calls": 10,
        "total_tokens": 100_000,
        "longest_active_hours": 12.0,
    }
    with patch("overseer.monitor.session_db.run_ssh_command", return_value=_mock_ssh(data)):
        signals = check_session_activity(_HOST, _USER, _DB, _DEFAULT_THRESHOLDS)
    assert any(s.tier == AlertTier.YELLOW and "Active session" in s.message for s in signals)


def test_session_activity_ssh_failure() -> None:
    with patch(
        "overseer.monitor.session_db.run_ssh_command",
        return_value=Err("connection refused", source="ssh"),
    ):
        signals = check_session_activity(_HOST, _USER, _DB, _DEFAULT_THRESHOLDS)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "Failed to query" in signals[0].message


def test_session_activity_malformed_json() -> None:
    with patch(
        "overseer.monitor.session_db.run_ssh_command",
        return_value=Ok("not json at all"),
    ):
        signals = check_session_activity(_HOST, _USER, _DB, _DEFAULT_THRESHOLDS)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW


# ---------------------------------------------------------------------------
# check_token_budget
# ---------------------------------------------------------------------------

_DEFAULT_BUDGET = TokenBudgetConfig()


def test_token_budget_under_budget() -> None:
    with patch(
        "overseer.monitor.session_db.run_ssh_command",
        return_value=Ok("500000"),
    ):
        signals = check_token_budget(_HOST, _USER, _DB, _DEFAULT_BUDGET)
    assert signals == []


def test_token_budget_warn() -> None:
    with patch(
        "overseer.monitor.session_db.run_ssh_command",
        return_value=Ok("1500000"),
    ):
        signals = check_token_budget(_HOST, _USER, _DB, _DEFAULT_BUDGET)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "warning" in signals[0].message.lower()


def test_token_budget_critical() -> None:
    with patch(
        "overseer.monitor.session_db.run_ssh_command",
        return_value=Ok("4000000"),
    ):
        signals = check_token_budget(_HOST, _USER, _DB, _DEFAULT_BUDGET)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.ORANGE
    assert "critical" in signals[0].message.lower()


def test_token_budget_ssh_failure() -> None:
    with patch(
        "overseer.monitor.session_db.run_ssh_command",
        return_value=Err("timeout", source="ssh"),
    ):
        signals = check_token_budget(_HOST, _USER, _DB, _DEFAULT_BUDGET)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW


def test_token_budget_empty_db() -> None:
    with patch(
        "overseer.monitor.session_db.run_ssh_command",
        return_value=Ok("0"),
    ):
        signals = check_token_budget(_HOST, _USER, _DB, _DEFAULT_BUDGET)
    assert signals == []
