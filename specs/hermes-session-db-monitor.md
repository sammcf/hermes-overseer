# Spec: Hermes Session DB Integration

## Context

Hermes v0.3.0 stores all session telemetry in `~/.hermes/state.db` (SQLite, WAL mode).
This replaces the need for our `check_rolling_window_usage` stub in `monitor/cost.py`
and gives us direct observability into session activity, token consumption, and model
usage — all queryable via SSH + sqlite3 on the VPS.

The snapshot pipeline already WAL-checkpoints and archives state.db (snapshot.py lines
81-89). On red-alert shutdown, the last snapshot contains a forensically complete copy
of the DB. This spec covers both **live monitoring** (poll-cycle queries) and
**post-incident forensic analysis** (local DB introspection after recovery).

### Hermes state.db schema (v4)

```sql
sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,        -- 'cli', 'telegram', 'discord', etc.
    user_id TEXT,
    model TEXT,
    model_config TEXT,           -- JSON: max_iterations, reasoning, max_tokens
    system_prompt TEXT,
    parent_session_id TEXT,      -- compression chain link
    started_at REAL NOT NULL,    -- epoch
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    title TEXT
)

messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,           -- 'user', 'assistant', 'tool', 'system'
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,             -- JSON array
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT
)

messages_fts (FTS5 virtual table on messages.content)
```

---

## Part 1: Live Monitoring

### New module: `src/overseer/monitor/session_db.py`

Two pure functions following the existing monitor pattern (return `list[Signal]`,
never crash — callers wrap in `_guard()`).

Both use a single SSH round-trip each, executing a sqlite3 query that returns JSON.
WAL mode means our reads don't block hermes writes.

#### 1. `check_session_activity`

Detects anomalous session patterns.

| Condition | Tier | Rationale |
|-----------|------|-----------|
| No sessions in last N hours | YELLOW | Hermes may be down or DB not writing |
| Session with >X tool calls in single run | YELLOW | Runaway agent loop indicator |
| Session with >Y total tokens in window | ORANGE | Cost spike / unexpected heavy usage |
| Active session running >Z hours | YELLOW | Possible hung session |
| DB unreachable or corrupt | YELLOW | Infra problem |

```python
def check_session_activity(
    hostname: str,
    user: str,
    hermes_db_path: str,       # e.g. "/home/hermes/.hermes/state.db"
    thresholds: SessionThresholds,
) -> list[Signal]:
```

**Query** (single round-trip, JSON output):

```sql
SELECT json_object(
    'recent_sessions', (
        SELECT COUNT(*) FROM sessions
        WHERE started_at > unixepoch() - ?
    ),
    'max_tool_calls', (
        SELECT COALESCE(MAX(tool_call_count), 0) FROM sessions
        WHERE started_at > unixepoch() - ?
    ),
    'total_tokens', (
        SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM sessions
        WHERE started_at > unixepoch() - ?
    ),
    'longest_active_hours', (
        SELECT COALESCE(MAX((unixepoch() - started_at) / 3600.0), 0)
        FROM sessions WHERE ended_at IS NULL
    ),
    'models_used', (
        SELECT json_group_array(DISTINCT model) FROM sessions
        WHERE started_at > unixepoch() - ? AND model IS NOT NULL
    )
);
```

Executed via `ssh user@host "sqlite3 /path/state.db '<query>'"` with window_seconds
baked into the query string (no parameterised binds over SSH — values are config-driven
integers, not user input, so no injection risk).

#### 2. `check_token_budget`

Replaces the `check_rolling_window_usage` stub. Monitors cumulative token spend
across a rolling window, broken down by model.

| Condition | Tier | Rationale |
|-----------|------|-----------|
| Rolling-window tokens > warn threshold | YELLOW | Approaching budget |
| Rolling-window tokens > critical threshold | ORANGE | Budget exceeded |

```python
def check_token_budget(
    hostname: str,
    user: str,
    hermes_db_path: str,
    budget: TokenBudgetConfig,
) -> list[Signal]:
```

**Query:**

```sql
SELECT model,
       SUM(input_tokens) as input_tok,
       SUM(output_tokens) as output_tok
FROM sessions
WHERE started_at > unixepoch() - ?
GROUP BY model;
```

Simple aggregate token count against threshold. Cost estimation (USD) is a nice-to-have
but not needed for the alerting logic — token counts are the enforcement boundary.

### Config additions

