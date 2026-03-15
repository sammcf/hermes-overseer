"""Tests for binarylane.actions — mutating API calls and poll_action."""

from __future__ import annotations

import httpx
import pytest
import respx

from overseer.binarylane import actions as bl_actions
from overseer.binarylane import client as bl_client
from overseer.binarylane.actions import poll_action, power_off, power_on, rebuild, take_backup
from overseer.binarylane.client import create_client
from overseer.config import BinaryLaneConfig
from overseer.types import Err, Ok

BASE_URL = "https://api.binarylane.com.au"
SERVER_ID = 123
ACTIONS_URL = f"{BASE_URL}/v2/servers/{SERVER_ID}/actions"
ACTION_URL = f"{BASE_URL}/v2/servers/{SERVER_ID}/actions/42"


def _action_response(action_id: int = 42, status: str = "in-progress") -> dict:  # type: ignore[type-arg]
    return {"action": {"id": action_id, "status": status, "type": "power_off"}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl_client, "_sleep", lambda _: None)
    monkeypatch.setattr(bl_actions, "_sleep", lambda _: None)


@pytest.fixture()
def http_client(monkeypatch: pytest.MonkeyPatch) -> httpx.Client:
    monkeypatch.setenv("BL_API_TOKEN", "test-token")
    return create_client(BinaryLaneConfig())


# ---------------------------------------------------------------------------
# power_off
# ---------------------------------------------------------------------------


@respx.mock
def test_power_off_success(http_client: httpx.Client) -> None:
    respx.post(ACTIONS_URL).mock(
        return_value=httpx.Response(200, json=_action_response(status="in-progress"))
    )
    result = power_off(http_client, SERVER_ID)
    assert isinstance(result, Ok)
    assert result.value["id"] == 42


@respx.mock
def test_power_off_verifies_request_body(http_client: httpx.Client) -> None:
    route = respx.post(ACTIONS_URL).mock(
        return_value=httpx.Response(200, json=_action_response())
    )
    power_off(http_client, SERVER_ID)
    import json
    body = json.loads(route.calls.last.request.content)
    assert body == {"type": "power_off"}


@respx.mock
def test_power_off_error(http_client: httpx.Client) -> None:
    respx.post(ACTIONS_URL).mock(return_value=httpx.Response(500, text="error"))
    result = power_off(http_client, SERVER_ID)
    assert isinstance(result, Err)


# ---------------------------------------------------------------------------
# power_on
# ---------------------------------------------------------------------------


@respx.mock
def test_power_on_success(http_client: httpx.Client) -> None:
    route = respx.post(ACTIONS_URL).mock(
        return_value=httpx.Response(200, json={"action": {"id": 43, "status": "in-progress"}})
    )
    result = power_on(http_client, SERVER_ID)
    assert isinstance(result, Ok)
    assert result.value["id"] == 43
    import json
    body = json.loads(route.calls.last.request.content)
    assert body == {"type": "power_on"}


# ---------------------------------------------------------------------------
# take_backup
# ---------------------------------------------------------------------------


@respx.mock
def test_take_backup_success(http_client: httpx.Client) -> None:
    route = respx.post(ACTIONS_URL).mock(
        return_value=httpx.Response(200, json={"action": {"id": 44, "status": "in-progress"}})
    )
    result = take_backup(http_client, SERVER_ID)
    assert isinstance(result, Ok)
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "take_backup"
    assert body["backup_type"] == "temporary"


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------


@respx.mock
def test_rebuild_success(http_client: httpx.Client) -> None:
    route = respx.post(ACTIONS_URL).mock(
        return_value=httpx.Response(200, json={"action": {"id": 45, "status": "in-progress"}})
    )
    result = rebuild(http_client, SERVER_ID, "ubuntu-24.04")
    assert isinstance(result, Ok)
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "rebuild"
    assert body["image"] == "ubuntu-24.04"
    assert "options" not in body


@respx.mock
def test_rebuild_with_user_data(http_client: httpx.Client) -> None:
    route = respx.post(ACTIONS_URL).mock(
        return_value=httpx.Response(200, json={"action": {"id": 45, "status": "in-progress"}})
    )
    result = rebuild(http_client, SERVER_ID, "ubuntu-24.04", user_data="#!/bin/bash\necho hi")
    assert isinstance(result, Ok)
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["options"]["user_data"] == "#!/bin/bash\necho hi"


# ---------------------------------------------------------------------------
# poll_action — success path
# ---------------------------------------------------------------------------


@respx.mock
def test_poll_action_completes_immediately(http_client: httpx.Client) -> None:
    respx.get(ACTION_URL).mock(
        return_value=httpx.Response(200, json={"action": {"id": 42, "status": "completed"}})
    )
    result = poll_action(http_client, SERVER_ID, 42, timeout_seconds=10.0)
    assert isinstance(result, Ok)
    assert result.value["status"] == "completed"


@respx.mock
def test_poll_action_polls_until_complete(http_client: httpx.Client) -> None:
    route = respx.get(ACTION_URL)
    route.side_effect = [
        httpx.Response(200, json={"action": {"id": 42, "status": "in-progress"}}),
        httpx.Response(200, json={"action": {"id": 42, "status": "in-progress"}}),
        httpx.Response(200, json={"action": {"id": 42, "status": "completed"}}),
    ]
    result = poll_action(http_client, SERVER_ID, 42, timeout_seconds=60.0)
    assert isinstance(result, Ok)
    assert route.call_count == 3


# ---------------------------------------------------------------------------
# poll_action — error status
# ---------------------------------------------------------------------------


@respx.mock
def test_poll_action_errored_status(http_client: httpx.Client) -> None:
    respx.get(ACTION_URL).mock(
        return_value=httpx.Response(200, json={"action": {"id": 42, "status": "errored"}})
    )
    result = poll_action(http_client, SERVER_ID, 42, timeout_seconds=10.0)
    assert isinstance(result, Err)
    assert "errored" in result.error


# ---------------------------------------------------------------------------
# poll_action — timeout
# ---------------------------------------------------------------------------


def test_poll_action_timeout(http_client: httpx.Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Timeout fires when monotonic clock advances past deadline."""
    import time

    # We'll simulate time advancing by mocking monotonic.
    # Each call to monotonic returns a higher value, so deadline is exceeded quickly.
    times = iter([0.0, 0.0, 999.0])  # first two calls init + first check, third exceeds deadline

    monkeypatch.setattr(time, "monotonic", lambda: next(times))

    with respx.mock:
        respx.get(ACTION_URL).mock(
            return_value=httpx.Response(200, json={"action": {"id": 42, "status": "in-progress"}})
        )
        result = poll_action(http_client, SERVER_ID, 42, timeout_seconds=5.0)

    assert isinstance(result, Err)
    assert "timed out" in result.error


# ---------------------------------------------------------------------------
# poll_action — API error during polling
# ---------------------------------------------------------------------------


@respx.mock
def test_poll_action_api_error(http_client: httpx.Client) -> None:
    respx.get(ACTION_URL).mock(return_value=httpx.Response(500, text="server error"))
    result = poll_action(http_client, SERVER_ID, 42, timeout_seconds=10.0)
    assert isinstance(result, Err)


# ---------------------------------------------------------------------------
# poll_action — missing 'action' key
# ---------------------------------------------------------------------------


@respx.mock
def test_poll_action_missing_key(http_client: httpx.Client) -> None:
    respx.get(ACTION_URL).mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    result = poll_action(http_client, SERVER_ID, 42, timeout_seconds=10.0)
    assert isinstance(result, Err)
    assert "action" in result.error.lower()
