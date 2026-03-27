# Mailbox Protocol Plugin — Architecture Plan

## Status

**Phase:** Design  
**Created:** 2026-03-27  
**Updated:** 2026-03-27

---

## 1. Overview

The mailbox plugin is a hermes-agent plugin that implements a general-purpose, LLM-native communication and coordination protocol. It provides:

- **Addressable mailboxes** — content-addressable message storage with channel-based routing
- **Append-only heap** — durable, queryable content store with provenance tracking
- **Effect ledger** — idempotent side-effect execution with full audit trail
- **Frame stack** — hierarchical execution contexts (channel → subscription → work-frame)
- **Transport adapter interface** — pluggable inbound/outbound for any messaging platform
- **REST API surface** — mailbox accessible to external agents, humans, and tools via HTTP

The daily briefing system is the first use case and validation harness. The protocol is designed to be general-purpose, supporting arbitrary adapters (scheduled digests, agent-to-agent coordination, system health notifications, etc.).

### Design Principles

1. **Protocol purity** — interfaces define types, contracts, and behavior. Nothing in the protocol contract assumes hermes internals.
2. **Hermes-native implementation** — protocol interfaces are implemented using hermes primitives: SQLite persistence, LLM dispatch, cron scheduling, gateway transport.
3. **Deterministic plumbing, non-deterministic content** — timing, routing, queue semantics, and error handling are deterministic; content synthesis is sandboxed in LLM calls.
4. **Append-only everywhere** — no mutation, only new entries. All state changes are appends. Mutable interaction paradigms (cursor tracking, delivery acknowledgment) are computed over immutable logs.
5. **Extraction over integration** — protocol is designed so it could run outside hermes. The hermes implementation exploits platform capabilities but doesn't depend on them architecturally.

---

## 2. Layer Model

```
Layer 1: hermes-agent (platform)
  ├── /v1/chat/completions — LLM dispatch
  ├── /api/jobs            — cron job management
  ├── /api/mailbox         — REST API surface (plugin-provided)
  ├── gateway              — transport adapters (Telegram, Signal, etc.)
  ├── state.db             — session and core hermes state
  └── plugin system         — tool registration, lifecycle hooks

Layer 2: mailbox plugin (core — hermes-plugin-distributable)
  ├── effect_ledger.py     — idempotent effect execution with dedupe
  ├── frame_store.py       — frame tree: channel / subscription / work frames
  ├── heap_store.py        — append-only, content-addressable content store
  ├── replay_engine.py     — checkpoint + replay for /retry and recovery
  ├── channel_router.py    — channel subscription management + routing
  ├── transport_adapter.py — interface for platform-specific send/receive
  ├── rest_api.py          — HTTP API surface for external access
  ├── incoming_hook.py     — gateway incoming message dispatcher
  └── plugin_tools.py      — hermes tool registrations

Layer 3: adapters (vault-based, user-editable)
  ├── daily-briefing/
  │   ├── morning_workflow.py
  │   ├── evening_workflow.py
  │   ├── vault_adapter.py      — read tasks.md, daily notes
  │   ├── reply_parser.py       — parse morning selection, evening sections
  │   ├── quality_scorer.py     — self-review + quality score
  │   └── templates/            — Jinja2 templates in vault
  ├── [future: scheduled-research-digest]
  ├── [future: agent-coordination-bus]
  └── [future: system-health-briefing]
```

---

## 3. Data Model

### 3.1 Storage

**Separate `mailbox.db`** — WAL-mode SQLite at `~/.hermes/mailbox.db`. Rationale:

- Isolation from `state.db`: protocol writes never contend with hermes core writes
- Unbounded heap growth doesn't affect core hermes backup size or query performance
- Schema migrations for the protocol don't touch hermes core
- Cross-host access (hermes on VPS, overseer on home server) uses REST API regardless of store location
- Cross-store concerns are solvable: unified search via a protocol tool that queries both stores; hermes tools surface mailbox data without directly accessing schema