```python
class SessionThresholds(BaseModel, frozen=True):
    """Thresholds for hermes session activity monitoring."""
    window_hours: int = 6
    inactivity_alert_hours: int = 24
    max_tool_calls_per_session: int = 200
    max_tokens_per_window: int = 2_000_000
    max_session_duration_hours: float = 8.0

class TokenBudgetConfig(BaseModel, frozen=True):
    """Rolling-window token budget enforcement."""
    window_hours: int = 6
    warn_tokens: int = 1_000_000
    critical_tokens: int = 3_000_000

class MonitorConfig(BaseModel, frozen=True):
    # ... existing fields ...
    session_thresholds: SessionThresholds = SessionThresholds()
    token_budget: TokenBudgetConfig = TokenBudgetConfig()
```

### Pipeline integration

In `run_poll_cycle` (pipeline.py), add after config drift (#5):

```python
# 6. Hermes session DB — activity and token budget
hermes_db = f"{config.vps.hermes_home}/state.db"
signals.extend(_guard("session_db", check_session_activity,
    hostname=config.vps.tailscale_hostname,
    user=config.vps.ssh_user,
    hermes_db_path=hermes_db,
    thresholds=config.monitor.session_thresholds,
))
signals.extend(_guard("token_budget", check_token_budget,
    hostname=config.vps.tailscale_hostname,
    user=config.vps.ssh_user,
    hermes_db_path=hermes_db,
    budget=config.monitor.token_budget,
))
```

Note: `hermes_home` is `/home/hermes/.hermes` and state.db lives directly inside it,
so the path is `{hermes_home}/state.db` — no extra `.hermes` nesting.

---

## Part 2: Forensic Analysis

On red-alert shutdown, the response pipeline runs `take_backup` before `rebuild`.
The snapshot archive contains a WAL-checkpointed copy of state.db. For forensics,
we need to extract and analyse it locally.

### New module: `src/overseer/forensics/session_db.py`

Pure functions that operate on a local SQLite file (no SSH). Used interactively
or by a post-incident analysis CLI command.

#### `extract_db_from_snapshot`

```python
def extract_db_from_snapshot(
    archive_path: str,
    extract_dir: str,
) -> Result[str]:
    """Extract state.db from a snapshot archive. Returns path to the extracted DB."""
```

Runs `tar xzf <archive> -C <dir> .hermes/state.db` to pull just the DB file.

#### `generate_incident_report`

```python
def generate_incident_report(
    db_path: str,
    window_hours: float = 24.0,
) -> IncidentReport:
```

Returns a structured report dataclass:

```python
@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    source: str
    model: str
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
    tool_name: str
    timestamp: datetime
    args_snippet: str        # first 200 chars of tool_calls JSON
    result_snippet: str      # first 200 chars of content (for role=tool)

@dataclass(frozen=True)
class IncidentReport:
    db_path: str
    window_start: datetime
    window_end: datetime
    sessions: list[SessionSummary]
    tool_calls: list[ToolCallDetail]     # all tool invocations in window
    total_tokens: int
    models_used: list[str]
    anomalies: list[str]                 # human-readable anomaly descriptions
```

**Queries** (all local, no SSH):

1. **Session timeline:** All sessions in window, ordered by started_at.
2. **Tool call audit trail:** Join messages (role='tool' or tool_calls IS NOT NULL)
   with sessions for the window. This gives us every tool invocation with timestamps —
   critical for understanding what the agent was *doing* when things went wrong.
3. **Content search:** FTS5 query for suspicious patterns — can parameterise with
   keywords relevant to the incident (e.g. "ssh", "curl", "eval", specific hostnames).
4. **Model switching:** Detect mid-window model changes (sessions with different
   model values) — could indicate config manipulation.

#### `search_messages`

```python
def search_messages(
    db_path: str,
    query: str,
    window_hours: float = 24.0,
) -> list[dict]:
    """FTS5 search across message content. Returns matching messages with session context."""
```

Uses the existing `messages_fts` virtual table. Useful for grep-style forensics
("did the agent ever mention X?").

### CLI integration

Add a subcommand to the overseer CLI:

```
overseer forensics [--snapshot <path>] [--window <hours>] [--search <query>]
```

- `--snapshot`: path to archive (default: latest in backup_dir)
- `--window`: analysis window in hours (default: 24)
- `--search`: FTS5 query for message content search

Outputs the incident report as structured text to stdout. No external dependencies
beyond sqlite3 (Python stdlib).

---

## Part 3: Existing Monitor Calibration

### What changes

| Current monitor | State.db impact | Action |
|----------------|----------------|--------|
| `cost.check_rolling_window_usage` | Directly replaced by `check_token_budget` | Deprecate stub |
| `cost.check_openrouter_balance` | Orthogonal (wallet balance ≠ session tokens) | Keep as-is |
| `connections.check_connections` | Concurrent tool execution means hermes may open parallel outbound connections | Raise `sustained_unknown_threshold` or add process-name awareness for `python3` |
| `config_drift.check_config_drift` | No change — checks YAML config, not runtime state | Keep as-is |
| `files.evaluate_file_changes` | `cron/jobs.json` → now SQLite; `state.db` changes are *expected* and should not trigger file-diff alerts | Add `state.db`, `state.db-wal`, `state.db-shm` to exclusions |
| `metrics.check_bl_metrics` | No change | Keep as-is |

### File monitoring exclusions

The watched files system uses rsync + diff. state.db will change every poll cycle
(because hermes is actively using it). We need to ensure state.db and its WAL files
are excluded from file-change diffing — they're expected to change constantly and
the session_db monitor handles anomaly detection at a semantic level instead.

Currently `watched_files` only specifies files to *watch*, not exclude, and state.db
isn't in any watch list, so this should already be fine. But worth verifying that
rsync doesn't pull state.db into the diff directory (it shouldn't — rsync_pull uses
`--relative` with explicit path lists from `watched_files` config).

