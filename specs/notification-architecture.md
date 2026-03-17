# Spec: Unified Notification & API Architecture

## Status: Draft — refinement in progress, implementation target next few days

## System Boundaries

### Overseer
**Owns:** Configuration (canonical, immutable), secrets/API keys, VPS provider API
(standup/teardown/rebuild), frozen snapshots, threat model enforcement.

**Does NOT own:** Live runtime state. The agent always knows its own state better
than overseer does.

**Exposes:**
- `GET /status` — current overseer state (poll results, last signals, alert tier)
- `POST /snapshot` — trigger on-demand snapshot
- `POST /rebuild` — trigger full rebuild pipeline

That's it. Minimal surface. Everything else is internal.

**Telegram bot:** Announces heartbeats and alerts to #hermes group + DMs operator.
Exposes operator-only functionality via `/commands` (human-only, not for hermes).

### Hermes
**Owns:** Live runtime state, sessions, ongoing work, evolving memory/context.
Has its own API surface (ACP, gateway). Uses plugin hooks (`on_session_start`,
`post_llm_call`, `on_session_end`) for automated self-observation.

**Self-aware of threat models:** Hermes is advised of our threat models and
assessment tools. If it observes an issue, it triggers `/snapshot` and `/rebuild`
via overseer's REST API autonomously.

**Telegram bot:** Primary user interface. Sees all messages in #hermes group
(except overseer bot's — Telegram bot-to-bot limitation).

### The Bot-to-Bot Problem

Telegram bots cannot see other bots' messages. This means:
- Overseer announces alerts in #hermes → hermes bot can't see them
- Hermes responds in #hermes → overseer bot can't see those

Telegram is a **human display layer**, not a system communication bus.

## Notification System

### Architecture: Unified Notification Bus

Instead of Telegram as the primary notification channel with HTTP as a workaround,
**invert it**: the notification system is an internal abstraction that dispatches
to multiple receivers.

```
[Notification Bus]
    │
    ├── Telegram receiver (group + DM)
    ├── HTTP push receiver (hermes agent API)
    ├── Email receiver (existing)
    └── (future receivers: webhooks, etc.)
```

### Overseer → Hermes Flow

1. Overseer detects condition (alert, heartbeat, status change)
2. Notification bus formats the message once
3. Bus dispatches to all registered receivers:
   - Telegram: posts to #hermes group (human sees it)
   - HTTP push: POSTs to hermes receiving endpoint (agent sees it)
   - Email: sends to operator (independent channel)

**Net effect from the human's perspective:** In the #hermes Telegram group, it
appears as if hermes "receives" the overseer's messages natively — hermes can
acknowledge, respond, or act on them. In reality, the content arrives via HTTP
push and is injected into hermes's context for the #hermes channel.

### Hermes → Overseer Flow

Hermes calls overseer REST endpoints directly over Tailnet:
- `GET /status` — check overseer's view of system health
- `POST /snapshot` — request snapshot before risky operations
- `POST /rebuild` — self-initiated rebuild if hermes detects an issue

No notification bus needed for this direction — it's direct API calls.

### Symmetry: Telegram Commands ↔ HTTP API

The Telegram bot `/commands` and the HTTP API endpoints trigger identical
responses. `/status` in Telegram and `GET /status` via HTTP run the same
underlying function. `/snapshot` in Telegram and `POST /snapshot` via HTTP
run the same pipeline.

```
[User sends /status in Telegram]  →  parse_command()  →  handle_status()
[Agent calls GET /status via HTTP] →                  →  handle_status()
```

The handlers are the shared core. Telegram and HTTP are just transport adapters.
This makes the system more robust (multiple access paths to same functionality)
and more extensible (new transports don't require new handlers).

## Tailscale ACL Changes

Current: `tag:hermes` gets outbound internet only, zero Tailnet peer access.

Required: Allow `tag:hermes` → `tag:overseer` on overseer's HTTP port only.
Scoped, deliberate, minimal. Hermes can call overseer API but nothing else
on the Tailnet.

## Future: Development Harness Integration

The overseer proxy model means any claude code session (or other dev tooling)
working on the overseer codebase can reach hermes through overseer's API — no
direct VPS access needed. Overseer acts as an authenticated relay.

Use cases:
- Query hermes live state before deploying config changes
- Trigger graceful snapshot from a dev session before risky operations
- Inject system advisories ("rebuild imminent, wrap up")
- Run diagnostics ("current model, context pressure, active sessions")

Hermes in the other direction: call overseer to check policy compliance before
expensive operations, shifting from external-enforcement-after-the-fact to
real-time pre-flight checks.

Longer term: a queryable resource adapter could wrap the hermes API as a typed,
cached, provenance-tracked resource — "hermes live state" becomes addressable
in the same framework as files and databases.

## Open Questions

1. **Hermes receiving API:** What does hermes's inbound API look like? Need to
   explore ACP and gateway APIs to find the right injection point for context
   insertion in the #hermes channel.
2. **Auth on overseer endpoints:** Shared secret / mTLS / Tailscale identity?
   Tailscale already authenticates the connection, but defense in depth says
   add a bearer token.
3. **Notification message format:** Structured (JSON with type/severity/body)
   or plain text? Structured is better for hermes to parse; plain text is what
   Telegram displays. Probably both: structured payload with a `display_text`
   field.
4. **Hermes self-rebuild trigger:** What conditions should hermes autonomously
   decide to snapshot/rebuild? This needs a well-defined policy to prevent
   the agent from triggering unnecessary rebuilds.
