"""Forensic analysis of hermes state.db from snapshot archives.

All functions operate on a local SQLite file — no SSH needed. Used for
post-incident analysis after red-alert shutdown or on-demand investigation.
"""

from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from overseer.types import Err, Ok, Result


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    source: str
    model: str | None
    started_at: datetime
    ended_at: datetime | None
    message_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    title: str | None


@dataclass(frozen=True)
class ToolCallDetail:
    session_id: str
    tool_name: str | None
    timestamp: datetime
    args_snippet: str
    result_snippet: str


@dataclass(frozen=True)
class IncidentReport:
    db_path: str
    window_start: datetime
    window_end: datetime
    sessions: list[SessionSummary]
    tool_calls: list[ToolCallDetail]
    total_tokens: int
    models_used: list[str]
    anomalies: list[str] = field(default_factory=list)


def extract_db_from_snapshot(
    archive_path: str,
    extract_dir: str | None = None,
) -> Result[str]:
    """Extract state.db from a snapshot archive. Returns path to extracted DB."""
    if extract_dir is None:
        extract_dir = tempfile.mkdtemp(prefix="overseer-forensics-")

    # Snapshot archives contain .hermes/state.db (relative to hermes user home)
    try:
        subprocess.run(
            [
                "tar", "xzf", archive_path,
                "-C", extract_dir,
                "--wildcards", "*/state.db",
                "--strip-components=0",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as e:
        return Err(f"tar extraction failed: {e.stderr.strip()}", source="forensics")
    except subprocess.TimeoutExpired:
        return Err("tar extraction timed out", source="forensics")

    # Find the extracted DB file
    import glob
    matches = glob.glob(f"{extract_dir}/**/state.db", recursive=True)
    if not matches:
        return Err("state.db not found in archive", source="forensics")

    return Ok(matches[0])


def _epoch_to_dt(epoch: float | None) -> datetime | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC)


def generate_incident_report(
    db_path: str,
    window_hours: float = 24.0,
) -> IncidentReport:
    """Generate a structured incident report from a local state.db."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    window_end = datetime.now(UTC)
    window_secs = window_hours * 3600
    # Use the DB's own timestamps for window calculation
    # If the DB is from a snapshot, "now" is when analysis runs — use max timestamp instead
    max_ts_row = conn.execute(
        "SELECT MAX(started_at) as ts FROM sessions"
    ).fetchone()
    if max_ts_row and max_ts_row["ts"]:
        reference_epoch = max_ts_row["ts"]
        window_end = _epoch_to_dt(reference_epoch) or window_end
    else:
        reference_epoch = window_end.timestamp()

    window_start_epoch = reference_epoch - window_secs
    window_start = _epoch_to_dt(window_start_epoch) or window_end

    # Session timeline
    session_rows = conn.execute(
        "SELECT * FROM sessions WHERE started_at > ? ORDER BY started_at",
        (window_start_epoch,),
    ).fetchall()

    sessions = [
        SessionSummary(
            session_id=r["id"],
            source=r["source"],
            model=r["model"],
            started_at=_epoch_to_dt(r["started_at"]) or window_start,
            ended_at=_epoch_to_dt(r["ended_at"]),
            message_count=r["message_count"] or 0,
            tool_call_count=r["tool_call_count"] or 0,
            input_tokens=r["input_tokens"] or 0,
            output_tokens=r["output_tokens"] or 0,
            title=r["title"],
        )
        for r in session_rows
    ]

    # Tool call audit trail
    tool_rows = conn.execute(
        """
        SELECT m.session_id, m.tool_name, m.timestamp,
               COALESCE(m.tool_calls, '') as tool_calls,
               COALESCE(m.content, '') as content
        FROM messages m
        JOIN sessions s ON m.session_id = s.id
        WHERE s.started_at > ?
          AND (m.role = 'tool' OR m.tool_calls IS NOT NULL)
        ORDER BY m.timestamp
        """,
        (window_start_epoch,),
    ).fetchall()

    tool_calls = [
        ToolCallDetail(
            session_id=r["session_id"],
            tool_name=r["tool_name"],
            timestamp=_epoch_to_dt(r["timestamp"]) or window_start,
            args_snippet=r["tool_calls"][:200] if r["tool_calls"] else "",
            result_snippet=r["content"][:200] if r["content"] else "",
        )
        for r in tool_rows
    ]

    total_tokens = sum(s.input_tokens + s.output_tokens for s in sessions)
    models_used = sorted({s.model for s in sessions if s.model})

    # Detect anomalies
    anomalies: list[str] = []
    for s in sessions:
        if s.tool_call_count > 200:
            anomalies.append(
                f"Session {s.session_id[:8]} had {s.tool_call_count} tool calls"
            )
        if s.ended_at is None and s.started_at:
            duration_h = (window_end - s.started_at).total_seconds() / 3600
            if duration_h > 8:
                anomalies.append(
                    f"Session {s.session_id[:8]} still active after {duration_h:.1f}h"
                )
    if len(models_used) > 1:
        anomalies.append(f"Multiple models used in window: {', '.join(models_used)}")

    conn.close()

    return IncidentReport(
        db_path=db_path,
        window_start=window_start,
        window_end=window_end,
        sessions=sessions,
        tool_calls=tool_calls,
        total_tokens=total_tokens,
        models_used=models_used,
        anomalies=anomalies,
    )


def search_messages(
    db_path: str,
    query: str,
    window_hours: float = 24.0,
) -> list[dict]:
    """FTS5 search across message content. Returns matching messages with session context."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Determine reference time from DB
    max_ts_row = conn.execute(
        "SELECT MAX(started_at) as ts FROM sessions"
    ).fetchone()
    if max_ts_row and max_ts_row["ts"]:
        reference_epoch = max_ts_row["ts"]
    else:
        reference_epoch = datetime.now(UTC).timestamp()

    window_start_epoch = reference_epoch - window_hours * 3600

    # Check if FTS5 table exists
    fts_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()

    if fts_exists:
        rows = conn.execute(
            """
            SELECT m.session_id, m.role, m.content, m.timestamp, m.tool_name,
                   s.source, s.model
            FROM messages_fts fts
            JOIN messages m ON m.rowid = fts.rowid
            JOIN sessions s ON m.session_id = s.id
            WHERE messages_fts MATCH ?
              AND s.started_at > ?
            ORDER BY m.timestamp
            """,
            (query, window_start_epoch),
        ).fetchall()
    else:
        # Fallback to LIKE search if no FTS5
        rows = conn.execute(
            """
            SELECT m.session_id, m.role, m.content, m.timestamp, m.tool_name,
                   s.source, s.model
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content LIKE ?
              AND s.started_at > ?
            ORDER BY m.timestamp
            """,
            (f"%{query}%", window_start_epoch),
        ).fetchall()

    results = [
        {
            "session_id": r["session_id"],
            "role": r["role"],
            "content": (r["content"] or "")[:500],
            "timestamp": _epoch_to_dt(r["timestamp"]).isoformat() if r["timestamp"] else None,
            "tool_name": r["tool_name"],
            "source": r["source"],
            "model": r["model"],
        }
        for r in rows
    ]

    conn.close()
    return results