### 3.2 Schema

```sql
-- Frame tree (mirrors queryable's frame stack)
CREATE TABLE frames (
    frame_id      TEXT PRIMARY KEY,
    parent_frame_id TEXT REFERENCES frames(frame_id),  -- NULL = root (channel frame)
    namespace     TEXT NOT NULL,   -- channel_id; "global" for system frames
    agent_id      TEXT NOT NULL,
    status        TEXT NOT NULL,  -- "open" | "committed" | "discarded" | "compensated"
    opened_at_utc INTEGER NOT NULL,
    closed_at_utc INTEGER,
    metadata_json TEXT             -- adapter-specific JSON
);

-- Append-only heap (content-addressable)
CREATE TABLE heap_entries (
    entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type    TEXT NOT NULL,  -- "message" | "vault_read" | "synthesis" | "effect_result"
    namespace     TEXT NOT NULL,   -- channel_id; "global" for system entries
    content_hash  TEXT NOT NULL,  -- SHA-256 of content_json for dedupe + integrity
    content_json  TEXT NOT NULL,  -- JSON payload
    frame_id      TEXT REFERENCES frames(frame_id),
    provenance_json TEXT,         -- JSON array of parent entry_ids
    created_at_utc INTEGER NOT NULL,
    UNIQUE(namespace, entry_type, content_hash)  -- idempotent: same content = same entry
);

-- Effect ledger (from queryable Effect.hs)
CREATE TABLE effects (
    effect_key    TEXT PRIMARY KEY,   -- "{frame_id}:{effect_id}" — idempotency key
    frame_id      TEXT REFERENCES frames(frame_id),
    effect_type   TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    result_json  TEXT,
    status        TEXT NOT NULL,  -- "pending" | "applied" | "failed" | "deduped" | "conflict"
    created_at_utc INTEGER NOT NULL,
    updated_at_utc INTEGER NOT NULL
);

-- Checkpoints (frame snapshots for replay)
CREATE TABLE checkpoints (
    checkpoint_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id      TEXT REFERENCES frames(frame_id),
    through_entry_id INTEGER,
    snapshot_json TEXT NOT NULL,
    created_at_utc INTEGER NOT NULL
);

-- Subscription registry
CREATE TABLE subscriptions (
    subscription_id TEXT PRIMARY KEY,
    frame_id      TEXT REFERENCES frames(frame_id),  -- root (channel) frame
    adapter_name  TEXT NOT NULL,   -- e.g. "daily-briefing"
    cursor_seq    INTEGER NOT NULL DEFAULT 0,  -- last processed entry_id for this subscriber
    created_at_utc INTEGER NOT NULL,
    metadata_json TEXT
);

-- Indexes
CREATE INDEX idx_frames_namespace    ON frames(namespace, opened_at_utc DESC);
CREATE INDEX idx_frames_parent       ON frames(parent_frame_id);
CREATE INDEX idx_heap_namespace      ON heap_entries(namespace, created_at_utc DESC);
CREATE INDEX idx_heap_type           ON heap_entries(namespace, entry_type, created_at_utc DESC);
CREATE INDEX idx_heap_hash           ON heap_entries(content_hash);
CREATE INDEX idx_effects_frame      ON effects(frame_id, created_at_utc DESC);
CREATE INDEX idx_effects_status      ON effects(status, created_at_utc);
CREATE INDEX idx_checkpoints_frame   ON checkpoints(frame_id, checkpoint_id DESC);
CREATE INDEX idx_subscriptions_frame ON subscriptions(frame_id);

-- FTS5 virtual table for content search
CREATE VIRTUAL TABLE heap_fts USING fts5(
    content_json,
    namespace UNINDEXED,
    entry_type UNINDEXED,
    content=heap_entries,
    content_rowid=entry_id
);
```

### 3.3 Key Data Model Invariants

