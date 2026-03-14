"""Tests for overseer.monitor.connections."""

from __future__ import annotations

import pytest

from overseer.monitor.connections import (
    check_connections,
    evaluate_sustained_unknowns,
    parse_ss_output,
)
from overseer.types import AlertTier, Err, Ok

# ---------------------------------------------------------------------------
# parse_ss_output
# ---------------------------------------------------------------------------

SAMPLE_SS_OUTPUT = """\
ESTAB 0 0 10.0.0.1:54321 93.184.216.34:443 users:(("python3",pid=1234,fd=5))
ESTAB 0 0 10.0.0.1:54322 1.2.3.4:80 users:(("curl",pid=5678,fd=3))
ESTAB 0 0 10.0.0.1:54323 2001:db8::1:443
"""


def test_parse_ss_output_basic() -> None:
    connections = parse_ss_output(SAMPLE_SS_OUTPUT)
    assert len(connections) == 3


def test_parse_ss_output_fields() -> None:
    connections = parse_ss_output(SAMPLE_SS_OUTPUT)
    first = connections[0]
    assert first.remote_host == "93.184.216.34"
    assert first.remote_port == 443
    assert first.process == "python3"
    assert first.local_addr == "10.0.0.1:54321"


def test_parse_ss_output_second_entry() -> None:
    connections = parse_ss_output(SAMPLE_SS_OUTPUT)
    second = connections[1]
    assert second.remote_host == "1.2.3.4"
    assert second.remote_port == 80
    assert second.process == "curl"


def test_parse_ss_output_no_process() -> None:
    """Lines without users:() info should still parse with empty process."""
    connections = parse_ss_output(SAMPLE_SS_OUTPUT)
    third = connections[2]
    assert third.remote_host == "2001:db8::1"
    assert third.remote_port == 443
    assert third.process == ""


def test_parse_ss_output_empty() -> None:
    assert parse_ss_output("") == []


def test_parse_ss_output_ignores_malformed_lines() -> None:
    bad_input = "this is not valid ss output\nESTAB 0 0 10.0.0.1:1234 8.8.8.8:53\n"
    connections = parse_ss_output(bad_input)
    # Only the valid ESTAB line should parse.
    assert len(connections) == 1
    assert connections[0].remote_host == "8.8.8.8"


# ---------------------------------------------------------------------------
# evaluate_sustained_unknowns
# ---------------------------------------------------------------------------


def test_evaluate_sustained_unknowns_below_threshold() -> None:
    assert evaluate_sustained_unknowns(unknown_count=2, threshold=3) is None


def test_evaluate_sustained_unknowns_at_threshold() -> None:
    assert evaluate_sustained_unknowns(unknown_count=3, threshold=3) == AlertTier.ORANGE


def test_evaluate_sustained_unknowns_above_threshold() -> None:
    assert evaluate_sustained_unknowns(unknown_count=5, threshold=3) == AlertTier.ORANGE


def test_evaluate_sustained_unknowns_zero() -> None:
    assert evaluate_sustained_unknowns(unknown_count=0, threshold=3) is None


# ---------------------------------------------------------------------------
# check_connections (monkeypatched SSH)
# ---------------------------------------------------------------------------


def test_check_connections_all_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    ss_output = (
        "ESTAB 0 0 10.0.0.1:1234 8.8.8.8:53 users:((\"systemd\",pid=1,fd=10))\n"
    )
    monkeypatch.setattr(
        "overseer.monitor.connections.run_ssh_command",
        lambda *args, **kwargs: Ok(ss_output),
    )

    result = check_connections("host", "user", allowlist=["8.8.8.8"])
    assert isinstance(result, Ok)
    assert result.value == []


def test_check_connections_unknown_host(monkeypatch: pytest.MonkeyPatch) -> None:
    ss_output = (
        "ESTAB 0 0 10.0.0.1:1234 99.99.99.99:443 users:((\"python3\",pid=99,fd=4))\n"
    )
    monkeypatch.setattr(
        "overseer.monitor.connections.run_ssh_command",
        lambda *args, **kwargs: Ok(ss_output),
    )

    result = check_connections("host", "user", allowlist=["8.8.8.8"])
    assert isinstance(result, Ok)
    signals = result.value
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "99.99.99.99" in signals[0].message


def test_check_connections_ssh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "overseer.monitor.connections.run_ssh_command",
        lambda *args, **kwargs: Err("connection refused"),
    )

    result = check_connections("host", "user", allowlist=[])
    assert isinstance(result, Err)
    assert "connection refused" in result.error


def test_check_connections_multiple_unknowns(monkeypatch: pytest.MonkeyPatch) -> None:
    ss_output = (
        "ESTAB 0 0 10.0.0.1:1 1.1.1.1:80 users:((\"curl\",pid=10,fd=1))\n"
        "ESTAB 0 0 10.0.0.1:2 2.2.2.2:443 users:((\"wget\",pid=11,fd=2))\n"
        "ESTAB 0 0 10.0.0.1:3 8.8.8.8:53 users:((\"dns\",pid=12,fd=3))\n"
    )
    monkeypatch.setattr(
        "overseer.monitor.connections.run_ssh_command",
        lambda *args, **kwargs: Ok(ss_output),
    )

    result = check_connections("host", "user", allowlist=["8.8.8.8"])
    assert isinstance(result, Ok)
    # Only the first two are unknown
    assert len(result.value) == 2
    hosts = {s.message for s in result.value}
    assert any("1.1.1.1" in m for m in hosts)
    assert any("2.2.2.2" in m for m in hosts)
