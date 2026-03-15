"""Tests for overseer.provision.provisioner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from overseer.config import Config, load_config
from overseer.provision.provisioner import build_hermes_env_content, provision_after_rebuild
from overseer.types import Err, Ok, ProvisionResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXAMPLE_CONFIG = Path(__file__).parent.parent.parent / "config" / "overseer.example.yaml"
_FAKE_CLOUD_INIT = (
    "#cloud-config\nusers:\n  - name: test\n"
    "packages:\n  - curl\nruncmd:\n  - echo"
)


@pytest.fixture()
def config() -> Config:
    """Load example config with warnings suppressed."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_config(EXAMPLE_CONFIG)


@pytest.fixture()
def bl_client() -> httpx.Client:
    return MagicMock(spec=httpx.Client)


# ---------------------------------------------------------------------------
# build_hermes_env_content
# ---------------------------------------------------------------------------


class TestBuildHermesEnvContent:
    def test_correct_formatting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env vars are resolved and formatted as KEY=VALUE lines."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")

        mapping = {
            "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
            "TELEGRAM_BOT_TOKEN": "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_ALLOWED_USERS": "TELEGRAM_ALLOWED_USERS",
        }
        result = build_hermes_env_content(mapping)

        assert isinstance(result, Ok)
        lines = result.value.strip().split("\n")
        assert len(lines) == 3
        # Sorted by key
        assert lines[0] == "OPENROUTER_API_KEY=sk-or-test"
        assert lines[1] == "TELEGRAM_ALLOWED_USERS=42"
        assert lines[2] == "TELEGRAM_BOT_TOKEN=123:abc"
        # Trailing newline
        assert result.value.endswith("\n")

    def test_missing_var_returns_err(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing env var returns Err with descriptive message."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")

        mapping = {
            "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
            "TELEGRAM_BOT_TOKEN": "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_ALLOWED_USERS": "TELEGRAM_ALLOWED_USERS",
        }
        result = build_hermes_env_content(mapping)

        assert isinstance(result, Err)
        assert "OPENROUTER_API_KEY" in result.error

    def test_empty_mapping_returns_empty_content(self) -> None:
        result = build_hermes_env_content({})
        assert isinstance(result, Ok)
        assert result.value == "\n"


# ---------------------------------------------------------------------------
# provision_after_rebuild — happy path
# ---------------------------------------------------------------------------


class TestProvisionHappyPath:
    def test_full_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        config: Config,
        bl_client: httpx.Client,
        tmp_path: Path,
    ) -> None:
        """All steps succeed → Ok(ProvisionResult) with all flags true."""
        # Set up SSH pubkey
        pubkey = tmp_path / "id_ed25519.pub"
        pubkey.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test@host")
        monkeypatch.setattr(
            config.vps, "__class__", type(config.vps)
        )  # frozen workaround not needed — use a fresh config
        # Rebuild config with our tmp pubkey path
        config_dict = config.model_dump()
        config_dict["vps"]["ssh_public_key_path"] = str(pubkey)
        # Point canonical config to a real file
        canonical = tmp_path / "hermes-canonical.yaml"
        canonical.write_text("model:\n  default: test\n")
        config_dict["cost"]["canonical_hermes_config"] = str(canonical)

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            test_config = Config.model_validate(config_dict)

        # Set required env vars
        monkeypatch.setenv("TS_HERMES_AUTH_KEY", "tskey-auth-test")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")

        # Mock cloud-init render/validate
        monkeypatch.setattr(
            "overseer.provision.provisioner.render_cloud_init",
            lambda vars, **kw: Ok(_FAKE_CLOUD_INIT),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.validate_cloud_init",
            lambda rendered: Ok(rendered),
        )

        # Mock BL API
        rebuild_action = {"id": 999, "status": "in-progress", "type": "rebuild"}
        monkeypatch.setattr(
            "overseer.provision.provisioner.rebuild",
            lambda client, sid, image_id, user_data=None: Ok(rebuild_action),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.poll_action",
            lambda client, sid, aid, timeout_seconds=300: Ok({"id": 999, "status": "completed"}),
        )

        # Mock SSH ops
        monkeypatch.setattr(
            "overseer.provision.provisioner.wait_for_ssh",
            lambda host, user, timeout=300: Ok(host),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.push_file_content",
            lambda host, user, content, path, mode="0600": Ok(path),
        )

        def _mock_ssh(host, user, cmd, timeout=30):
            if "cloud-init" in cmd:
                return Ok("cloud-init-done")
            return Ok("active")

        monkeypatch.setattr(
            "overseer.provision.provisioner.run_ssh_command", _mock_ssh,
        )

        result = provision_after_rebuild(test_config, bl_client)

        assert isinstance(result, Ok)
        pr = result.value
        assert isinstance(pr, ProvisionResult)
        assert pr.rebuild_action == rebuild_action
        assert pr.config_pushed is True
        assert pr.env_pushed is True
        assert pr.service_started is True


# ---------------------------------------------------------------------------
# provision_after_rebuild — failure cases
# ---------------------------------------------------------------------------


