"""Tests for overseer.provision.builder."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from overseer.provision.builder import render_cloud_init, validate_cloud_init
from overseer.types import Err, Ok

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_TEMPLATE = textwrap.dedent("""\
    #cloud-config
    users:
      - name: ${ssh_user}
        ssh_authorized_keys:
          - ${ssh_public_key}
    packages:
      - curl
    runcmd:
      - echo hello ${ssh_user}
      - tailscale up --auth-key=${tailscale_auth_key} --hostname=${tailscale_hostname}
""")

MINIMAL_VARS: dict[str, str] = {
    "ssh_user": "hermes",
    "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test@host",
    "tailscale_auth_key": "tskey-auth-abc123",
    "tailscale_hostname": "hermes-vps",
}


@pytest.fixture()
def template_file(tmp_path: Path) -> Path:
    """Write MINIMAL_TEMPLATE to a temp file and return its path."""
    p = tmp_path / "hermes-vps.yaml"
    p.write_text(MINIMAL_TEMPLATE)
    return p


# ---------------------------------------------------------------------------
# render_cloud_init
# ---------------------------------------------------------------------------


def test_render_substitutes_all_variables(template_file: Path) -> None:
    result = render_cloud_init(MINIMAL_VARS, template_path=str(template_file))
    assert isinstance(result, Ok)
    rendered = result.value
    assert "hermes" in rendered
    assert "tskey-auth-abc123" in rendered
    assert "hermes-vps" in rendered
    assert "${ssh_user}" not in rendered
    assert "${tailscale_auth_key}" not in rendered


def test_render_missing_template_file() -> None:
    result = render_cloud_init(MINIMAL_VARS, template_path="/nonexistent/path/template.yaml")
    assert isinstance(result, Err)
    assert "not found" in result.error.lower()


def test_render_missing_variable(template_file: Path) -> None:
    incomplete_vars = {k: v for k, v in MINIMAL_VARS.items() if k != "tailscale_auth_key"}
    result = render_cloud_init(incomplete_vars, template_path=str(template_file))
    assert isinstance(result, Err)
    assert "tailscale_auth_key" in result.error


def test_render_extra_variables_are_ignored(template_file: Path) -> None:
    """Extra variables that don't appear in the template are silently ignored."""
    vars_with_extra = {**MINIMAL_VARS, "unused_var": "some_value"}
    result = render_cloud_init(vars_with_extra, template_path=str(template_file))
    assert isinstance(result, Ok)


def test_render_default_template_path_missing(monkeypatch, tmp_path: Path) -> None:
    """Default template path is used when none is supplied; Err if not found."""
    monkeypatch.chdir(tmp_path)  # ensure cwd has no cloud-init dir
    result = render_cloud_init(MINIMAL_VARS)
    assert isinstance(result, Err)


def test_render_default_template_path_found(monkeypatch, tmp_path: Path) -> None:
    """render_cloud_init finds the template via the default path when cwd is correct."""
    cloud_init_dir = tmp_path / "cloud-init"
    cloud_init_dir.mkdir()
    (cloud_init_dir / "hermes-vps.yaml").write_text(MINIMAL_TEMPLATE)
    monkeypatch.chdir(tmp_path)
    result = render_cloud_init(MINIMAL_VARS)
    assert isinstance(result, Ok)


# ---------------------------------------------------------------------------
# validate_cloud_init
# ---------------------------------------------------------------------------


def _rendered_with(template_file: Path, vars: dict[str, str] = MINIMAL_VARS) -> str:
    result = render_cloud_init(vars, template_path=str(template_file))
    assert isinstance(result, Ok)
    return result.value


def test_validate_valid_document(template_file: Path) -> None:
    rendered = _rendered_with(template_file)
    result = validate_cloud_init(rendered)
    assert isinstance(result, Ok)
    assert result.value == rendered  # passthrough


def test_validate_invalid_yaml() -> None:
    bad_yaml = "key: [unclosed bracket"
    result = validate_cloud_init(bad_yaml)
    assert isinstance(result, Err)
    assert "yaml" in result.error.lower() or "invalid" in result.error.lower()


def test_validate_not_a_mapping() -> None:
    result = validate_cloud_init("- just\n- a\n- list\n")
    assert isinstance(result, Err)
    assert "mapping" in result.error.lower()


def test_validate_missing_users_key() -> None:
    doc = "packages:\n  - curl\nruncmd:\n  - echo hi\n"
    result = validate_cloud_init(doc)
    assert isinstance(result, Err)
    assert "users" in result.error


def test_validate_missing_packages_key() -> None:
    doc = "users:\n  - name: test\nruncmd:\n  - echo hi\n"
    result = validate_cloud_init(doc)
    assert isinstance(result, Err)
    assert "packages" in result.error


def test_validate_missing_runcmd_key() -> None:
    doc = "users:\n  - name: test\npackages:\n  - curl\n"
    result = validate_cloud_init(doc)
    assert isinstance(result, Err)
    assert "runcmd" in result.error


def test_validate_missing_multiple_keys() -> None:
    doc = "packages:\n  - curl\n"
    result = validate_cloud_init(doc)
    assert isinstance(result, Err)
    # Both missing keys should be named
    assert "users" in result.error or "runcmd" in result.error


def test_validate_extra_keys_allowed(template_file: Path) -> None:
    """Additional top-level keys beyond the required three are fine."""
    rendered = _rendered_with(template_file)
    extra = rendered + "\nwrite_files:\n  - path: /tmp/hello\n    content: world\n"
    result = validate_cloud_init(extra)
    assert isinstance(result, Ok)


# ---------------------------------------------------------------------------
# render + validate pipeline
# ---------------------------------------------------------------------------


def test_render_then_validate_real_template() -> None:
    """End-to-end: render the actual cloud-init template in the repo, then validate."""
    repo_root = Path(__file__).parent.parent.parent
    template_path = repo_root / "cloud-init" / "hermes-vps.yaml"
    if not template_path.exists():
        pytest.skip("cloud-init/hermes-vps.yaml not found in repo")

    render_result = render_cloud_init(MINIMAL_VARS, template_path=str(template_path))
    assert isinstance(render_result, Ok), f"render failed: {render_result}"

    validate_result = validate_cloud_init(render_result.value)
    assert isinstance(validate_result, Ok), f"validate failed: {validate_result}"