- **`content_hash` uniqueness**: `(namespace, entry_type, content_hash)` is unique. Writing the same content twice is idempotent — returns the existing `entry_id`. This is the content-addressable heap property.
- **`entry_id` as "heap address"**: Every heap entry has a stable integer ID. Any entry can be addressed by `(namespace, entry_id)`. This is the pointer value for provenance chains.
- **`provenance_json`**: JSON array of parent `entry_id`s. Walking the provenance chain backwards from any synthesis entry reconstructs the full source lineage.
- **`effect_key` namespacing**: `"{frame_id}:{effect_id}"` prevents collision between adapters. Daily-briefing's `enqueue_synthesis` and research-digest's `enqueue_digest` can share the same underlying `effects` table without conflict.
- **`namespace` partitioning**: All protocol data is namespaced by `channel_id` or `"global"`. Hermes core never sees protocol data; protocol never touches session data.

---

## 4. Core Primitives

### 4.1 Effect Ledger

Mirrors queryable's `Effect.hs` semantics. The core operation is:

```python
apply_effect(effect_key: str, effect_type: str, payload: dict) -> EffectOutcome
```

**Behavior:**
1. Check if `effect_key` exists in `effects` table
2. If exists and `status = "applied"`: return `Deduped(existing_result)`
3. If exists and `status = "failed"`: allow retry (return `Pending`)
4. If exists and `status = "pending"`: return `Conflict`
5. If not exists: insert with `status = "pending"`, execute, update `result_json`, set `status = "applied"`

**Outcomes:** `Applied(result) | Failed(error) | Deduped(result) | Conflict`

**Effect types** (defined by protocol, extended by adapters):
- `mailbox.enqueue` — add a job to the queue
- `mailbox.send_message` — send via transport adapter
- `vault.read` — read from vault filesystem
- `synthesize_morning` — LLM synthesis for morning briefing
- `synthesize_evening` — LLM synthesis for evening briefing
- `send_briefing` — deliver briefing via transport
- `score_quality` — self-review and quality scoring

### 4.2 Frame Store

Frame lifecycle mirrors queryable's frame stack semantics, adapted for the channel model:

```
Channel frame (root, namespace = channel_id, parent_frame_id = NULL)
└── Subscription frames (one per adapter subscribed to channel)
    └── Work frames (per-job execution contexts)
        ├── effect: vault_read(tasks.md) → heap:0x123
        ├── effect: vault_read(daily.md) → heap:0x456
        ├── effect: synthesize_morning  → heap:0x789
        │   └── provenance: [0x123, 0x456]
        └── effect: send_briefing       → heap:0xABC
```

**Frame states:**
- `open` — active, accepting new effects
- `committed` — closed normally; effects are final
- `discarded` — rolled back; effects marked compensated
- `compensated` — rollback completed; frame is closed

**Operations:**
- `push_frame(namespace, agent_id, metadata)` → creates work frame under current subscription frame
- `close_frame(frame_id, status)` → closes frame; if `status = "committed"`, marks effects as permanent
- `checkpoint_frame(frame_id)` → snapshots current state through last effect
- `replay_frame(frame_id)` → re-executes from checkpoint (used by `/retry`)

### 4.3 Heap Store

Append-only, content-addressable. All state lives here.

**Operations:**
- `write_entry(entry_type, namespace, content_json, frame_id, provenance)` → appends to heap, returns `entry_id`. Idempotent via `content_hash` dedupe.
- `read_entry(namespace, entry_id)` → returns heap entry by address
- `query_heap(namespace, entry_type, cursor, limit)` → paginated backward traversal (newest first)
- `search_heap(namespace, query, cursor, limit)` → FTS search across heap content
- `get_provenance(entry_id)` → returns chain of parent entry_ids

**Entry types:**
- `message` — incoming or outgoing message on a channel
- `vault_read` — result of a vault file read
- `synthesis` — LLM-generated content (briefing, digest, etc.)
- `effect_result` — output of a completed effect
- `checkpoint` — frame snapshot (stored in `checkpoints`, referenced by heap)

### 4.4 Channel Router

