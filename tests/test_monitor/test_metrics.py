"""Tests for overseer.monitor.metrics."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from overseer.monitor.metrics import check_bl_metrics, check_bl_threshold_alerts
from overseer.types import AlertTier, Err, Ok

# ---------------------------------------------------------------------------
# check_bl_metrics
# ---------------------------------------------------------------------------


def test_check_bl_metrics_normal_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Normal CPU and memory → no signals."""
    sample = {"server_id": 1, "average_cpu": 20.0, "average_memory": 50.0}
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_metrics",
        lambda *a, **kw: Ok(sample),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_metrics(client, server_id=1)
    assert signals == []


def test_check_bl_metrics_high_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU > 90% → YELLOW signal."""
    sample = {"server_id": 1, "average_cpu": 95.5, "average_memory": 40.0}
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_metrics",
        lambda *a, **kw: Ok(sample),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_metrics(client, server_id=1)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "CPU" in signals[0].message
    assert "95.5" in signals[0].message


def test_check_bl_metrics_high_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Memory > 95% → YELLOW signal."""
    sample = {"server_id": 1, "average_cpu": 30.0, "average_memory": 97.2}
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_metrics",
        lambda *a, **kw: Ok(sample),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_metrics(client, server_id=1)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "Memory" in signals[0].message
    assert "97.2" in signals[0].message


def test_check_bl_metrics_both_high(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both CPU and memory high → two YELLOW signals."""
    sample = {"server_id": 1, "average_cpu": 91.0, "average_memory": 96.0}
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_metrics",
        lambda *a, **kw: Ok(sample),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_metrics(client, server_id=1)
    assert len(signals) == 2
    assert all(s.tier == AlertTier.YELLOW for s in signals)


def test_check_bl_metrics_api_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """API error → single YELLOW 'metrics unavailable' signal."""
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_metrics",
        lambda *a, **kw: Err("connection refused", source="binarylane"),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_metrics(client, server_id=1)
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "unavailable" in signals[0].message


def test_check_bl_metrics_at_threshold_not_triggered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly at threshold (90.0 CPU, 95.0 memory) — not exceeded → no signals."""
    sample = {"server_id": 1, "average_cpu": 90.0, "average_memory": 95.0}
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_metrics",
        lambda *a, **kw: Ok(sample),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_metrics(client, server_id=1)
    assert signals == []


def test_check_bl_metrics_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Metrics dict without cpu/memory keys → no signals (safe default)."""
    sample = {"server_id": 1}
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_metrics",
        lambda *a, **kw: Ok(sample),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_metrics(client, server_id=1)
    assert signals == []


# ---------------------------------------------------------------------------
# check_bl_threshold_alerts
# ---------------------------------------------------------------------------


def test_check_bl_threshold_alerts_with_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each BL alert maps to a YELLOW signal."""
    alerts = [
        {"id": 1, "type": "cpu", "threshold": 90},
        {"id": 2, "type": "memory", "threshold": 85},
    ]
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_threshold_alerts",
        lambda *a, **kw: Ok(alerts),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_threshold_alerts(client)
    assert len(signals) == 2
    assert all(s.tier == AlertTier.YELLOW for s in signals)
    assert any("cpu" in s.message for s in signals)
    assert any("memory" in s.message for s in signals)


def test_check_bl_threshold_alerts_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No alerts configured → empty list."""
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_threshold_alerts",
        lambda *a, **kw: Ok([]),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_threshold_alerts(client)
    assert signals == []


def test_check_bl_threshold_alerts_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """API error → empty list (silent fail, not a crash)."""
    monkeypatch.setattr(
        "overseer.monitor.metrics.get_threshold_alerts",
        lambda *a, **kw: Err("service unavailable", source="binarylane"),
    )
    client = MagicMock(spec=httpx.Client)
    signals = check_bl_threshold_alerts(client)
    assert signals == []
