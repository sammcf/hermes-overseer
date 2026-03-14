"""Tests for binarylane.queries — read-only API calls."""

from __future__ import annotations

import httpx
import pytest
import respx

from overseer.binarylane import client as bl_client
from overseer.binarylane.client import create_client
from overseer.binarylane.queries import get_metrics, get_server, get_threshold_alerts, list_backups
from overseer.config import BinaryLaneConfig
from overseer.types import Err, Ok

BASE_URL = "https://api.binarylane.com.au"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl_client, "_sleep", lambda _: None)


@pytest.fixture()
def http_client(monkeypatch: pytest.MonkeyPatch) -> httpx.Client:
    monkeypatch.setenv("BL_API_TOKEN", "test-token")
    return create_client(BinaryLaneConfig())


# ---------------------------------------------------------------------------
# get_server
# ---------------------------------------------------------------------------

SERVER_PAYLOAD = {
    "server": {
        "id": 123,
        "name": "hermes",
        "status": "active",
        "networks": {"v4": [{"ip_address": "1.2.3.4"}]},
    }
}


@respx.mock
def test_get_server_success(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/123").mock(
        return_value=httpx.Response(200, json=SERVER_PAYLOAD)
    )
    result = get_server(http_client, 123)
    assert isinstance(result, Ok)
    assert result.value["id"] == 123
    assert result.value["name"] == "hermes"


@respx.mock
def test_get_server_not_found(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/999").mock(
        return_value=httpx.Response(404, text="not found")
    )
    result = get_server(http_client, 999)
    assert isinstance(result, Err)
    assert "404" in result.error


@respx.mock
def test_get_server_missing_key(http_client: httpx.Client) -> None:
    """Response 200 but no 'server' key → Err."""
    respx.get(f"{BASE_URL}/v2/servers/123").mock(
        return_value=httpx.Response(200, json={"unexpected": "data"})
    )
    result = get_server(http_client, 123)
    assert isinstance(result, Err)
    assert "server" in result.error.lower()


# ---------------------------------------------------------------------------
# get_metrics
# ---------------------------------------------------------------------------

METRICS_PAYLOAD = {
    "sample_set": {
        "server_id": 123,
        "average_cpu": 12.5,
        "average_memory": 45.2,
    }
}


@respx.mock
def test_get_metrics_success(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/samplesets/123/latest").mock(
        return_value=httpx.Response(200, json=METRICS_PAYLOAD)
    )
    result = get_metrics(http_client, 123)
    assert isinstance(result, Ok)
    assert result.value["server_id"] == 123
    assert result.value["average_cpu"] == 12.5


@respx.mock
def test_get_metrics_error(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/samplesets/123/latest").mock(
        return_value=httpx.Response(500, text="error")
    )
    result = get_metrics(http_client, 123)
    assert isinstance(result, Err)


@respx.mock
def test_get_metrics_missing_key(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/samplesets/123/latest").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    result = get_metrics(http_client, 123)
    assert isinstance(result, Err)
    assert "sample_set" in result.error


# ---------------------------------------------------------------------------
# get_threshold_alerts
# ---------------------------------------------------------------------------

ALERTS_PAYLOAD = {
    "threshold_alerts": [
        {"id": 1, "type": "cpu", "threshold": 90},
        {"id": 2, "type": "memory", "threshold": 85},
    ]
}


@respx.mock
def test_get_threshold_alerts_success(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/threshold_alerts").mock(
        return_value=httpx.Response(200, json=ALERTS_PAYLOAD)
    )
    result = get_threshold_alerts(http_client)
    assert isinstance(result, Ok)
    assert len(result.value) == 2
    assert result.value[0]["type"] == "cpu"


@respx.mock
def test_get_threshold_alerts_empty(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/threshold_alerts").mock(
        return_value=httpx.Response(200, json={"threshold_alerts": []})
    )
    result = get_threshold_alerts(http_client)
    assert isinstance(result, Ok)
    assert result.value == []


@respx.mock
def test_get_threshold_alerts_missing_key(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/threshold_alerts").mock(
        return_value=httpx.Response(200, json={})
    )
    result = get_threshold_alerts(http_client)
    assert isinstance(result, Err)
    assert "threshold_alerts" in result.error


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------

BACKUPS_PAYLOAD = {
    "backups": [
        {"id": "bk-1", "created_at": "2026-01-01T00:00:00Z", "type": "temporary"},
        {"id": "bk-2", "created_at": "2026-01-02T00:00:00Z", "type": "temporary"},
    ]
}


@respx.mock
def test_list_backups_success(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/123/backups").mock(
        return_value=httpx.Response(200, json=BACKUPS_PAYLOAD)
    )
    result = list_backups(http_client, 123)
    assert isinstance(result, Ok)
    assert len(result.value) == 2
    assert result.value[0]["id"] == "bk-1"


@respx.mock
def test_list_backups_empty(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/123/backups").mock(
        return_value=httpx.Response(200, json={"backups": []})
    )
    result = list_backups(http_client, 123)
    assert isinstance(result, Ok)
    assert result.value == []


@respx.mock
def test_list_backups_error(http_client: httpx.Client) -> None:
    respx.get(f"{BASE_URL}/v2/servers/123/backups").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    result = list_backups(http_client, 123)
    assert isinstance(result, Err)
    assert "403" in result.error
