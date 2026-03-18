"""Tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from overseer.config import Config, load_config


class TestLoadConfig:
    def test_loads_example_config(self, example_config_path: Path) -> None:
        cfg = load_config(example_config_path)
        assert cfg.vps.server_id == 592953
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
                "telegram": {"dm_chat_id": "123"},
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
                "telegram": {"dm_chat_id": "999"},
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
        assert example_config.response.orange.actions == ["alert"]
        assert "rebuild" in example_config.response.red.actions


class TestVpsConfigNewFields:
    def test_ssh_public_key_path_default(self, example_config: Config) -> None:
        import os

        expected = os.path.expanduser("~/.ssh/id_ed25519.pub")
        assert example_config.vps.ssh_public_key_path == expected

    def test_ssh_public_key_path_expanded(self, tmp_path: Path) -> None:
        """Tilde in ssh_public_key_path is expanded."""
        import os
        import warnings

        data = {
            "vps": {
                "server_id": 1,
                "tailscale_hostname": "test",
                "ssh_public_key_path": "~/custom/key.pub",
            },
            "alerts": {
                "telegram": {"dm_chat_id": "123"},
                "email": {"from_address": "a@b.com", "to_address": "c@d.com"},
            },
        }
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.dump(data))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cfg = load_config(path)
        assert cfg.vps.ssh_public_key_path == os.path.expanduser("~/custom/key.pub")
        assert "~" not in cfg.vps.ssh_public_key_path


class TestHermesSecretsConfig:
    def test_default_mapping(self, example_config: Config) -> None:
        mapping = example_config.hermes_secrets.env_mapping
        assert "OPENROUTER_API_KEY" in mapping
        assert "TELEGRAM_BOT_TOKEN" in mapping
        assert "TELEGRAM_ALLOWED_USERS" in mapping

    def test_custom_mapping(self, tmp_path: Path) -> None:
        import warnings

        data = {
            "vps": {"server_id": 1, "tailscale_hostname": "test"},
            "alerts": {
                "telegram": {"dm_chat_id": "123"},
                "email": {"from_address": "a@b.com", "to_address": "c@d.com"},
            },
            "hermes_secrets": {
                "env_mapping": {"CUSTOM_KEY": "MY_ENV_VAR"},
            },
        }
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.dump(data))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cfg = load_config(path)
        assert cfg.hermes_secrets.env_mapping == {"CUSTOM_KEY": "MY_ENV_VAR"}

    def test_hermes_secrets_is_frozen(self, example_config: Config) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            example_config.hermes_secrets.env_mapping = {}  # type: ignore[misc]


class TestFileSecretsConfig:
    def test_default_file_secrets_include_google_oauth_files(
        self, example_config: Config
    ) -> None:
        """Google OAuth files are included in default file_secrets mapping."""
        fs = example_config.hermes_secrets.file_secrets
        assert "google_token.json" in fs
        assert "google_client_secret.json" in fs

    def test_file_secrets_is_frozen(self, example_config: Config) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            example_config.hermes_secrets.file_secrets = {}  # type: ignore[misc]

    def test_custom_file_secrets(self, tmp_path: Path) -> None:
        """file_secrets can be overridden in config."""
        import warnings

        data = {
            "vps": {"server_id": 1, "tailscale_hostname": "test"},
            "alerts": {
                "telegram": {"dm_chat_id": "123"},
                "email": {"from_address": "a@b.com", "to_address": "c@d.com"},
            },
            "hermes_secrets": {
                "file_secrets": {"custom.json": "custom.json"},
            },
        }
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.dump(data))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cfg = load_config(path)
        assert cfg.hermes_secrets.file_secrets == {"custom.json": "custom.json"}


class TestBackupConfig:
    def test_backup_interval_defaults_to_4_hours(self, example_config: Config) -> None:
        """Default backup interval is 14400 seconds (4 hours)."""
        assert example_config.overseer.backup_interval_seconds == 14400

    def test_backup_retention_count_defaults_to_24(self, example_config: Config) -> None:
        """Default retention of 24 keeps ~4 days at 4-hour intervals."""
        assert example_config.overseer.backup_retention_count == 24

    def test_backup_dir_default_is_expanded(self, example_config: Config) -> None:
        """backup_dir tilde is expanded, default is under ~/.local/share."""
        import os

        assert "~" not in example_config.overseer.backup_dir
        assert os.path.expanduser("~") in example_config.overseer.backup_dir
        assert "hermes-overseer" in example_config.overseer.backup_dir

    def test_secrets_dir_default_is_expanded(self, example_config: Config) -> None:
        """secrets_dir tilde is expanded, default is under ~/.config."""
        import os

        assert "~" not in example_config.overseer.secrets_dir
        assert os.path.expanduser("~") in example_config.overseer.secrets_dir
        assert "hermes-overseer" in example_config.overseer.secrets_dir