Channels are identified by string name (e.g. `#hermes-briefings`). The router manages:
- Channel creation and deletion
- Subscription registration (adapter → channel binding)
- Incoming message routing (which adapters receive a given message)
- Cursor tracking per subscription

**Routing question (open):** When a message arrives on a channel, should it be delivered to all subscribers (pub/sub fan-out) or to exactly one (dispatch)? The protocol should support both. Each `Subscription` record carries a `delivery_mode` field: `"fanout"` or `"exclusive"`.

---

## 5. Transport Adapter Interface

Abstract interface for platform-specific send/receive:

```python
class TransportAdapter(ABC):
    @abstractmethod
    def send(self, channel: str, message: Message) -> SendResult:
        """Send a message to a channel. Returns delivery confirmation."""

    @abstractmethod
    def subscribe(self, channel: str, handler: Callable[[Message], None]):
        """Register a handler for incoming messages on a channel."""

    @abstractmethod
    def unsubscribe(self, channel: str):
        """Stop receiving messages on a channel."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Identifier: 'telegram', 'signal', 'email', etc."""
```

**Telegram adapter** (hermes gateway already has Telegram support):
- Send: via hermes `send_message` tool
- Subscribe: gateway incoming message hook with `#channel` routing
- Platform name: `telegram`

**Future adapters:** Signal, email (IMAP/SMTP), REST webhook, internal (cross-agent)

---

## 6. Gateway Integration

### 6.1 Incoming Message Hook

Hermes gateway dispatches incoming messages to registered platform handlers. The mailbox plugin hooks into this via the plugin system:

```python
def register_incoming_handler(platform: str, channel: str, handler: Callable):
    """Register handler for incoming messages on a specific channel/platform."""

def unregister_incoming_handler(platform: str, channel: str):
    """Remove a handler."""
```

**Routing question (open):** How does the gateway know which plugin handles which channel?
- **Option A:** Plugin registers `channel → handler` mappings at startup. Gateway maintains a dispatch table. Fan-out to all matching handlers.
- **Option B:** Gateway tags messages with `#channel` prefix in the payload. Protocol plugin receives all messages and filters by channel internally.
- **Option C:** Protocol plugin registers as a middleware that sees all gateway traffic and routes by channel tag in message body.

**Recommendation:** Option A — plugin lifecycle registration is explicit, the dispatch table is auditable, and routing is fast (dict lookup).

### 6.2 Slash Commands

Standard hermes slash command registration via the plugin system (`/briefing-reply`, `/retry`, etc.):

```python
def register_slash_command(name: str, handler: Callable[[str, str], str]):
    """Register a slash command. name='retry', handler receives (args, user_id)."""
```

Commands handled by daily-briefing adapter:
- `/briefing-reply <text>` — process user's reply to a briefing
- `/retry <feedback>` — re-run the last synthesis with feedback appended to prompt
- `/briefing-status` — show queue state, last run timestamps, quality scores

### 6.3 Cron Integration

The plugin uses hermes cron for scheduling. Jobs are defined via `hermes cron add`:

```yaml
# Morning briefing — every weekday at 08:00
08:00 * * 1-5:
  adapter: daily-briefing
  action: morning
  target: #hermes-briefings
  adapter_config:
    model: claude-sonnet-4.7

# Evening briefing — every weekday at 18:30
18:30 * * 1-5:
  adapter: daily-briefing
  action: evening
  target: #hermes-briefings
```

The protocol plugin provides a `cron_adapter` toolset that hermes cron calls into. Job definitions live in vault alongside the adapter templates.

---

## 7. REST API Surface

Exposed via hermes's API server adapter at `/api/mailbox/...`. Auth: reuse hermes API key mechanism.

### 7.1 Endpoints

