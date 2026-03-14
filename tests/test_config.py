"""Tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from overseer.config import Config, load_config


class TestLoadConfig:
    def test_loads_example_config(self, example_config_path: Path) -> None:
        cfg = load_config(example_config_path)
        assert cfg.vps.server_id == 123456
        assert cfg.vps.tailscale_hostname == "hermes-vps"
        assert cfg.overseer.poll_interval_seconds == 120

    def test_rejects_nonexistent_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nope.yaml")

    def test_rejects_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        with pytest.raises(ValueError, match="must contain a YAML mapping"):
            load_config(empty)

    def test_rejects_missing_required_fields(self, tmp_path: Path) -> None:
        """vps and alerts are required — config without them should fail."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(yaml.dump({"overseer": {"poll_interval_seconds": 60}}))
        with pytest.raises((ValueError, Exception)):
            load_config(bad)

    def test_minimal_valid_config(self, tmp_path: Path) -> None:
        """Only required fields, everything else defaults."""
        minimal = {
            "vps": {"server_id": 1, "tailscale_hostname": "test-vps"},
            "alerts": {
                "telegram": {"chat_id": "123"},
                "email": {
                    "from_address": "a@b.com",
                    "to_address": "c@d.com",
                },
            },
        }
        path = tmp_path / "minimal.yaml"
        path.write_text(yaml.dump(minimal))
        cfg = load_config(path)
        assert cfg.vps.server_id == 1
        assert cfg.overseer.poll_interval_seconds == 120  # default
        assert cfg.binarylane.max_retries == 5  # default

    def test_overrides_defaults(self, tmp_path: Path) -> None:
        data = {
            "overseer": {"poll_interval_seconds": 60},
            "vps": {"server_id": 99, "tailscale_hostname": "custom"},
            "binarylane": {"max_retries": 10},
            "alerts": {
                "telegram": {"chat_id": "999"},
                "email": {"from_address": "x@y.com", "to_address": "z@w.com"},
            },
        }
        path = tmp_path / "custom.yaml"
        path.write_text(yaml.dump(data))
        cfg = load_config(path)
        assert cfg.overseer.poll_interval_seconds == 60
        assert cfg.binarylane.max_retries == 10


class TestConfigFrozen:
    def test_config_is_immutable(self, example_config: Config) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            example_config.overseer.poll_interval_seconds = 999  # type: ignore[misc]

    def test_vps_is_immutable(self, example_config: Config) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            example_config.vps.server_id = 999  # type: ignore[misc]


class TestMonitorConfig:
    def test_watched_files_populated(self, example_config: Config) -> None:
        wf = example_config.monitor.watched_files
        assert ".env" in wf.orange_on_any_diff
        assert "SOUL.md" in wf.orange_on_suspicious_diff
        assert "cron/jobs.json" in wf.yellow_on_any_diff

    def test_connection_allowlist_populated(self, example_config: Config) -> None:
        al = example_config.monitor.connection_allowlist
        assert "api.openrouter.ai" in al
        assert "api.telegram.org" in al


class TestCostConfig:
    def test_provider_types(self, example_config: Config) -> None:
        providers = example_config.cost.providers
        assert providers["openrouter"].type == "prepaid_wallet"
        assert providers["anthropic"].type == "rolling_window"

    def test_dispatch_policy_fields(self, example_config: Config) -> None:
        assert "model.default" in example_config.cost.dispatch_policy_fields


class TestResponseConfig:
    def test_tier_actions(self, example_config: Config) -> None:
        assert example_config.response.yellow.actions == ["alert"]
        assert "power_off" in example_config.response.orange.actions
        assert "rebuild" in example_config.response.red.actions
