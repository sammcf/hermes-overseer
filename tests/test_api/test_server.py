"""Tests for the overseer HTTP API server."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

from overseer.api.server import AppState, create_app
from overseer.types import Err, Ok, PollState, ProvisionResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TOKEN = "test-bearer-token-123"


def _make_app_state(
    poll_state: PollState | None = None,
) -> AppState:
    """Build an AppState with a minimal fake config."""
    import warnings

    from overseer.config import Config

    raw = {
        "vps": {"server_id": 1, "tailscale_hostname": "hermes-test"},
        "alerts": {
            "telegram": {"dm_chat_id": "999"},
            "email": {"from_address": "a@b.com", "to_address": "c@d.com"},
        },
        "api": {"enabled": True, "bearer_token_env": "TEST_API_TOKEN"},
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = Config.model_validate(raw)

    return AppState(
        config=cfg,
        poll_state=poll_state or PollState(),
        bl_client=None,
        start_time=time.monotonic(),
        op_lock=asyncio.Lock(),
    )


@pytest.fixture
def app_state() -> AppState:
    return _make_app_state()


@pytest.fixture
async def client(app_state: AppState, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TEST_API_TOKEN", _TOKEN)
    app = create_app(app_state)
    server = TestServer(app)
    tc = TestClient(server)
    await tc.start_server()
    yield tc
    await tc.close()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ---------------------------------------------------------------------------
# /health — unauthenticated
# ---------------------------------------------------------------------------


async def test_health_returns_ok(client: TestClient) -> None:
    resp = await client.get("/health")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok"


async def test_health_no_auth_required(client: TestClient) -> None:
    resp = await client.get("/health")
    assert resp.status == 200


# ---------------------------------------------------------------------------
# /status — authenticated
# ---------------------------------------------------------------------------


async def test_status_requires_auth(client: TestClient) -> None:
    resp = await client.get("/status")
    assert resp.status == 401


async def test_status_wrong_token(client: TestClient) -> None:
    resp = await client.get("/status", headers={"Authorization": "Bearer wrong"})
    assert resp.status == 401


async def test_status_returns_state(
    client: TestClient, app_state: AppState
) -> None:
    ts = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC)
    app_state.poll_state = PollState(last_poll_time=ts, sustained_unknown_count=3)
    resp = await client.get("/status", headers=_auth_headers())
    assert resp.status == 200
    body = await resp.json()
    assert body["sustained_unknown_count"] == 3
    assert "2026-03-20" in body["last_poll_time"]
    assert body["vps_hostname"] == "hermes-test"
    assert "uptime_seconds" in body
    assert "version" in body


async def test_status_never_polled(client: TestClient) -> None:
    resp = await client.get("/status", headers=_auth_headers())
    assert resp.status == 200
    body = await resp.json()
    assert body["last_poll_time"] is None
    assert body["sustained_unknown_count"] == 0


# ---------------------------------------------------------------------------
# /snapshot — authenticated
# ---------------------------------------------------------------------------


async def test_snapshot_requires_auth(client: TestClient) -> None:
    resp = await client.post("/snapshot")
    assert resp.status == 401


async def test_snapshot_success(client: TestClient) -> None:
    with patch(
        "overseer.backup.snapshot.take_snapshot",
        return_value=Ok("/backups/hermes-state-20260320T120000Z.tar.gz"),
    ), patch("overseer.backup.snapshot.prune_snapshots", return_value=1):
        resp = await client.post("/snapshot", headers=_auth_headers())

    assert resp.status == 200
    body = await resp.json()
    assert "hermes-state" in body["filename"]
    assert body["pruned"] == 1


async def test_snapshot_failure(client: TestClient) -> None:
    with patch(
        "overseer.backup.snapshot.take_snapshot",
        return_value=Err("ssh failed", source="snapshot"),
    ):
        resp = await client.post("/snapshot", headers=_auth_headers())

    assert resp.status == 500
    body = await resp.json()
    assert "ssh failed" in body["error"]


# ---------------------------------------------------------------------------
# /rebuild — authenticated
# ---------------------------------------------------------------------------


async def test_rebuild_requires_auth(client: TestClient) -> None:
    resp = await client.post("/rebuild")
    assert resp.status == 401


async def test_rebuild_success(client: TestClient) -> None:
    with patch(
        "overseer.provision.provisioner.provision_after_rebuild",
        return_value=Ok(ProvisionResult(
            rebuild_action={},
            config_pushed=True,
            env_pushed=True,
            service_started=True,
        )),
    ):
        resp = await client.post("/rebuild", headers=_auth_headers())

    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "complete"
    assert body["config_pushed"] is True


async def test_rebuild_failure(client: TestClient) -> None:
    with patch(
        "overseer.provision.provisioner.provision_after_rebuild",
        return_value=Err("BL API error", source="provision"),
    ):
        resp = await client.post("/rebuild", headers=_auth_headers())

    assert resp.status == 500
    assert "BL API error" in (await resp.json())["error"]


# ---------------------------------------------------------------------------
# Concurrency: op_lock prevents simultaneous operations
# ---------------------------------------------------------------------------


async def test_snapshot_returns_409_when_locked(
    client: TestClient, app_state: AppState
) -> None:
    # Acquire the lock externally to simulate an in-progress operation
    await app_state.op_lock.acquire()
    try:
        resp = await client.post("/snapshot", headers=_auth_headers())
        assert resp.status == 409
        body = await resp.json()
        assert "in progress" in body["error"]
    finally:
        app_state.op_lock.release()


async def test_rebuild_returns_409_when_locked(
    client: TestClient, app_state: AppState
) -> None:
    await app_state.op_lock.acquire()
    try:
        resp = await client.post("/rebuild", headers=_auth_headers())
        assert resp.status == 409
    finally:
        app_state.op_lock.release()
