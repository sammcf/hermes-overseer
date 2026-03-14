"""End-to-end pipeline tests for overseer.monitor.pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from overseer.monitor.pipeline import run_poll_cycle, run_response_cycle
from overseer.types import AlertTier, Err, Ok, PollState, Signal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config(example_config: any) -> any:  # type: ignore[valid-type]
    """Reuse the example config loaded by the shared conftest fixture."""
    return example_config


@pytest.fixture()
def bl_client() -> httpx.Client:
    return MagicMock(spec=httpx.Client)


@pytest.fixture()
def fresh_state() -> PollState:
    return PollState()


def _make_signal(source: str = "test", tier: AlertTier = AlertTier.YELLOW) -> Signal:
    return Signal.now(source=source, tier=tier, message=f"test signal from {source}")


# ---------------------------------------------------------------------------
# Helpers to build targeted monkeypatches
# ---------------------------------------------------------------------------


def _patch_all_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch every sub-check to return clean (no signals)."""
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_bl_metrics",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_bl_threshold_alerts",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.pull_watched_files",
        lambda *a, **kw: Ok("ok"),
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.evaluate_file_changes",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_connections",
        lambda *a, **kw: Ok([]),
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_config_drift",
        lambda *a, **kw: [],
    )


# ---------------------------------------------------------------------------
# run_poll_cycle — no issues
# ---------------------------------------------------------------------------


