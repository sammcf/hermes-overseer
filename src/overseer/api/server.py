"""Lightweight aiohttp API server for overseer.

Endpoints:
  GET  /health   — unauthenticated liveness probe
  GET  /status   — poll state, uptime, version (bearer auth)
  POST /snapshot — trigger on-demand snapshot (bearer auth)
  POST /rebuild  — trigger full rebuild pipeline (bearer auth)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from overseer.config import Config, resolve_secret
from overseer.types import Err, Ok, PollState

logger = logging.getLogger(__name__)

_VERSION = "0.1.0"


@dataclass
class AppState:
    """Shared mutable state between the API server and the main loop."""

    config: Config
    poll_state: PollState
    bl_client: Any  # httpx.Client — passed through to actions
    start_time: float
    op_lock: asyncio.Lock  # prevents concurrent snapshot/rebuild


_APP_STATE_KEY = web.AppKey("app_state", AppState)


def _bearer_token(cfg: Config) -> str:
    return resolve_secret(cfg.api.bearer_token_env)


def _check_auth(request: web.Request, app_state: AppState) -> web.Response | None:
    """Return an error response if auth fails, or None if authorised."""
    expected = _bearer_token(app_state.config)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {expected}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_status(request: web.Request) -> web.Response:
    app_state: AppState = request.app[_APP_STATE_KEY]
    auth_err = _check_auth(request, app_state)
    if auth_err is not None:
        return auth_err

    ps = app_state.poll_state
    uptime = time.monotonic() - app_state.start_time
    return web.json_response({
        "version": _VERSION,
        "uptime_seconds": round(uptime, 1),
        "last_poll_time": ps.last_poll_time.isoformat() if ps.last_poll_time else None,
        "sustained_unknown_count": ps.sustained_unknown_count,
        "vps_hostname": app_state.config.vps.tailscale_hostname,
    })


async def handle_snapshot(request: web.Request) -> web.Response:
    app_state: AppState = request.app[_APP_STATE_KEY]
    auth_err = _check_auth(request, app_state)
    if auth_err is not None:
        return auth_err

    if app_state.op_lock.locked():
        return web.json_response({"error": "another operation in progress"}, status=409)

    async with app_state.op_lock:
        from overseer.backup.snapshot import prune_snapshots, take_snapshot

        cfg = app_state.config
        result = await asyncio.to_thread(
            take_snapshot,
            cfg.vps.tailscale_hostname,
            cfg.vps.ssh_user,
            cfg.vps.hermes_home,
            cfg.backup.dir,
            cfg.backup.extra_paths,
        )

    if isinstance(result, Err):
        return web.json_response({"error": result.error}, status=500)

    # Prune after successful snapshot
    pruned = prune_snapshots(cfg.backup.dir, cfg.backup.retention_count)
    filename = result.value.rsplit("/", 1)[-1]
    return web.json_response({"filename": filename, "pruned": pruned})


async def handle_rebuild(request: web.Request) -> web.Response:
    app_state: AppState = request.app[_APP_STATE_KEY]
    auth_err = _check_auth(request, app_state)
    if auth_err is not None:
        return auth_err

    if app_state.op_lock.locked():
        return web.json_response({"error": "another operation in progress"}, status=409)

    async with app_state.op_lock:
        from overseer.provision.provisioner import provision_after_rebuild

        result = await asyncio.to_thread(
            provision_after_rebuild,
            app_state.config,
            app_state.bl_client,
        )

    if isinstance(result, Err):
        return web.json_response({"error": result.error}, status=500)

    r = result.value
    return web.json_response({
        "status": "complete",
        "config_pushed": r.config_pushed,
        "env_pushed": r.env_pushed,
        "service_started": r.service_started,
    })


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(app_state: AppState) -> web.Application:
    """Build the aiohttp application with routes and shared state."""
    app = web.Application()
    app[_APP_STATE_KEY] = app_state
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/snapshot", handle_snapshot)
    app.router.add_post("/rebuild", handle_rebuild)
    return app


async def start_api_server(app_state: AppState) -> web.AppRunner:
    """Start the API server as a background task. Returns the runner for cleanup."""
    cfg = app_state.config
    app = create_app(app_state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.api.host, cfg.api.port)
    await site.start()
    logger.info("API server listening on %s:%d", cfg.api.host, cfg.api.port)
    return runner
