"""Tests for overseer.response.actions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from overseer.config import Config, ResponseConfig, TierActionConfig, load_config
from overseer.response.actions import execute_actions, get_action_sequence
from overseer.types import AlertTier, Err, Ok, Signal

Y = AlertTier.YELLOW
OR = AlertTier.ORANGE
R = AlertTier.RED


def _sig(tier: AlertTier) -> Signal:
    return Signal.now(source="test", tier=tier, message="test signal")


_DEFAULT_YELLOW = ["alert"]
_DEFAULT_ORANGE = ["power_off", "alert", "take_backup"]
_DEFAULT_RED = ["take_backup", "rebuild", "revoke_keys", "alert"]


def _config(
    yellow: list[str] | None = None,
    orange: list[str] | None = None,
    red: list[str] | None = None,
) -> ResponseConfig:
    return ResponseConfig(
        yellow=TierActionConfig(actions=_DEFAULT_YELLOW if yellow is None else yellow),
        orange=TierActionConfig(actions=_DEFAULT_ORANGE if orange is None else orange),
        red=TierActionConfig(actions=_DEFAULT_RED if red is None else red),
    )


# ---------------------------------------------------------------------------
# get_action_sequence
# ---------------------------------------------------------------------------


def test_get_action_sequence_yellow():
    cfg = _config(yellow=["alert"])
    assert get_action_sequence(Y, cfg) == ["alert"]


def test_get_action_sequence_orange():
    cfg = _config(orange=["power_off", "alert", "take_backup"])
    assert get_action_sequence(OR, cfg) == ["power_off", "alert", "take_backup"]


def test_get_action_sequence_red():
    cfg = _config(red=["take_backup", "rebuild", "revoke_keys", "alert"])
    assert get_action_sequence(R, cfg) == ["take_backup", "rebuild", "revoke_keys", "alert"]


def test_get_action_sequence_returns_copy():
    """Mutating the returned list must not affect the config."""
    cfg = _config(yellow=["alert"])
    seq = get_action_sequence(Y, cfg)
    seq.append("extra")
    assert get_action_sequence(Y, cfg) == ["alert"]


def test_get_action_sequence_empty():
    cfg = _config(yellow=[], orange=[], red=[])
    assert get_action_sequence(Y, cfg) == []
    assert get_action_sequence(OR, cfg) == []
    assert get_action_sequence(R, cfg) == []


# ---------------------------------------------------------------------------
# execute_actions: ordering and continuation on failure
# ---------------------------------------------------------------------------


async def test_execute_actions_calls_in_order():
    """Actions must be executed left-to-right; results must be in the same order."""
    call_order: list[str] = []

    async def fake_alert(alerts_config, signals, tier):
        call_order.append("alert")
        return Ok("sent")

    def fake_power_off(client, server_id):
        call_order.append("power_off")
        return Ok({"status": "completed"})

    signals = [_sig(OR)]
    with (
        patch("overseer.response.actions._action_alert", side_effect=fake_alert),
        patch("overseer.response.actions._action_power_off", side_effect=fake_power_off),
    ):
        results = await execute_actions(
            ["power_off", "alert"],
            server_id=42,
            bl_client=MagicMock(),
            alerts_config=MagicMock(),
            signals=signals,
            tier=OR,
        )

    assert call_order == ["power_off", "alert"]
    assert len(results) == 2
    assert all(isinstance(r, Ok) for r in results)


async def test_execute_actions_continues_after_failure():
    """A failing action must not prevent subsequent actions from running."""
    call_order: list[str] = []

    def fake_power_off(client, server_id):
        call_order.append("power_off")
        return Err("BL API down", source="binarylane")

    async def fake_alert(alerts_config, signals, tier):
        call_order.append("alert")
        return Ok("sent")

    with (
        patch("overseer.response.actions._action_power_off", side_effect=fake_power_off),
        patch("overseer.response.actions._action_alert", side_effect=fake_alert),
    ):
        results = await execute_actions(
            ["power_off", "alert"],
            server_id=1,
            bl_client=MagicMock(),
            alerts_config=MagicMock(),
            signals=[_sig(OR)],
            tier=OR,
        )

    assert call_order == ["power_off", "alert"]
    assert isinstance(results[0], Err)
    assert isinstance(results[1], Ok)


async def test_execute_actions_revoke_keys_placeholder():
    results = await execute_actions(
        ["revoke_keys"],
        server_id=1,
        bl_client=MagicMock(),
        alerts_config=MagicMock(),
        signals=[_sig(R)],
        tier=R,
    )
    assert len(results) == 1
    assert isinstance(results[0], Ok)
    assert "placeholder" in results[0].value.lower()


async def test_execute_actions_unknown_action_returns_err():
    results = await execute_actions(
        ["totally_unknown"],
        server_id=1,
        bl_client=MagicMock(),
        alerts_config=MagicMock(),
        signals=[_sig(Y)],
        tier=Y,
    )
    assert len(results) == 1
    assert isinstance(results[0], Err)
    assert "totally_unknown" in results[0].error


async def test_execute_actions_empty_list():
    results = await execute_actions(
        [],
        server_id=1,
        bl_client=MagicMock(),
        alerts_config=MagicMock(),
        signals=[_sig(Y)],
        tier=Y,
    )
    assert results == []


# ---------------------------------------------------------------------------
# rebuild action: config=None vs config provided
# ---------------------------------------------------------------------------

EXAMPLE_CONFIG = Path(__file__).parent.parent.parent / "config" / "overseer.example.yaml"


def _load_example_config() -> Config:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_config(EXAMPLE_CONFIG)


async def test_rebuild_without_config_calls_bare_rebuild():
    """config=None preserves the original bare rebuild behaviour."""
    called_with: list[str] = []

    def fake_rebuild(client, server_id, image_id):
        called_with.append(image_id)
        return Ok({"id": 1, "status": "completed", "type": "rebuild"})

    with patch("overseer.binarylane.actions.rebuild", fake_rebuild):
        results = await execute_actions(
            ["rebuild"],
            server_id=1,
            bl_client=MagicMock(),
            alerts_config=MagicMock(),
            signals=[_sig(R)],
            tier=R,
            config=None,
        )

    assert len(results) == 1
    assert isinstance(results[0], Ok)
    assert called_with == ["ubuntu-24.04"]


async def test_rebuild_with_config_calls_provisioner():
    """When config is provided, rebuild dispatches to provision_after_rebuild."""
    provisioner_called = False

    def fake_provision(config, bl_client):
        nonlocal provisioner_called
        provisioner_called = True
        return Ok("provisioned")

    config = _load_example_config()

    with patch(
        "overseer.provision.provisioner.provision_after_rebuild",
        fake_provision,
    ):
        results = await execute_actions(
            ["rebuild"],
            server_id=config.vps.server_id,
            bl_client=MagicMock(),
            alerts_config=MagicMock(),
            signals=[_sig(R)],
            tier=R,
            config=config,
        )

    assert provisioner_called
    assert len(results) == 1
    assert isinstance(results[0], Ok)