```
GET    /api/mailbox/channels                    — list all channels
POST   /api/mailbox/channels                    — create a channel
GET    /api/mailbox/channels/{channel}/entries  — list entries (cursor, limit, type filter)
POST   /api/mailbox/channels/{channel}/entries  — post a message to channel
GET    /api/mailbox/channels/{channel}/entries/{id}  — get single entry
GET    /api/mailbox/channels/{channel}/provenance/{id}  — get provenance chain
POST   /api/mailbox/channels/{channel}/subscribe   — subscribe an external client
DELETE /api/mailbox/channels/{channel}/subscribe/{sub_id}  — unsubscribe
GET    /api/mailbox/search                      — FTS search across all namespaces
GET    /api/mailbox/frames/{frame_id}/checkpoint  — get checkpoint snapshot
POST   /api/mailbox/frames/{frame_id}/replay    — trigger replay from checkpoint
```

### 7.2 Auth Surface (open question)

**Option A:** Reuse hermes API server's own API key. External clients authenticate with the same key used for `/v1/chat/completions`. Simple, single key.

**Option A2:** Per-channel API keys. Each channel has its own key, generated at channel creation. Allows fine-grained revocation.

**Option B:** Separate mailbox API key with its own config path. Plugin generates a key at install time, stored in `~/.hermes/mailbox.api_key`.

**Option C:** OAuth-based external auth. Clients authenticate via a token exchange. More complex but supports fine-grained scopes.

**Recommendation:** Option A to start. The mailbox REST API is a hermes plugin; external access is gated by hermes's own auth. Upgrade to per-channel keys if multi-tenant access becomes a concern.

---

## 8. Daily Briefing Adapter

First and most thoroughly validated use case.

### 8.1 Morning Workflow

```
cron(08:00 Mon-Fri)
  → push_frame(channel=#hermes-briefings, adapter=daily-briefing, type=morning)
  → apply_effect("vault.read_tasks")
      → read vault/tasks.md, vault/daily-notes.md
      → write_entry(entry_type=vault_read, content_json={files, content})
  → apply_effect("synthesize_morning")
      → read template from vault/templates/daily-briefing/morning.md
      → fill Jinja2 holes with vault reads + date + quality context from last evening
      → LLM synthesis
      → write_entry(entry_type=synthesis, content_json={briefing_text, quality_score})
  → apply_effect("send_briefing")
      → read latest synthesis entry
      → transport.send(channel=#hermes-briefings, message=synthesis)
  → close_frame(status=committed)
```

### 8.2 Evening Workflow

```
cron(18:30 Mon-Fri)
  → push_frame(channel=#hermes-briefings, adapter=daily-briefing, type=evening)
  → apply_effect("vault.read_tasks")
      → read vault/tasks.md, vault/daily-notes.md, vault/evening-template.md
  → apply_effect("vault.read_morning_synthesis")
      → read today's morning synthesis entry
      → parse user's morning selections (replied tasks)
  → apply_effect("synthesize_evening")
      → read morning selection results
      → compare: what was planned vs. what was done
      → LLM synthesis
  → apply_effect("score_quality")
      → self-review: did the morning briefing accurately represent tasks?
      → quality score 1-5 with reasoning
      → write_entry(entry_type=effect_result, content_json={score, reasoning})
  → apply_effect("send_briefing")
      → transport.send(channel=#hermes-briefings, message=evening_briefing)
  → close_frame(status=committed)
```

### 8.3 User Reply Processing

When user replies to a morning briefing in Telegram:
1. Gateway dispatches to `#hermes-briefings` handler
2. Protocol plugin routes to daily-briefing adapter
3. Adapter parses selection (LLM-assisted if ambiguous)
4. Writes selection to heap under the morning work frame
5. Selection is available for evening synthesis via provenance query

### 8.4 /retry Flow

```
user sends: /retry "missed some tasks i know i need to do today"
  → push_frame(channel=#hermes-briefings, adapter=daily-briefing, type=retry)
  → apply_effect("vault.read_tasks")
  → apply_effect("synthesize_morning")
      → include original context + feedback from user
      → new quality score
  → apply_effect("send_briefing")
  → close_frame(status=committed)
```

