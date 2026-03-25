"""Tests for overseer.forensics.session_db."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from overseer.forensics.session_db import (
    IncidentReport,
    generate_incident_report,
    search_messages,
)


@pytest.fixture
def test_db(tmp_path: Path) -> str:
    """Create a minimal hermes-compatible state.db with test data."""
    db_path = str(tmp_path / "state.db")
    conn = sqlite3.connect(db_path)

    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            user_id TEXT,
            model TEXT,
            model_config TEXT,
            system_prompt TEXT,
            parent_session_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            title TEXT
        );

        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            token_count INTEGER,
            finish_reason TEXT
        );
    """)

    now = time.time()

    # Insert test sessions
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess-1", "telegram", "sam", "claude-sonnet-4-20250514",
         now - 3600, now - 1800, "completed",
         20, 45, 50000, 30000, "Normal session"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, NULL, ?, ?, ?, ?, ?)",
        ("sess-2", "cli", "sam", "claude-sonnet-4-20250514",
         now - 600, 5, 250, 100000, 80000, "Runaway session"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess-old", "telegram", "sam", "gpt-4o",
         now - 100000, now - 99000, "completed",
         10, 5, 20000, 10000, "Old session"),
    )

    # Insert test messages
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, tool_name) "
        "VALUES (?, ?, ?, ?, NULL)",
        ("sess-1", "user", "What is the weather?", now - 3500),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, tool_name, tool_calls) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("sess-1", "assistant", None, now - 3400, None,
         '[{"name": "get_weather", "args": {"city": "Sydney"}}]'),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, tool_name) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess-1", "tool", "Sunny, 25C", now - 3300, "get_weather"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp, tool_name) "
        "VALUES (?, ?, ?, ?, NULL)",
        ("sess-2", "user", "Run the security audit now", now - 590),
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def empty_db(tmp_path: Path) -> str:
    """Create an empty state.db with the schema but no data."""
    db_path = str(tmp_path / "empty_state.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            user_id TEXT,
            model TEXT,
            model_config TEXT,
            system_prompt TEXT,
            parent_session_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            title TEXT
        );

        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            token_count INTEGER,
            finish_reason TEXT
        );
    """)
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# generate_incident_report
# ---------------------------------------------------------------------------


def test_incident_report_populated_db(test_db: str) -> None:
    report = generate_incident_report(test_db, window_hours=24.0)
    assert isinstance(report, IncidentReport)
    assert report.db_path == test_db
    # Should include sess-1 and sess-2 (within 24h), but not sess-old (>24h old)
    session_ids = {s.session_id for s in report.sessions}
    assert "sess-1" in session_ids
    assert "sess-2" in session_ids
    assert "sess-old" not in session_ids


def test_incident_report_total_tokens(test_db: str) -> None:
    report = generate_incident_report(test_db, window_hours=24.0)
    # sess-1: 50000 + 30000 = 80000; sess-2: 100000 + 80000 = 180000
    assert report.total_tokens == 260_000


def test_incident_report_models(test_db: str) -> None:
    report = generate_incident_report(test_db, window_hours=24.0)
    assert "claude-sonnet-4-20250514" in report.models_used


def test_incident_report_tool_calls(test_db: str) -> None:
    report = generate_incident_report(test_db, window_hours=24.0)
    # Should find the tool call and tool result from sess-1
    assert len(report.tool_calls) >= 1
    tool_names = [tc.tool_name for tc in report.tool_calls]
    assert "get_weather" in tool_names


def test_incident_report_empty_db(empty_db: str) -> None:
    report = generate_incident_report(empty_db, window_hours=24.0)
    assert report.sessions == []
    assert report.tool_calls == []
    assert report.total_tokens == 0


def test_incident_report_anomaly_active_session(test_db: str) -> None:
    """sess-2 has no ended_at — it should not trigger anomaly unless >8h."""
    report = generate_incident_report(test_db, window_hours=24.0)
    # sess-2 started 10 minutes ago, so <8h — no anomaly
    active_anomalies = [a for a in report.anomalies if "still active" in a]
    assert len(active_anomalies) == 0


def test_incident_report_wide_window_includes_old(test_db: str) -> None:
    report = generate_incident_report(test_db, window_hours=48.0)
    session_ids = {s.session_id for s in report.sessions}
    assert "sess-old" in session_ids


# ---------------------------------------------------------------------------
# search_messages
# ---------------------------------------------------------------------------


def test_search_messages_finds_content(test_db: str) -> None:
    results = search_messages(test_db, "weather", window_hours=24.0)
    assert len(results) >= 1
    assert any("weather" in r["content"].lower() for r in results)


def test_search_messages_no_match(test_db: str) -> None:
    results = search_messages(test_db, "nonexistent_query_xyz", window_hours=24.0)
    assert results == []


def test_search_messages_tool_result(test_db: str) -> None:
    results = search_messages(test_db, "Sunny", window_hours=24.0)
    assert len(results) >= 1
    assert results[0]["tool_name"] == "get_weather"


def test_search_messages_empty_db(empty_db: str) -> None:
    results = search_messages(empty_db, "anything", window_hours=24.0)
    assert results == []
