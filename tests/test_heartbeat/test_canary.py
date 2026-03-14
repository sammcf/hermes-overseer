"""Tests for overseer.heartbeat.canary."""

from __future__ import annotations

import time

from overseer.heartbeat.canary import check_canary_stale, touch_canary
from overseer.types import Err, Ok

# ---------------------------------------------------------------------------
# touch_canary
# ---------------------------------------------------------------------------


def test_touch_canary_success(monkeypatch):
    monkeypatch.setattr(
        "overseer.heartbeat.canary.run_ssh_command",
        lambda hostname, user, command, **kw: Ok(""),
    )
    result = touch_canary("vps.local", "hermes")
    assert isinstance(result, Ok)
    assert result.value == "touched"


def test_touch_canary_ssh_failure(monkeypatch):
    monkeypatch.setattr(
        "overseer.heartbeat.canary.run_ssh_command",
        lambda hostname, user, command, **kw: Err("Connection refused", source="ssh"),
    )
    result = touch_canary("vps.local", "hermes")
    assert isinstance(result, Err)
    assert "Connection refused" in result.error


def test_touch_canary_custom_path(monkeypatch):
    captured = {}

    def fake_ssh(hostname, user, command, **kw):
        captured["command"] = command
        return Ok("")

    monkeypatch.setattr("overseer.heartbeat.canary.run_ssh_command", fake_ssh)
    touch_canary("vps.local", "hermes", canary_path="/var/run/my-canary")
    assert captured["command"] == "touch /var/run/my-canary"


# ---------------------------------------------------------------------------
# check_canary_stale
# ---------------------------------------------------------------------------


def test_check_canary_fresh(monkeypatch):
    """File touched 10 seconds ago with 60s threshold → not stale."""
    now = int(time.time())
    mtime = now - 10

    monkeypatch.setattr(
        "overseer.heartbeat.canary.run_ssh_command",
        lambda *a, **kw: Ok(f"{mtime}\n"),
    )
    monkeypatch.setattr("overseer.heartbeat.canary.time", _make_fake_time(now))

    result = check_canary_stale("vps.local", "hermes", threshold_seconds=60)
    assert isinstance(result, Ok)
    assert result.value is False


def test_check_canary_stale(monkeypatch):
    """File touched 120 seconds ago with 60s threshold → stale."""
    now = int(time.time())
    mtime = now - 120

    monkeypatch.setattr(
        "overseer.heartbeat.canary.run_ssh_command",
        lambda *a, **kw: Ok(f"{mtime}\n"),
    )
    monkeypatch.setattr("overseer.heartbeat.canary.time", _make_fake_time(now))

    result = check_canary_stale("vps.local", "hermes", threshold_seconds=60)
    assert isinstance(result, Ok)
    assert result.value is True


def test_check_canary_exactly_at_threshold(monkeypatch):
    """File age equals threshold → not stale (strictly greater-than)."""
    now = int(time.time())
    mtime = now - 60

    monkeypatch.setattr(
        "overseer.heartbeat.canary.run_ssh_command",
        lambda *a, **kw: Ok(f"{mtime}\n"),
    )
    monkeypatch.setattr("overseer.heartbeat.canary.time", _make_fake_time(now))

    result = check_canary_stale("vps.local", "hermes", threshold_seconds=60)
    assert isinstance(result, Ok)
    assert result.value is False


def test_check_canary_ssh_unreachable(monkeypatch):
    monkeypatch.setattr(
        "overseer.heartbeat.canary.run_ssh_command",
        lambda *a, **kw: Err("ssh: connect to host vps.local port 22: No route to host"),
    )
    result = check_canary_stale("vps.local", "hermes", threshold_seconds=60)
    assert isinstance(result, Err)
    assert "No route to host" in result.error


def test_check_canary_bad_stat_output(monkeypatch):
    monkeypatch.setattr(
        "overseer.heartbeat.canary.run_ssh_command",
        lambda *a, **kw: Ok("not-a-number\n"),
    )
    result = check_canary_stale("vps.local", "hermes", threshold_seconds=60)
    assert isinstance(result, Err)
    assert "Unexpected output" in result.error


def test_check_canary_custom_path(monkeypatch):
    captured = {}

    def fake_ssh(hostname, user, command, **kw):
        captured["command"] = command
        return Ok(f"{int(time.time()) - 10}\n")

    monkeypatch.setattr("overseer.heartbeat.canary.run_ssh_command", fake_ssh)

    check_canary_stale("vps.local", "hermes", 60, canary_path="/var/run/my-canary")
    assert "stat -c %Y /var/run/my-canary" in captured["command"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTime:
    def __init__(self, now: int) -> None:
        self._now = now

    def time(self) -> float:
        return float(self._now)


def _make_fake_time(now: int) -> _FakeTime:
    return _FakeTime(now)
