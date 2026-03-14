"""Tests for overseer.monitor.config_drift."""

from __future__ import annotations

from pathlib import Path

import yaml

from overseer.monitor.config_drift import check_config_drift
from overseer.types import AlertTier


def _write_yaml(path: Path, data: dict) -> str:
    path.write_text(yaml.dump(data))
    return str(path)


# ---------------------------------------------------------------------------
# No drift
# ---------------------------------------------------------------------------


def test_no_drift(tmp_path: Path) -> None:
    config = {
        "model": {"default": "claude-3-5-haiku-20241022", "provider": "anthropic"},
        "toolsets": ["core"],
        "log_level": "info",
    }
    current = _write_yaml(tmp_path / "current.yaml", config)
    canonical = _write_yaml(tmp_path / "canonical.yaml", config)
    policy_fields = ["model.default", "model.provider", "toolsets"]

    signals = check_config_drift(current, canonical, policy_fields)
    assert signals == []


# ---------------------------------------------------------------------------
# Policy field drift → ORANGE
# ---------------------------------------------------------------------------


def test_policy_field_drift_orange(tmp_path: Path) -> None:
    canonical_data = {
        "model": {"default": "claude-3-5-haiku-20241022", "provider": "anthropic"},
        "toolsets": ["core"],
    }
    current_data = {
        "model": {"default": "gpt-4o", "provider": "openai"},  # drifted
        "toolsets": ["core"],
    }
    current = _write_yaml(tmp_path / "current.yaml", current_data)
    canonical = _write_yaml(tmp_path / "canonical.yaml", canonical_data)
    policy_fields = ["model.default", "model.provider", "toolsets"]

    signals = check_config_drift(current, canonical, policy_fields)

    orange_signals = [s for s in signals if s.tier == AlertTier.ORANGE]
    assert len(orange_signals) == 2

    messages = {s.message for s in orange_signals}
    assert any("model.default" in m for m in messages)
    assert any("model.provider" in m for m in messages)


def test_policy_field_drift_source(tmp_path: Path) -> None:
    canonical_data = {"model": {"default": "haiku"}}
    current_data = {"model": {"default": "gpt-4"}}
    current = _write_yaml(tmp_path / "current.yaml", current_data)
    canonical = _write_yaml(tmp_path / "canonical.yaml", canonical_data)

    signals = check_config_drift(current, canonical, ["model.default"])

    assert all(s.source == "config_drift" for s in signals)
    assert signals[0].tier == AlertTier.ORANGE


# ---------------------------------------------------------------------------
# Non-policy drift → YELLOW
# ---------------------------------------------------------------------------


def test_non_policy_drift_yellow(tmp_path: Path) -> None:
    canonical_data = {
        "model": {"default": "haiku", "provider": "anthropic"},
        "log_level": "info",
    }
    current_data = {
        "model": {"default": "haiku", "provider": "anthropic"},
        "log_level": "debug",  # non-policy change
    }
    current = _write_yaml(tmp_path / "current.yaml", current_data)
    canonical = _write_yaml(tmp_path / "canonical.yaml", canonical_data)
    policy_fields = ["model.default", "model.provider"]

    signals = check_config_drift(current, canonical, policy_fields)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "log_level" in signals[0].message


def test_mixed_drift_orange_and_yellow(tmp_path: Path) -> None:
    canonical_data = {
        "model": {"default": "haiku", "provider": "anthropic"},
        "log_level": "info",
    }
    current_data = {
        "model": {"default": "gpt-4", "provider": "openai"},  # policy drift
        "log_level": "debug",  # non-policy drift
    }
    current = _write_yaml(tmp_path / "current.yaml", current_data)
    canonical = _write_yaml(tmp_path / "canonical.yaml", canonical_data)
    policy_fields = ["model.default", "model.provider"]

    signals = check_config_drift(current, canonical, policy_fields)

    tiers = {s.tier for s in signals}
    assert AlertTier.ORANGE in tiers
    assert AlertTier.YELLOW in tiers


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_missing_policy_field_in_current(tmp_path: Path) -> None:
    canonical_data = {"model": {"default": "haiku"}}
    current_data = {}  # policy field entirely missing
    current = _write_yaml(tmp_path / "current.yaml", current_data)
    canonical = _write_yaml(tmp_path / "canonical.yaml", canonical_data)

    signals = check_config_drift(current, canonical, ["model.default"])

    assert len(signals) >= 1
    assert any(s.tier == AlertTier.ORANGE for s in signals)


def test_extra_field_in_current(tmp_path: Path) -> None:
    canonical_data = {"model": {"default": "haiku"}}
    current_data = {"model": {"default": "haiku"}, "extra_key": "extra_value"}
    current = _write_yaml(tmp_path / "current.yaml", current_data)
    canonical = _write_yaml(tmp_path / "canonical.yaml", canonical_data)

    signals = check_config_drift(current, canonical, ["model.default"])

    # extra_key is a non-policy diff → yellow
    yellow = [s for s in signals if s.tier == AlertTier.YELLOW]
    assert len(yellow) == 1
    assert "extra_key" in yellow[0].message
