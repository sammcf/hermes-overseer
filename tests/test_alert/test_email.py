"""Tests for the SMTP email alert channel."""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from overseer.alert.email import format_email_body, send_email
from overseer.config import EmailConfig
from overseer.types import AlertTier, Err, Ok, Signal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)

_SIGNALS = [
    Signal(
        source="connections", tier=AlertTier.RED,
        message="Unknown outbound to 1.2.3.4", timestamp=_TS,
    ),
    Signal(
        source="config_drift", tier=AlertTier.RED,
        message=".env modified", timestamp=_TS,
    ),
]

_EMAIL_CONFIG = EmailConfig(
    smtp_host="smtp.example.com",
    smtp_port=587,
    from_address="overseer@example.com",
    to_address="operator@example.com",
    password_env="TEST_EMAIL_PASSWORD",
)


# ---------------------------------------------------------------------------
# format_email_body
# ---------------------------------------------------------------------------


def test_format_email_body_contains_tier() -> None:
    body = format_email_body(_SIGNALS, AlertTier.RED)
    assert "RED" in body


def test_format_email_body_contains_sources() -> None:
    body = format_email_body(_SIGNALS, AlertTier.RED)
    assert "CONNECTIONS" in body
    assert "CONFIG_DRIFT" in body


def test_format_email_body_contains_messages() -> None:
    body = format_email_body(_SIGNALS, AlertTier.RED)
    assert "Unknown outbound to 1.2.3.4" in body
    assert ".env modified" in body


def test_format_email_body_plain_text() -> None:
    body = format_email_body(_SIGNALS, AlertTier.YELLOW)
    # Plain text: no HTML tags
    assert "<b>" not in body
    assert "<i>" not in body


# ---------------------------------------------------------------------------
# send_email — success
# ---------------------------------------------------------------------------


def test_send_email_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_EMAIL_PASSWORD", "secret")

    mock_smtp = MagicMock()
    mock_smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

    with patch("overseer.alert.email.smtplib.SMTP", mock_smtp):
        result = send_email(_EMAIL_CONFIG, "Test Subject", "Test body")

    assert isinstance(result, Ok)
    assert result.value is None
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.login.assert_called_once_with("overseer@example.com", "secret")
    mock_smtp_instance.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# send_email — connection error → Err
# ---------------------------------------------------------------------------


def test_send_email_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_EMAIL_PASSWORD", "secret")

    with patch(
        "overseer.alert.email.smtplib.SMTP",
        side_effect=OSError("Connection refused"),
    ):
        result = send_email(_EMAIL_CONFIG, "Test Subject", "Test body")

    assert isinstance(result, Err)
    assert "Connection refused" in result.error


def test_send_email_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_EMAIL_PASSWORD", "wrong")

    mock_smtp = MagicMock()
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Bad credentials")
    mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

    with patch("overseer.alert.email.smtplib.SMTP", mock_smtp):
        result = send_email(_EMAIL_CONFIG, "Subject", "Body")

    assert isinstance(result, Err)
    assert "authentication" in result.error.lower()


def test_send_email_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_EMAIL_PASSWORD", raising=False)
    result = send_email(_EMAIL_CONFIG, "Subject", "Body")
    assert isinstance(result, Err)
    assert "TEST_EMAIL_PASSWORD" in result.error
