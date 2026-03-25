"""Tests for overseer.heartbeat.pulse."""

from __future__ import annotations

import httpx
import respx

from overseer.heartbeat.pulse import send_pulse
from overseer.types import Err, Ok

_BOT_TOKEN = "test-token-123"
_CHAT_ID = "-100123456"
_TELEGRAM_URL = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"


@respx.mock
async def test_send_pulse_success():
    respx.post(_TELEGRAM_URL).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )

    result = await send_pulse(_BOT_TOKEN, _CHAT_ID, "Overseer alive. VPS: OK")

    assert isinstance(result, Ok)
    assert result.value["ok"] is True
    assert result.value["result"]["message_id"] == 42


@respx.mock
async def test_send_pulse_includes_summary_in_body():
    sent_payload: dict = {}

    def capture(request, route):
        import json
        sent_payload.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    respx.post(_TELEGRAM_URL).mock(side_effect=capture)

    await send_pulse(_BOT_TOKEN, _CHAT_ID, "Last poll: 2026-03-14T12:00:00Z. VPS: OK")

    assert sent_payload["chat_id"] == _CHAT_ID
    assert "Last poll: 2026-03-14T12:00:00Z. VPS: OK" in sent_payload["text"]
    assert sent_payload["parse_mode"] == "HTML"


@respx.mock
async def test_send_pulse_telegram_api_error():
    respx.post(_TELEGRAM_URL).mock(
        return_value=httpx.Response(200, json={"ok": False, "description": "Unauthorized"})
    )

    result = await send_pulse(_BOT_TOKEN, _CHAT_ID, "status")

    assert isinstance(result, Err)
    assert "Unauthorized" in result.error
    assert result.source == "pulse"


@respx.mock
async def test_send_pulse_timeout():
    respx.post(_TELEGRAM_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    result = await send_pulse(_BOT_TOKEN, _CHAT_ID, "status")

    assert isinstance(result, Err)
    assert "timed out" in result.error
    assert result.source == "pulse"


@respx.mock
async def test_send_pulse_http_error():
    respx.post(_TELEGRAM_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    result = await send_pulse(_BOT_TOKEN, _CHAT_ID, "status")

    assert isinstance(result, Err)
    assert result.source == "pulse"


@respx.mock
async def test_send_pulse_message_contains_heartbeat_header():
    sent_text: list[str] = []

    def capture(request, route):
        import json
        sent_text.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {}})

    respx.post(_TELEGRAM_URL).mock(side_effect=capture)

    await send_pulse(_BOT_TOKEN, _CHAT_ID, "VPS: OK")

    assert sent_text
    assert "Heartbeat" in sent_text[0]
