"""Tests for alert dispatch (fan-out to all channels)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from overseer.alert import dispatch_alert
from overseer.config import AlertsConfig, EmailConfig, TelegramConfig
from overseer.types import AlertTier, Err, Ok, Signal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)

_SIGNALS = [
    Signal(source="metrics", tier=AlertTier.YELLOW, message="Disk 80% full", timestamp=_TS),
]

_ALERTS_CONFIG = AlertsConfig(
    telegram=TelegramConfig(
        bot_token_env="TEST_TG_TOKEN",
        dm_chat_id="999",
    ),
    email=EmailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        from_address="overseer@example.com",
        to_address="operator@example.com",
        password_env="TEST_EMAIL_PASSWORD",
    ),
)


# ---------------------------------------------------------------------------
# dispatch_alert — both channels called
# ---------------------------------------------------------------------------


def test_dispatch_alert_calls_both_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TG_TOKEN", "tg_token")
    monkeypatch.setenv("TEST_EMAIL_PASSWORD", "email_pass")

    tg_result = [Ok({"ok": True})]
    email_result = Ok(None)

    with (
        patch("overseer.alert.telegram_channel.send_alert", return_value=tg_result) as mock_tg,
        patch("overseer.alert.email_channel.send_alert", return_value=email_result) as mock_email,
    ):
        results = dispatch_alert(_ALERTS_CONFIG, _SIGNALS, AlertTier.YELLOW)

    mock_tg.assert_called_once_with(_ALERTS_CONFIG.telegram, _SIGNALS, AlertTier.YELLOW)
    mock_email.assert_called_once_with(_ALERTS_CONFIG.email, _SIGNALS, AlertTier.YELLOW)
    assert len(results) == 2


def test_dispatch_alert_returns_both_results(monkeypatch: pytest.MonkeyPatch) -> None:
    tg_item = Ok({"ok": True})
    tg_result = [tg_item]
    email_result = Ok(None)

    with (
        patch("overseer.alert.telegram_channel.send_alert", return_value=tg_result),
        patch("overseer.alert.email_channel.send_alert", return_value=email_result),
    ):
        results = dispatch_alert(_ALERTS_CONFIG, _SIGNALS, AlertTier.YELLOW)

    assert results[0] is tg_item
    assert results[1] is email_result


# ---------------------------------------------------------------------------
# dispatch_alert — one channel failure doesn't block the other
# ---------------------------------------------------------------------------


def test_dispatch_telegram_failure_does_not_block_email(monkeypatch: pytest.MonkeyPatch) -> None:
    tg_result = [Err("Telegram timed out", source="telegram")]
    email_result = Ok(None)

    with (
        patch("overseer.alert.telegram_channel.send_alert", return_value=tg_result),
        patch("overseer.alert.email_channel.send_alert", return_value=email_result),
    ):
        results = dispatch_alert(_ALERTS_CONFIG, _SIGNALS, AlertTier.ORANGE)

    assert isinstance(results[0], Err)
    assert isinstance(results[1], Ok)
    assert len(results) == 2


def test_dispatch_email_failure_does_not_block_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    tg_result = [Ok({"ok": True})]
    email_result = Err("SMTP connection refused", source="email")

    with (
        patch("overseer.alert.telegram_channel.send_alert", return_value=tg_result),
        patch("overseer.alert.email_channel.send_alert", return_value=email_result),
    ):
        results = dispatch_alert(_ALERTS_CONFIG, _SIGNALS, AlertTier.RED)

    assert isinstance(results[0], Ok)
    assert isinstance(results[1], Err)
    assert len(results) == 2


def test_dispatch_both_channels_fail() -> None:
    tg_result = [Err("no token", source="telegram")]
    email_result = Err("no password", source="email")

    with (
        patch("overseer.alert.telegram_channel.send_alert", return_value=tg_result),
        patch("overseer.alert.email_channel.send_alert", return_value=email_result),
    ):
        results = dispatch_alert(_ALERTS_CONFIG, _SIGNALS, AlertTier.RED)

    assert all(isinstance(r, Err) for r in results)
    assert len(results) == 2
