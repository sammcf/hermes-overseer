"""SMTP email alert channel."""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

from overseer.config import EmailConfig, resolve_secret
from overseer.types import AlertTier, Err, Ok, Result, Signal

_TIER_LABEL: dict[AlertTier, str] = {
    AlertTier.YELLOW: "YELLOW",
    AlertTier.ORANGE: "ORANGE",
    AlertTier.RED: "RED",
}


def format_email_body(signals: list[Signal], tier: AlertTier) -> str:
    """Format signals into a plain-text email body."""
    label = _TIER_LABEL[tier]
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"Hermes Overseer — {label} Alert",
        f"Generated: {ts}",
        "",
        f"{'Signal':<16} {'Time':<12} Message",
        "-" * 72,
    ]
    for sig in signals:
        sig_ts = sig.timestamp.strftime("%H:%M:%SZ")
        lines.append(f"{sig.source.upper():<16} {sig_ts:<12} {sig.message}")
    lines += ["", f"Tier: {label}"]
    return "\n".join(lines)


def send_email(config: EmailConfig, subject: str, body: str) -> Result[None]:
    """Connect to SMTP with STARTTLS, authenticate, and send. Returns Ok(None) or Err."""
    try:
        password = resolve_secret(config.password_env)
    except RuntimeError as exc:
        return Err(str(exc), source="email")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.from_address
    msg["To"] = config.to_address
    msg.set_content(body)

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.from_address, password)
            smtp.send_message(msg)
        return Ok(None)
    except smtplib.SMTPAuthenticationError as exc:
        return Err(f"SMTP authentication failed: {exc}", source="email")
    except (smtplib.SMTPException, OSError) as exc:
        return Err(f"SMTP error: {exc}", source="email")


def send_alert(
    config: EmailConfig, signals: list[Signal], tier: AlertTier
) -> Result[None]:
    """Format and send an email alert for the given signals and tier."""
    label = _TIER_LABEL[tier]
    subject = f"[Hermes Overseer] {label} Alert — {len(signals)} signal(s)"
    body = format_email_body(signals, tier)
    return send_email(config, subject, body)