class TestProvisionFailures:
    def _make_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Config:
        """Build a test config with valid paths."""
        pubkey = tmp_path / "id_ed25519.pub"
        pubkey.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test@host")
        canonical = tmp_path / "hermes-canonical.yaml"
        canonical.write_text("model:\n  default: test\n")

        import warnings

        data = {
            "vps": {
                "server_id": 1,
                "tailscale_hostname": "test",
                "ssh_public_key_path": str(pubkey),
            },
            "cost": {"canonical_hermes_config": str(canonical)},
            "alerts": {
                "telegram": {"chat_id": "123"},
                "email": {"from_address": "a@b.com", "to_address": "c@d.com"},
            },
        }
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return Config.model_validate(data)

    def test_cloud_init_render_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Missing template var stops pipeline before API call."""
        config = self._make_config(monkeypatch, tmp_path)
        monkeypatch.setenv("TS_HERMES_AUTH_KEY", "tskey-test")

        monkeypatch.setattr(
            "overseer.provision.provisioner.render_cloud_init",
            lambda vars, **kw: Err("Missing template variable: 'ssh_user'", source="provision"),
        )

        bl_client = MagicMock(spec=httpx.Client)
        result = provision_after_rebuild(config, bl_client)

        assert isinstance(result, Err)
        assert "template variable" in result.error.lower()

    def test_rebuild_api_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """BL API 500 returns Err."""
        config = self._make_config(monkeypatch, tmp_path)
        monkeypatch.setenv("TS_HERMES_AUTH_KEY", "tskey-test")

        monkeypatch.setattr(
            "overseer.provision.provisioner.render_cloud_init",
            lambda vars, **kw: Ok(_FAKE_CLOUD_INIT),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.validate_cloud_init",
            lambda r: Ok(r),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.rebuild",
            lambda *a, **kw: Err("HTTP 500", source="binarylane"),
        )

        bl_client = MagicMock(spec=httpx.Client)
        result = provision_after_rebuild(config, bl_client)

        assert isinstance(result, Err)
        assert "500" in result.error

    def test_ssh_timeout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """wait_for_ssh never succeeds → Err."""
        config = self._make_config(monkeypatch, tmp_path)
        monkeypatch.setenv("TS_HERMES_AUTH_KEY", "tskey-test")

        monkeypatch.setattr(
            "overseer.provision.provisioner.render_cloud_init",
            lambda vars, **kw: Ok(_FAKE_CLOUD_INIT),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.validate_cloud_init",
            lambda r: Ok(r),
        )
        rebuild_action = {"id": 1, "status": "in-progress", "type": "rebuild"}
        monkeypatch.setattr(
            "overseer.provision.provisioner.rebuild",
            lambda *a, **kw: Ok(rebuild_action),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.poll_action",
            lambda *a, **kw: Ok({"id": 1, "status": "completed"}),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.wait_for_ssh",
            lambda *a, **kw: Err("SSH not available after 300s", source="ssh"),
        )

        bl_client = MagicMock(spec=httpx.Client)
        result = provision_after_rebuild(config, bl_client)

        assert isinstance(result, Err)
        assert "not available" in result.error.lower() or "ssh" in result.error.lower()

    def test_service_start_failure_partial_success(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Service start fails but pipeline returns Ok with service_started=False."""
        config = self._make_config(monkeypatch, tmp_path)
        monkeypatch.setenv("TS_HERMES_AUTH_KEY", "tskey-test")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")

        monkeypatch.setattr(
            "overseer.provision.provisioner.render_cloud_init",
            lambda vars, **kw: Ok(_FAKE_CLOUD_INIT),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.validate_cloud_init",
            lambda r: Ok(r),
        )
        rebuild_action = {"id": 1, "status": "in-progress", "type": "rebuild"}
        monkeypatch.setattr(
            "overseer.provision.provisioner.rebuild",
            lambda *a, **kw: Ok(rebuild_action),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.poll_action",
            lambda *a, **kw: Ok({"id": 1, "status": "completed"}),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.wait_for_ssh",
            lambda *a, **kw: Ok("test"),
        )
        monkeypatch.setattr(
            "overseer.provision.provisioner.push_file_content",
            lambda *a, **kw: Ok("/path"),
        )

        def mock_ssh(*args, **kwargs):
            cmd = args[2] if len(args) > 2 else kwargs.get("cmd", "")
            if "cloud-init" in cmd:
                return Ok("cloud-init-done")
            return Err("systemctl failed")

        monkeypatch.setattr(
            "overseer.provision.provisioner.run_ssh_command",
            mock_ssh,
        )

        bl_client = MagicMock(spec=httpx.Client)
        result = provision_after_rebuild(config, bl_client)

        assert isinstance(result, Ok)
        assert result.value.service_started is False
        assert result.value.config_pushed is True
        assert result.value.env_pushed is True

    def test_missing_ssh_pubkey(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Missing SSH public key file → Err before any API call."""
        import warnings

        monkeypatch.setenv("TS_HERMES_AUTH_KEY", "tskey-test")

        data = {
            "vps": {
                "server_id": 1,
                "tailscale_hostname": "test",
                "ssh_public_key_path": str(tmp_path / "nonexistent.pub"),
            },
            "alerts": {
                "telegram": {"chat_id": "123"},
                "email": {"from_address": "a@b.com", "to_address": "c@d.com"},
            },
        }
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            config = Config.model_validate(data)

        bl_client = MagicMock(spec=httpx.Client)
        result = provision_after_rebuild(config, bl_client)

        assert isinstance(result, Err)
        assert "public key" in result.error.lower()
