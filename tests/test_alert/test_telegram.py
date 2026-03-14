"""Tests for the Telegram alert channel."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from overseer.alert.telegram import format_alert, send_telegram
from overseer.types import AlertTier, Err, Ok, Signal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)

_SIGNALS = [
    Signal(source="metrics", tier=AlertTier.ORANGE, message="CPU above 90%", timestamp=_TS),
    Signal(source="cost", tier=AlertTier.ORANGE, message="OpenRouter wallet < $5", timestamp=_TS),
]


# ---------------------------------------------------------------------------
# format_alert
# ---------------------------------------------------------------------------


def test_format_alert_contains_tier_label() -> None:
    text = format_alert(_SIGNALS, AlertTier.ORANGE)
    assert "ORANGE" in text


def test_format_alert_contains_all_sources() -> None:
    text = format_alert(_SIGNALS, AlertTier.ORANGE)
    assert "METRICS" in text
    assert "COST" in text


def test_format_alert_contains_messages() -> None:
    text = format_alert(_SIGNALS, AlertTier.ORANGE)
    assert "CPU above 90%" in text
    assert "OpenRouter wallet < $5" in text


def test_format_alert_html_tags() -> None:
    text = format_alert(_SIGNALS, AlertTier.RED)
    assert "<b>" in text
    assert "<i>" in text


def test_format_alert_yellow_tier() -> None:
    sig = Signal(source="config", tier=AlertTier.YELLOW, message="Minor drift", timestamp=_TS)
    text = format_alert([sig], AlertTier.YELLOW)
    assert "YELLOW" in text


# ---------------------------------------------------------------------------
# send_telegram — success
# ---------------------------------------------------------------------------


@respx.mock
def test_send_telegram_success() -> None:
    respx.post("https://api.telegram.org/botTOKEN123/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )
    result = send_telegram("TOKEN123", "CHAT_ID", "hello")
    assert isinstance(result, Ok)
    assert result.value["ok"] is True


# ---------------------------------------------------------------------------
# send_telegram — API-level error
# ---------------------------------------------------------------------------


@respx.mock
def test_send_telegram_api_error() -> None:
    respx.post("https://api.telegram.org/botBAD/sendMessage").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        )
    )
    result = send_telegram("BAD", "CHAT_ID", "hello")
    assert isinstance(result, Err)
    assert "chat not found" in result.error


@respx.mock
def test_send_telegram_timeout() -> None:
    respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    result = send_telegram("TOKEN", "CHAT_ID", "hello")
    assert isinstance(result, Err)
    assert "timed out" in result.error.lower()


@respx.mock
def test_send_telegram_http_error() -> None:
    respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = send_telegram("TOKEN", "CHAT_ID", "hello")
    assert isinstance(result, Err)