def test_run_poll_cycle_clean(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """All monitors clean → empty signals, state updated."""
    _patch_all_clean(monkeypatch)

    signals, new_state = run_poll_cycle(config, bl_client, fresh_state)

    assert signals == []
    assert new_state.sustained_unknown_count == 0
    assert new_state.last_poll_time is not None
    assert new_state.last_poll_time.tzinfo is not None


def test_run_poll_cycle_updates_last_poll_time(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """last_poll_time is always updated even when there are no signals."""
    _patch_all_clean(monkeypatch)
    before = datetime.now(UTC)

    _, new_state = run_poll_cycle(config, bl_client, fresh_state)

    assert new_state.last_poll_time is not None
    assert new_state.last_poll_time >= before


# ---------------------------------------------------------------------------
# run_poll_cycle — sustained unknown connections
# ---------------------------------------------------------------------------


def test_run_poll_cycle_connection_unknowns_increments_count(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """Unknown connections increment sustained_unknown_count."""
    _patch_all_clean(monkeypatch)
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_connections",
        lambda *a, **kw: Ok([_make_signal("connections")]),
    )

    signals, new_state = run_poll_cycle(config, bl_client, fresh_state)

    assert new_state.sustained_unknown_count == 1
    # The YELLOW connection signal itself is present; no ORANGE yet (count < threshold=3)
    conn_signals = [s for s in signals if s.source == "connections"]
    assert any(s.tier == AlertTier.YELLOW for s in conn_signals)
    assert not any(s.tier == AlertTier.ORANGE for s in conn_signals)


def test_run_poll_cycle_connection_unknowns_resets_on_clean(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
) -> None:
    """A clean connections result resets sustained_unknown_count to 0."""
    _patch_all_clean(monkeypatch)
    prior_state = PollState(sustained_unknown_count=2)

    _, new_state = run_poll_cycle(config, bl_client, prior_state)

    assert new_state.sustained_unknown_count == 0


def test_run_poll_cycle_sustained_unknowns_trigger_orange(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
) -> None:
    """After sustained_unknown_threshold consecutive dirty cycles, an ORANGE signal is emitted."""
    _patch_all_clean(monkeypatch)
    # threshold=3; prior count=2 means this cycle takes it to 3 → ORANGE
    prior_state = PollState(sustained_unknown_count=2)
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_connections",
        lambda *a, **kw: Ok([_make_signal("connections")]),
    )

    signals, new_state = run_poll_cycle(config, bl_client, prior_state)

    assert new_state.sustained_unknown_count == 3
    orange_signals = [
        s for s in signals
        if s.tier == AlertTier.ORANGE and s.source == "connections"
    ]
    assert len(orange_signals) == 1
    assert "3" in orange_signals[0].message  # count mentioned


def test_run_poll_cycle_sustained_count_accumulates_across_cycles(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """sustained_unknown_count carries over across multiple cycles."""
    _patch_all_clean(monkeypatch)
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_connections",
        lambda *a, **kw: Ok([_make_signal("connections")]),
    )

    state = fresh_state
    for expected_count in range(1, 4):
        _, state = run_poll_cycle(config, bl_client, state)
        assert state.sustained_unknown_count == expected_count


# ---------------------------------------------------------------------------
# run_poll_cycle — propagates monitor failures as signals
# ---------------------------------------------------------------------------


def test_run_poll_cycle_metrics_exception_becomes_signal(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """An unexpected exception in check_bl_metrics is caught and emitted as a YELLOW signal."""
    _patch_all_clean(monkeypatch)
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_bl_metrics",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    signals, _ = run_poll_cycle(config, bl_client, fresh_state)

    failure_signals = [s for s in signals if "boom" in s.message]
    assert len(failure_signals) == 1
    assert failure_signals[0].tier == AlertTier.YELLOW


def test_run_poll_cycle_rsync_failure_becomes_signal(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """rsync pull failure emits a YELLOW signal rather than crashing."""
    _patch_all_clean(monkeypatch)
    monkeypatch.setattr(
        "overseer.monitor.pipeline.pull_watched_files",
        lambda *a, **kw: Err("rsync: No route to host", source="ssh"),
    )

    signals, _ = run_poll_cycle(config, bl_client, fresh_state)

    file_signals = [s for s in signals if s.source == "files"]
    assert len(file_signals) == 1
    assert file_signals[0].tier == AlertTier.YELLOW
    assert "rsync" in file_signals[0].message


def test_run_poll_cycle_connection_check_exception_becomes_signal(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """Exception in check_connections is caught and emitted as YELLOW."""
    _patch_all_clean(monkeypatch)
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_connections",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("ssh timeout")),
    )

    signals, _new_state = run_poll_cycle(config, bl_client, fresh_state)

    conn_signals = [s for s in signals if s.source == "connections"]
    assert any("ssh timeout" in s.message for s in conn_signals)
    assert all(s.tier == AlertTier.YELLOW for s in conn_signals)


def test_run_poll_cycle_config_drift_exception_becomes_signal(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """Exception in check_config_drift becomes a YELLOW signal."""
    _patch_all_clean(monkeypatch)
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_config_drift",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no such file")),
    )

    signals, _ = run_poll_cycle(config, bl_client, fresh_state)

    drift_signals = [s for s in signals if s.source == "config_drift"]
    assert len(drift_signals) == 1
    assert drift_signals[0].tier == AlertTier.YELLOW


def test_run_poll_cycle_collects_all_signals(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
    fresh_state: PollState,
) -> None:
    """Signals from all monitors are aggregated in one list."""
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_bl_metrics",
        lambda *a, **kw: [_make_signal("metrics")],
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_bl_threshold_alerts",
        lambda *a, **kw: [_make_signal("metrics")],
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.pull_watched_files",
        lambda *a, **kw: Ok("ok"),
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.evaluate_file_changes",
        lambda *a, **kw: [_make_signal("files")],
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_connections",
        lambda *a, **kw: Ok([_make_signal("connections")]),
    )
    monkeypatch.setattr(
        "overseer.monitor.pipeline.check_config_drift",
        lambda *a, **kw: [_make_signal("config_drift")],
    )

    signals, _ = run_poll_cycle(config, bl_client, fresh_state)

    sources = {s.source for s in signals}
    assert "metrics" in sources
    assert "files" in sources
    assert "connections" in sources
    assert "config_drift" in sources


# ---------------------------------------------------------------------------
# run_response_cycle
# ---------------------------------------------------------------------------


def test_run_response_cycle_no_signals_returns_empty(
    config: any,
    bl_client: httpx.Client,
) -> None:
    """No signals → no actions taken, empty results."""
    results = run_response_cycle([], config, bl_client)
    assert results == []


def test_run_response_cycle_yellow_signals_trigger_alert(
    monkeypatch: pytest.MonkeyPatch,
    config: any,
    bl_client: httpx.Client,
) -> None:
    """YELLOW signals → 'alert' action is attempted."""
    executed: list[str] = []

    def fake_execute_actions(actions, **kwargs):  # type: ignore[override]
        executed.extend(actions)
        return [Ok("done") for _ in actions]

    monkeypatch.setattr(
        "overseer.monitor.pipeline.execute_actions",
        fake_execute_actions,
    )

    signals = [_make_signal("metrics", AlertTier.YELLOW)]
    run_response_cycle(signals, config, bl_client)

    assert "alert" in executed
