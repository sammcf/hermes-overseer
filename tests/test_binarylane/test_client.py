"""Tests for binarylane.client — retry logic and request wrappers."""

from __future__ import annotations

import httpx
import pytest
import respx

from overseer.binarylane import client as bl_client
from overseer.binarylane.client import api_get, api_post, create_client
from overseer.config import BinaryLaneConfig
from overseer.types import Err, Ok

BASE_URL = "https://api.binarylane.com.au"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress all backoff sleeps in tests."""
    monkeypatch.setattr(bl_client, "_sleep", lambda _: None)


@pytest.fixture()
def token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BL_API_TOKEN", "test-token")


@pytest.fixture()
def http_client(token_env: None) -> httpx.Client:
    config = BinaryLaneConfig()
    return create_client(config)


# ---------------------------------------------------------------------------
# create_client
# ---------------------------------------------------------------------------


def test_create_client_sets_bearer_auth(token_env: None) -> None:
    config = BinaryLaneConfig()
    c = create_client(config)
    assert c.headers["authorization"] == "Bearer test-token"


def test_create_client_sets_base_url(token_env: None) -> None:
    config = BinaryLaneConfig()
    c = create_client(config)
    assert str(c.base_url).rstrip("/") == BASE_URL


def test_create_client_missing_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BL_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="BL_API_TOKEN"):
        create_client(BinaryLaneConfig())


# ---------------------------------------------------------------------------
# api_get — success
# ---------------------------------------------------------------------------


@respx.mock
def test_api_get_success(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/1").mock(
        return_value=httpx.Response(200, json={"server": {"id": 1}})
    )
    result = api_get(http_client, "/v2/servers/1", max_retries=0)
    assert isinstance(result, Ok)
    assert result.value == {"server": {"id": 1}}


# ---------------------------------------------------------------------------
# api_post — success
# ---------------------------------------------------------------------------


@respx.mock
def test_api_post_success(http_client: httpx.Client) -> None:
    respx.post(f"{BASE_URL}/v2/servers/1/actions").mock(
        return_value=httpx.Response(200, json={"action": {"id": 42, "status": "in-progress"}})
    )
    result = api_post(http_client, "/v2/servers/1/actions", {"type": "power_off"}, max_retries=0)
    assert isinstance(result, Ok)
    assert result.value["action"]["id"] == 42


# ---------------------------------------------------------------------------
# 429 — retry then succeed
# ---------------------------------------------------------------------------


@respx.mock
def test_api_get_retries_on_429(http_client: httpx.Client) -> None:
    route = respx.get(f"{BASE_URL}/v2/servers/1")
    route.side_effect = [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, json={"server": {"id": 1}}),
    ]
    result = api_get(http_client, "/v2/servers/1", max_retries=2)
    assert isinstance(result, Ok)
    assert route.call_count == 2


# ---------------------------------------------------------------------------
# 5xx — retry then succeed
# ---------------------------------------------------------------------------


@respx.mock
def test_api_get_retries_on_500(http_client: httpx.Client) -> None:
    route = respx.get(f"{BASE_URL}/v2/servers/1")
    route.side_effect = [
        httpx.Response(500, text="internal error"),
        httpx.Response(500, text="internal error"),
        httpx.Response(200, json={"server": {"id": 1}}),
    ]
    result = api_get(http_client, "/v2/servers/1", max_retries=3)
    assert isinstance(result, Ok)
    assert route.call_count == 3


# ---------------------------------------------------------------------------
# Max retries exhausted → Err
# ---------------------------------------------------------------------------


@respx.mock
def test_api_get_max_retries_exhausted(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/1").mock(return_value=httpx.Response(503, text="unavailable"))
    result = api_get(http_client, "/v2/servers/1", max_retries=2)
    assert isinstance(result, Err)
    assert "503" in result.error or "attempts" in result.error


@respx.mock
def test_api_get_exactly_max_retries_plus_one_attempts(http_client: httpx.Client) -> None:
    """With max_retries=N, total attempts should be N+1."""
    route = respx.get(f"{BASE_URL}/v2/servers/1")
    route.mock(return_value=httpx.Response(503, text="unavailable"))
    api_get(http_client, "/v2/servers/1", max_retries=3)
    assert route.call_count == 4


# ---------------------------------------------------------------------------
# Non-retryable 4xx → Err immediately (no retry)
# ---------------------------------------------------------------------------


@respx.mock
def test_api_get_non_retryable_4xx_no_retry(http_client: httpx.Client) -> None:
    route = respx.get(f"{BASE_URL}/v2/servers/1")
    route.mock(return_value=httpx.Response(404, text="not found"))
    result = api_get(http_client, "/v2/servers/1", max_retries=5)
    assert isinstance(result, Err)
    assert "404" in result.error
    # Should bail immediately — only one attempt
    assert route.call_count == 1


@respx.mock
def test_api_get_403_no_retry(http_client: httpx.Client) -> None:
    route = respx.get(f"{BASE_URL}/v2/servers/1")
    route.mock(return_value=httpx.Response(403, text="forbidden"))
    result = api_get(http_client, "/v2/servers/1", max_retries=5)
    assert isinstance(result, Err)
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Request-level error (network failure) → retried
# ---------------------------------------------------------------------------


@respx.mock
def test_api_get_retries_on_network_error(http_client: httpx.Client) -> None:
    route = respx.get(f"{BASE_URL}/v2/servers/1")
    route.side_effect = [
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json={"ok": True}),
    ]
    result = api_get(http_client, "/v2/servers/1", max_retries=2)
    assert isinstance(result, Ok)
    assert route.call_count == 2
