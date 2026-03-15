# WU-002: Service-Agnostic Adapter/Proxy Layer

## Status: Planned

## Motivation

Hermes-agent has hard dependencies on external SaaS APIs (Firecrawl, potentially
others). These cost money, have rate limits, and create vendor lock-in. We want
to slot in self-hosted backends where practical — starting with Crawl4AI
replacing Firecrawl — without modifying hermes-agent code.

The adapter pattern: a thin shim that speaks the SaaS API on the frontend and
the self-hosted service on the backend. Hermes-agent points its env vars at the
adapter instead of the SaaS, and everything works transparently.

## Architecture

```
hermes-agent (VPS)
  │
  │  FIRECRAWL_API_URL=http://home-server:8080
  │
  ├── Tailscale ──► home server (tailnet)
                      │
                      ├── firecrawl-adapter :8080 (FastAPI)
                      │     │
                      │     └──► crawl4ai :11235 (container)
                      │
                      └── (future adapters on other ports)
```

All traffic stays on the Tailnet. No public exposure.

## Changes

### 1. Crawl4AI container — `adapters/crawl4ai/`

**Quadlet file:** `adapters/crawl4ai/crawl4ai.container`

```ini
[Container]
Image=unclecode/crawl4ai:latest
PublishPort=127.0.0.1:11235:11235
Volume=crawl4ai-data:/app/data:Z
AutoUpdate=registry

[Service]
Restart=always

[Install]
WantedBy=default.target
```

Runs rootless via podman. Listens only on localhost — the adapter reaches it
directly, external access goes through the adapter.

### 2. Firecrawl adapter — `adapters/firecrawl-adapter/`

**Thin FastAPI shim (~150 lines)** that translates Firecrawl API → Crawl4AI API.

Core endpoints to implement:

| Firecrawl endpoint | Crawl4AI equivalent | Notes |
|--------------------|---------------------|-------|
| `POST /v1/scrape` | `POST /crawl` | Main scrape endpoint. Map `url` + `formats` → Crawl4AI request. Return markdown. |
| `POST /v1/crawl` | `POST /crawl` | Multi-page crawl. Crawl4AI handles depth natively. |
| `GET /v1/crawl/{id}` | `GET /task/{id}` | Poll async crawl status. |

**Request translation (scrape):**
```python
# Firecrawl request:
{"url": "https://example.com", "formats": ["markdown"]}

# → Crawl4AI request:
{"urls": ["https://example.com"], "priority": 8}

# Crawl4AI response → Firecrawl response:
{"success": True, "data": {"markdown": result["result"]["markdown"], "metadata": {...}}}
```

**Key design decisions:**
- Stateless — no database, no caching (Crawl4AI handles its own cache)
- API key passthrough: accept `Authorization: Bearer <key>` header but don't
  validate it (or use a simple shared secret for minimal auth)
- Error mapping: Crawl4AI errors → appropriate Firecrawl-style error responses
- Timeout: proxy Crawl4AI's async model, return Firecrawl-compatible status
  polling responses

**Packaging:** `adapters/firecrawl-adapter/Dockerfile` — minimal Python image
with FastAPI + httpx + uvicorn.

**Quadlet file:** `adapters/firecrawl-adapter/firecrawl-adapter.container`

```ini
[Container]
Image=localhost/firecrawl-adapter:latest
PublishPort=8080:8080
Network=host
Environment=CRAWL4AI_URL=http://127.0.0.1:11235

[Service]
Restart=always

[Install]
WantedBy=default.target
```

### 3. Overseer config — adapter registry

Add to `config.py`:

```python
class AdapterConfig(BaseModel, frozen=True):
    name: str                    # e.g. "firecrawl"
    listen_port: int             # e.g. 8080
    backend_url: str             # e.g. "http://127.0.0.1:11235"
    health_endpoint: str = "/health"
    enabled: bool = True
```

```yaml
# overseer.yaml
adapters:
  - name: firecrawl
    listen_port: 8080
    backend_url: "http://127.0.0.1:11235"
    health_endpoint: "/health"
```

### 4. Overseer adapter health monitoring

New monitor: `src/overseer/monitor/adapters.py`

- Periodic health check: `GET {adapter_url}/health` for each enabled adapter
- Reports adapter down/up status changes
- Integrates with existing alert pipeline (Telegram notification on adapter failure)

### 5. Hermes config update

In `config/hermes-canonical.yaml`, update:
```yaml
# Point at adapter instead of Firecrawl SaaS
firecrawl_api_url: "http://home-server:8080"
```

Remove `FIRECRAWL_API_KEY` from hermes secrets (no longer needed — self-hosted).

### 6. Deployment

On the home server (same machine as overseer distrobox):

```bash
# Deploy Crawl4AI
mkdir -p ~/.config/containers/systemd
cp adapters/crawl4ai/crawl4ai.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start crawl4ai

# Build and deploy adapter
podman build -t firecrawl-adapter adapters/firecrawl-adapter/
cp adapters/firecrawl-adapter/firecrawl-adapter.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start firecrawl-adapter
```

---

## Files to create/modify

| File | Action |
|------|--------|
| `adapters/crawl4ai/crawl4ai.container` | New: Quadlet definition |
| `adapters/firecrawl-adapter/app.py` | New: FastAPI adapter (~150 lines) |
| `adapters/firecrawl-adapter/Dockerfile` | New: container build |
| `adapters/firecrawl-adapter/requirements.txt` | New: fastapi, httpx, uvicorn |
| `adapters/firecrawl-adapter/firecrawl-adapter.container` | New: Quadlet definition |
| `src/overseer/config.py` | Add `AdapterConfig`, adapter list |
| `src/overseer/monitor/adapters.py` | New: adapter health checks |
| `config/overseer.example.yaml` | Add adapter config section |
| `config/hermes-canonical.yaml` | Update firecrawl URL |
| `tests/test_adapters/` | New: adapter translation tests |

---

## Future: additional adapters

The pattern generalises. Potential candidates:

- **Search:** SearXNG replacing any search API
- **TTS/STT:** Local Whisper/Piper replacing cloud speech APIs
- **LLM routing:** Local models via Ollama for low-stakes tasks

Each adapter follows the same structure: Quadlet container, FastAPI shim,
overseer health monitoring.

---

## Verification

1. `curl http://localhost:8080/v1/scrape -d '{"url":"https://example.com"}'` returns markdown
2. Hermes-agent web browsing works end-to-end via adapter
3. Overseer reports adapter health in monitoring cycle
4. `uv run pytest` — all tests pass
5. `uv run mypy src/` — clean
