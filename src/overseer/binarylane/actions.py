"""Mutating BinaryLane API actions. All functions return Result[T]."""

from __future__ import annotations

import time
from typing import Any

import httpx

from overseer.binarylane.client import api_get, api_post
from overseer.types import Err, Ok, Result

_ACTIONS_PATH = "/v2/servers/{server_id}/actions"
_ACTION_POLL_PATH = "/v2/servers/{server_id}/actions/{action_id}"
_TERMINAL_STATUSES = frozenset({"completed", "errored"})

# Seconds to sleep between poll attempts — overridable in tests
_sleep = time.sleep
_POLL_INTERVAL = 5.0


def power_off(client: httpx.Client, server_id: int) -> Result[dict[str, Any]]:
    """POST power_off action to the server."""
    return _post_action(client, server_id, {"type": "power_off"})


def power_on(client: httpx.Client, server_id: int) -> Result[dict[str, Any]]:
    """POST power_on action to the server."""
    return _post_action(client, server_id, {"type": "power_on"})


def take_backup(client: httpx.Client, server_id: int) -> Result[dict[str, Any]]:
    """POST take_backup (temporary) action to the server."""
    return _post_action(client, server_id, {"type": "take_backup", "backup_type": "temporary"})


def rebuild(
    client: httpx.Client,
    server_id: int,
    image_id: str,
    user_data: str | None = None,
) -> Result[dict[str, Any]]:
    """POST rebuild action to the server."""
    body: dict[str, Any] = {"type": "rebuild", "image": image_id}
    if user_data is not None:
        body["user_data"] = user_data
    return _post_action(client, server_id, body)


def poll_action(
    client: httpx.Client,
    server_id: int,
    action_id: int,
    timeout_seconds: float = 300.0,
) -> Result[dict[str, Any]]:
    """Poll GET /v2/servers/{server_id}/actions/{action_id} until terminal or timeout."""
    path = _ACTION_POLL_PATH.format(server_id=server_id, action_id=action_id)
    deadline = time.monotonic() + timeout_seconds

    while True:
        result = api_get(client, path)
        if isinstance(result, Err):
            return result

        action = result.value.get("action")
        if action is None:
            return Err(
                f"No 'action' key in poll response for action {action_id}",
                source="binarylane",
            )

        status = action.get("status", "")
        if status in _TERMINAL_STATUSES:
            if status == "errored":
                return Err(
                    f"Action {action_id} on server {server_id} errored: {action}",
                    source="binarylane",
                )
            return Ok(action)

        if time.monotonic() >= deadline:
            return Err(
                f"Action {action_id} on server {server_id} timed out after {timeout_seconds}s "
                f"(last status: {status!r})",
                source="binarylane",
            )

        _sleep(_POLL_INTERVAL)


def _post_action(
    client: httpx.Client,
    server_id: int,
    body: dict[str, Any],
) -> Result[dict[str, Any]]:
    path = _ACTIONS_PATH.format(server_id=server_id)
    result = api_post(client, path, body)
    if isinstance(result, Err):
        return result
    action = result.value.get("action")
    if action is None:
        return Err(
            f"No 'action' key in response for server {server_id} action {body.get('type')}",
            source="binarylane",
        )
    return Ok(action)