Quality scores are persistent. The protocol maintains a running quality history per adapter per channel.

### 8.5 Templates

Vault-based, user-editable, Jinja2:

```
vault/templates/daily-briefing/
  morning.md.j2
  evening.md.j2
  partials/
    task-list.md.j2
    quality-badge.md.j2
    day-summary.md.j2
```

Hole indicators: `{{ tasks }}`, `{{ date }}`, `{{ quality_context }}`, etc.

---

## 9. Addressing and Cursor Model

### 9.1 Entry Addressing

Every heap entry is addressable by:
- **Heap address**: `(namespace, entry_id)` — stable integer pointer
- **Content hash**: `(namespace, content_hash)` — content-addressable
- **Frame reference**: entries carry their `frame_id`, enabling frame-based queries

### 9.2 Cursor Tracking

Each `Subscription` record has a `cursor_seq` — the last `entry_id` processed by that subscriber. Querying new entries:

```sql
SELECT * FROM heap_entries
  WHERE namespace = :channel
    AND entry_id > :cursor_seq
  ORDER BY entry_id ASC
  LIMIT :limit;
```

This is O(1) index lookup. Cursor advances atomically after processing.

### 9.3 Frame Stack vs. Parallel Subscriptions

Queryable's frame stack is **nested** (sub-frames under parent frames). The messaging protocol uses **parallel sub-frames under a shared channel frame**:

```
Channel frame (#hermes-briefings)
└── subscription frames (independent, same parent)
    ├── sub-frame: daily-briefing adapter
    │   └── work frames: morning-2026-03-27, evening-2026-03-27, retry-...
    ├── sub-frame: research-digest adapter
    └── sub-frame: overseer monitor
```

Each subscription frame has its own cursor and processing state. All subscription frames share the same channel's heap entries. This enables:
- Fan-out: one event, multiple subscribers each see it independently
- Independent processing: daily-briefing and research-digest never block each other
- Audit trail: each adapter's frame tree is independently queryable

---

## 10. Compaction and GC

**Decision: compaction is a later concern.** The heap is the ground truth. Compaction is a storage format decision, analogous to choosing a filesystem and compression level for state serialization. It must always be reversible via pointer chains.

**Design constraint:** All compaction policies must preserve:
1. Pointer chain integrity — any `provenance_json` entry must be resolvable
2. Effect idempotency — `effect_key` lookups must remain correct
3. Frame closure — closed frames must remain queryable

**Future compaction options (open):**
- **Hard compaction**: entries older than N days are summarized; originals deleted, DAG nodes preserve pointer chains
- **Soft compaction**: summaries are themselves summarized periodically, preserving only top-level pointers
- **Namespace partitioning**: per-channel heaps, with independent GC per namespace

The implementation should not assume any compaction policy. All schema decisions should support retroactive compaction without breaking existing pointer chains.

---

## 11. Open Questions

### Q1: Gateway Channel Routing (Section 6.1)

**Question:** How does the gateway know which plugin handles which channel?

- **Option A (recommended):** Plugin registers `channel → handler` mappings at startup. Gateway maintains a dispatch table. Fan-out to all matching handlers. Explicit, auditable, fast.
- **Option B:** Gateway tags messages with `#channel` prefix. Protocol plugin receives all and filters internally.
- **Option C:** Protocol plugin registers as middleware seeing all gateway traffic.

### Q2: Channel Delivery Mode (Section 4.4)

**Question:** Should a message on a channel be delivered to all subscribers (pub/sub fan-out) or exactly one (exclusive dispatch)?

Both are valid. The protocol should support both via a `delivery_mode` field on `Subscription`. Default: `fanout` (pub/sub).

Rationale: fan-out is more expressive — exclusive dispatch is implementable as fan-out with a single subscriber.

### Q3: REST API Auth (Section 7.2)

**Question:** How do external clients authenticate to the mailbox REST API?

