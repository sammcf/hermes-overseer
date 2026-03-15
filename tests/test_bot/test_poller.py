"""Tests for overseer.bot.poller."""

from __future__ import annotations

import httpx
import respx

from overseer.bot.poller import fetch_updates
from overseer.types import Err, Ok

_BASE = "https://api.telegram.org/botTOKEN/getUpdates"
_SAMPLE_UPDATES = [
    {"update_id": 1, "message": {"chat": {"id": 99}, "text": "/help"}},
    {"update_id": 2, "message": {"chat": {"id": 99}, "text": "/status"}},
]


@respx.mock
def test_fetch_updates_returns_list() -> None:
    respx.get(_BASE).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": _SAMPLE_UPDATES})
    )
    result = fetch_updates("TOKEN", offset=0)
    assert isinstance(result, Ok)
    assert len(result.value) == 2


@respx.mock
def test_fetch_updates_empty() -> None:
    respx.get(_BASE).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    result = fetch_updates("TOKEN", offset=0)
    assert isinstance(result, Ok)
    assert result.value == []


@respx.mock
def test_fetch_updates_api_error() -> None:
    respx.get(_BASE).mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Unauthorized"}
        )
    )
    result = fetch_updates("TOKEN", offset=0)
    assert isinstance(result, Err)
    assert "Unauthorized" in result.error


@respx.mock
def test_fetch_updates_timeout() -> None:
    respx.get(_BASE).mock(side_effect=httpx.TimeoutException("timed out"))
    result = fetch_updates("TOKEN", offset=0)
    assert isinstance(result, Err)
    assert "timed out" in result.error.lower()


@respx.mock
def test_fetch_updates_http_error() -> None:
    respx.get(_BASE).mock(side_effect=httpx.ConnectError("connection refused"))
    result = fetch_updates("TOKEN", offset=0)
    assert isinstance(result, Err)


@respx.mock
def test_fetch_updates_sends_offset() -> None:
    route = respx.get(_BASE).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    fetch_updates("TOKEN", offset=42)
    assert route.called
    request = route.calls[0].request
    assert b"offset=42" in request.url.query


@respx.mock
def test_fetch_updates_nonblocking() -> None:
    """Verify timeout=0 is sent so Telegram returns immediately."""
    route = respx.get(_BASE).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    fetch_updates("TOKEN", offset=0)
    request = route.calls[0].request
    assert b"timeout=0" in request.url.query