### Cron observability

Hermes v0.3.0 moved cron jobs from `cron/jobs.json` (currently in `yellow_on_any_diff`)
to SQLite. The file-based watch on `cron/jobs.json` is now stale — the file may not
even exist. Options:

1. Remove `cron/jobs.json` from watched files (it's dead).
2. Add cron job monitoring via state.db query (future enhancement — cron jobs table
   isn't in the core state.db schema we examined; may be separate).

---

## Test plan

### `tests/test_monitor/test_session_db.py`

1. **Healthy state → no signals.** Mock SSH returning normal metrics within thresholds.
2. **Inactivity → YELLOW.** Mock SSH returning `recent_sessions: 0`.
3. **Runaway tool calls → YELLOW.** Mock SSH returning `max_tool_calls: 500`.
4. **Token spike → ORANGE.** Mock SSH returning `total_tokens: 5_000_000`.
5. **Hung session → YELLOW.** Mock SSH returning `longest_active_hours: 12.0`.
6. **SSH failure → YELLOW.** Mock SSH returning `Err`.
7. **Malformed JSON → YELLOW.** Mock SSH returning garbage.

### `tests/test_monitor/test_token_budget.py`

1. **Under budget → no signals.**
2. **Warn threshold → YELLOW.**
3. **Critical threshold → ORANGE.**
4. **SSH failure → YELLOW.**
5. **Empty DB (no sessions) → no signals.**

### `tests/test_forensics/test_session_db.py`

1. **Extract DB from real archive.** Create a minimal tar.gz with a test state.db.
2. **Incident report from populated DB.** Insert test sessions + messages, verify report.
3. **FTS5 search.** Insert messages, verify search returns correct matches.
4. **Empty DB.** Verify graceful empty report, not crash.
5. **Tool call audit trail.** Verify tool_calls join produces correct chronological output.

---

## Dependencies

- `sqlite3` CLI must be installed on VPS (for SSH queries). Currently flagged as
  missing in implementation notes — **must resolve before deployment**.
- Python `sqlite3` stdlib module (for forensics — already available everywhere).
- SSH connectivity (established, tested in existing monitors).

## Migration notes

- Delete stale patch: `patches/hermes-agent-write-through.patch` — **done**.
- `check_rolling_window_usage` → deprecate with pointer to `session_db.check_token_budget`.
- `check_openrouter_balance` → keep, orthogonal concern.
- `cron/jobs.json` watched file → remove from config (cron now in SQLite).
- No VPS-side config changes needed — read-only DB access.

## Resolved questions

- **DB path:** `{hermes_home}/state.db` = `/home/hermes/.hermes/state.db`. Absolute,
  no `~` expansion needed.
- **Query frequency:** One SSH + sqlite3 round-trip per check, two checks per poll
  cycle. WAL mode read-only, negligible impact. Fine at 120s poll interval.
- **Pricing table:** Not needed for alerting. Token counts are the enforcement
  boundary. Cost estimation is a nice-to-have for incident reports only.