- **Option A:** Reuse hermes API server's key (simple, single key)
- **Option A2:** Per-channel keys (fine-grained, revocable)
- **Option B:** Separate mailbox API key in config
- **Option C:** OAuth token exchange (complex, scoped)

**Recommendation:** Option A initially. Upgrade to per-channel keys if multi-tenant access becomes relevant.

### Q4: Template Storage and Vault Access

**Question:** How does the daily-briefing adapter authenticate to vault?

- Vault is on the home server (`/var/mnt/stuff/vault`)
- Hermes-agent runs on the VPS
- Communication: SSH mount, direct network access, or overseer as relay?

This depends on the current vault access topology. Needs clarification before planning the vault adapter.

---

## 12. Research References

### Papers and Systems

- **ESAA** (Event Sourcing for Autonomous Agents, Feb 2026) — Append-only event log, deterministic replay, non-determinism sandboxed at intention-emission. Most directly applicable to the effect ledger design.
- **Dealog** (Dec 2025) — Log-mediated multi-agent coordination. Agents read/append to shared log; synthesizer produces final output. Directly maps to the daily briefing workflow.
- **AgentLog** (Mar 2026) — JSONL append-only files per topic, SSE delivery, consumer offset tracking. Close to the heap + subscription cursor model.
- **Queryable** (sammcf) — Haskell REPL history system with effect ledger, frame stack, and content-addressable heap. Primary design reference; all semantics are ported to Python/SQLite.

### mcp_agent_mail (dicklesworthstone/mcp_agent_mail)

HTTP-only FastMCP server for agent-to-agent coordination. Provides addressable inboxes, dual SQLite+Git persistence, file reservations (advisory locks), and FTS5 search. Different problem (peer coordination vs. protocol), but validates the addressable mailbox + content-addressable storage approach.

---

## 13. Implementation Plan

### Phase 1: Core Store (mailbox.db)

1. Implement `heap_store.py` — append-only heap with content hashing, provenance, and FTS5
2. Implement `frame_store.py` — frame tree with open/close/commit lifecycle
3. Implement `effect_ledger.py` — idempotent effect execution with dedupe
4. Add schema migration support to `mailbox.db`
5. Implement `replay_engine.py` — checkpoint + replay

### Phase 2: Channel and Subscription Primitives

1. Implement `channel_router.py` — channel CRUD, subscription management
2. Implement `incoming_hook.py` — gateway message hook registration
3. Implement `transport_adapter.py` — abstract interface + Telegram implementation
4. Wire incoming Telegram messages through protocol → channel → adapter

### Phase 3: REST API

1. Implement `rest_api.py` — `/api/mailbox/...` endpoints
2. Add API key auth (reuse hermes key initially)
3. Implement SSE endpoint for real-time subscription delivery

### Phase 4: Daily Briefing Adapter

1. Vault adapter — read templates, tasks.md, daily notes
2. Morning workflow — cron trigger, synthesis, send
3. Evening workflow — morning selection parsing, evening synthesis, quality scoring
4. Reply processing — Telegram replies routed to morning frame
5. `/retry` — re-run synthesis with user feedback

### Phase 5: Polish and Observability

1. `mailbox_search` hermes tool — unified FTS search across heap
2. Quality history dashboard — per-channel quality score trends
3. Admin tools — channel management, subscription status, queue depth
4. Compaction policy (future — not in initial scope)

---

## Appendix A: Effect Type Registry

| effect_type | source | description |
|---|---|---|
| `mailbox.enqueue` | protocol | Add a job to the queue |
| `mailbox.send_message` | protocol | Send a message via transport |
| `mailbox.subscribe` | protocol | Subscribe to a channel |
| `vault.read` | daily-briefing | Read file(s) from vault |
| `synthesize_morning` | daily-briefing | Generate morning briefing |
| `synthesize_evening` | daily-briefing | Generate evening briefing |
| `send_briefing` | daily-briefing | Deliver briefing via transport |
| `score_quality` | daily-briefing | Self-review and quality score |
| `retry_synthesis` | daily-briefing | Re-run synthesis with feedback |

---

## Appendix B: Entry Type Registry

| entry_type | description |
|---|---|
| `message` | Incoming or outgoing message on a channel |
| `vault_read` | Result of a vault file read |
| `synthesis` | LLM-generated content |
| `effect_result` | Output of a completed effect |
| `checkpoint` | Frame snapshot (references checkpoint record) |
| `quality_score` | Self-review result with score and reasoning |
| `subscription_event` | Subscribe/unsubscribe events |

---

## Decision Log

| # | Date | Decision | Rationale |
|---|---|---|---|
| 1 | 2026-03-27 | Protocol plugin is the core product; daily briefing is an adapter on top | Enables reuse across use cases; daily briefing validates protocol design |
| 2 | 2026-03-27 | Hermes-agent is the target runtime, not an external dependency | Provides LLM dispatch, SQLite, cron, gateway transport; these are primitives the protocol exploits |
| 3 | 2026-03-27 | "Zero deps" means one dep — hermes-agent — which gives builtin capability | Protocol is portable but defaults to hermes-native implementation |
| 4 | 2026-03-27 | Queryable semantics ported to Python/SQLite | Haskell validates the design; Python implements against Hermes primitives |
| 5 | 2026-03-27 | Separate `mailbox.db`, not extension of `state.db` | Isolates contention, bounds core DB growth, separates schema migration risk |
| 6 | 2026-03-27 | Cross-store concerns solved via application layer, not shared store | Unified search via protocol tool querying both stores; hermes tools surface mailbox data |
| 7 | 2026-03-27 | Compaction is a future, reversible extension | Heap is ground truth; compaction is storage format, always reversible via pointer chains |
| 8 | 2026-03-27 | Frame model: channel (root) → subscription frames (parallel) → work frames (nested) | Parallel subscription frames under shared channel: each adapter has independent cursor and state; all share the channel heap |
| 9 | 2026-03-27 | Effect ledger uses `effect_key` namespacing by frame_id | Prevents collision between adapters; daily-briefing and research-digest can share the same effects table |
| 10 | 2026-03-27 | Content-addressable heap via `(namespace, entry_type, content_hash)` unique constraint | Writing the same content twice is idempotent; entry_id is the stable heap address |
| 11 | 2026-03-27 | Delivery mode per subscription: fan-out (default) or exclusive | Fan-out is more expressive; exclusive dispatch is fan-out with one subscriber |
| 12 | 2026-03-27 | Gateway routing: plugin registers `channel → handler` at startup | Explicit, auditable, fast dict lookup; gateway maintains dispatch table |
| 13 | 2026-03-27 | Cron integration: protocol plugin provides a `cron_adapter` toolset | hermes cron calls into the protocol; job definitions live in vault alongside adapter templates |
| 14 | 2026-03-27 | REST API auth: reuse hermes API key initially | Simple, single key, consistent with hermes security model; per-channel keys as future upgrade |
| 15 | 2026-03-27 | Templates in vault, user-editable, Jinja2 | Decoupled from plugin; dynamic format; parsing is script-driven + LLM-assisted |
| 16 | 2026-03-27 | `/retry` creates a new work frame with feedback appended to synthesis prompt | Idempotent: new frame, new effect chain, new delivery; old frame remains for audit |
| 17 | 2026-03-27 | Quality scoring: agent self-review as explicit effect, score stored in heap | Self-review is a separate non-deterministic step; score is persistent, queryable, and informative for next synthesis |
| 18 | 2026-03-27 | Provenance: `provenance_json` array of parent `entry_id`s | Walking provenance chain backwards reconstructs full source lineage for any synthesis output |
| 19 | 2026-03-27 | At-least-once delivery with idempotent processing | Deterministic retry semantics; GC eligibility computed from cursor state; sufficient for daily briefing and general coordination |
| 20 | 2026-03-27 | Vault access topology: open question — needs clarification | Depends on current vault access topology (SSH mount, direct network, overseer relay) |
