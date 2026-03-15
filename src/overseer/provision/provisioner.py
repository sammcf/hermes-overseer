"""Post-rebuild provisioning pipeline: cloud-init → rebuild → push config → start service."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from overseer.backup.snapshot import find_latest_snapshot, restore_snapshot
from overseer.binarylane.actions import poll_action, rebuild
from overseer.config import Config, resolve_secret
from overseer.provision.builder import render_cloud_init, validate_cloud_init
from overseer.ssh import push_file_content, run_ssh_command, wait_for_ssh
from overseer.tailscale import remove_devices_by_hostname
from overseer.types import Err, Ok, ProvisionResult, Result

logger = logging.getLogger(__name__)


def build_hermes_env_content(env_mapping: dict[str, str]) -> Result[str]:
    """Build KEY=VALUE .env file content from the hermes secrets mapping.

    Each entry maps a hermes env var name → an overseer env var name that holds the value.
    Returns Err if any required env var is missing.
    """
    lines: list[str] = []
    for hermes_key, overseer_env_var in sorted(env_mapping.items()):
        value = os.environ.get(overseer_env_var)
        if not value:
            return Err(
                f"Missing env var {overseer_env_var!r} (needed for hermes {hermes_key!r})",
                source="provision",
            )
        lines.append(f"{hermes_key}={value}")
    return Ok("\n".join(lines) + "\n")


def _gather_cloud_init_variables(config: Config) -> Result[dict[str, str]]:
    """Resolve all template variables needed for cloud-init rendering."""
    ssh_pubkey_path = Path(config.vps.ssh_public_key_path)
    if not ssh_pubkey_path.exists():
        return Err(
            f"SSH public key not found: {ssh_pubkey_path}",
            source="provision",
        )
    try:
        ssh_public_key = ssh_pubkey_path.read_text().strip()
    except OSError as exc:
        return Err(f"Failed to read SSH public key: {exc}", source="provision")

    try:
        ts_auth_key = resolve_secret(config.vps.tailscale_auth_key_env)
    except RuntimeError as exc:
        return Err(str(exc), source="provision")

    return Ok({
        "ssh_user": config.vps.ssh_user,
        "ssh_public_key": ssh_public_key,
        "tailscale_auth_key": ts_auth_key,
        "tailscale_hostname": config.vps.tailscale_hostname,
        "docker_image": config.vps.docker_image,
    })


def _wait_for_cloud_init(
    hostname: str,
    user: str,
    timeout: float = 600.0,
) -> Result[str]:
    """Block until cloud-init reports done on the remote host.

    SSH becomes available mid cloud-init (when Tailscale joins), but we need
    cloud-init to finish before pushing config/starting services.

    Uses `cloud-init status --wait` which blocks server-side until completion,
    avoiding polling. Timeout is generous since cloud-init includes apt, pip,
    docker pull, git clone etc.
    """
    # --wait blocks until cloud-init finishes, but exits non-zero on "degraded done"
    # (e.g. deprecation warnings in BL's default config). We only care that it finished.
    return run_ssh_command(
        hostname, user,
        "cloud-init status --wait >/dev/null 2>&1 || true; echo cloud-init-done",
        timeout=int(timeout),
    )


def provision_after_rebuild(
    config: Config,
    bl_client: httpx.Client,
) -> Result[ProvisionResult]:
    """Full provisioning pipeline: render cloud-init, rebuild, push config, start service.

    Steps 1-5 are hard dependencies - any failure stops the pipeline.
    Steps 6-8 are hard dependencies - service can't start without config/secrets.
    Steps 9-10 are best-effort - collect results but don't stop pipeline.
    """
    # --- Step 1: Gather variables & render cloud-init ---
    vars_result = _gather_cloud_init_variables(config)
    if isinstance(vars_result, Err):
        return vars_result

    rendered = render_cloud_init(vars_result.value)
    if isinstance(rendered, Err):
        return rendered

    # --- Step 2: Validate cloud-init ---
    validated = validate_cloud_init(rendered.value)
    if isinstance(validated, Err):
        return validated

    # --- Step 2b: Remove stale Tailscale devices (best-effort) ---
    ts_api_key = os.environ.get(config.vps.tailscale_api_key_env)
    if ts_api_key:
        cleanup = remove_devices_by_hostname(
            ts_api_key, config.vps.tailscale_tailnet, config.vps.tailscale_hostname
        )
        if isinstance(cleanup, Err):
            logger.warning("Tailscale device cleanup failed (continuing): %s", cleanup.error)
        else:
            logger.info("Removed %d stale Tailscale device(s)", cleanup.value)
    else:
        logger.info("No TS_API_KEY set, skipping Tailscale device cleanup")

    # --- Step 3: Rebuild via BL API with cloud-init ---
    logger.info("Starting rebuild of server %s with cloud-init", config.vps.server_id)
    rebuild_result = rebuild(
        bl_client,
        config.vps.server_id,
        image_id=config.vps.base_image_id,
        user_data=validated.value,
    )
    if isinstance(rebuild_result, Err):
        return rebuild_result

    rebuild_action: dict[str, Any] = rebuild_result.value

    # --- Step 4: Poll rebuild completion ---
    action_id = rebuild_action.get("id")
    if action_id is None:
        return Err("Rebuild response missing action id", source="provision")

    poll_result = poll_action(bl_client, config.vps.server_id, action_id, timeout_seconds=600)
    if isinstance(poll_result, Err):
        return poll_result

    # --- Step 5: Wait for Tailscale SSH ---
    logger.info("Waiting for SSH on %s", config.vps.tailscale_hostname)
    ssh_result = wait_for_ssh(config.vps.tailscale_hostname, config.vps.ssh_user, timeout=600)
    if isinstance(ssh_result, Err):
        return ssh_result

    # --- Step 5b: Wait for cloud-init to finish ---
    # SSH becomes available when Tailscale joins (mid cloud-init), but we need
    # cloud-init to complete (hermes install, systemd service file, etc.) first.
    logger.info("Waiting for cloud-init to finish on %s", config.vps.tailscale_hostname)
    ci_result = _wait_for_cloud_init(
        config.vps.tailscale_hostname, config.vps.ssh_user, timeout=600
    )
    if isinstance(ci_result, Err):
        return ci_result

    # --- Step 5c: Restore state from latest snapshot (best-effort) ---
    latest_snapshot = find_latest_snapshot(config.overseer.backup_dir)
    if latest_snapshot:
        logger.info("Restoring state from snapshot: %s", latest_snapshot)
        restore_result = restore_snapshot(
            config.vps.tailscale_hostname,
            config.vps.ssh_user,
            latest_snapshot,
            config.vps.hermes_home,
        )
        if isinstance(restore_result, Err):
            logger.warning("State restore failed (continuing): %s", restore_result.error)
        else:
            logger.info("State restore complete: %s", restore_result.value)
    else:
        logger.info("No snapshot found in %s — skipping state restore", config.overseer.backup_dir)

    # --- Step 5d: Apply local hermes-agent patches (best-effort) ---
    # Patches live in ~/.config/hermes-overseer/patches/*.patch and are applied
    # via git apply in the hermes-agent working tree after each rebuild.
    patches_dir = Path(config.overseer.patches_dir)
    if patches_dir.exists():
        for patch_file in sorted(patches_dir.glob("*.patch")):
            logger.info("Applying hermes-agent patch: %s", patch_file.name)
            try:
                patch_content = patch_file.read_text()
            except OSError as exc:
                logger.warning("Failed to read patch %s: %s", patch_file.name, exc)
                continue
            remote_patch = f"/tmp/{patch_file.name}"
            push_result = push_file_content(
                config.vps.tailscale_hostname,
                config.vps.ssh_user,
                patch_content,
                remote_patch,
                mode="0644",
            )
            if isinstance(push_result, Err):
                logger.warning(
                    "Failed to push patch %s: %s", patch_file.name, push_result.error
                )
                continue
            hermes_agent_dir = f"{config.vps.hermes_home}/hermes-agent"
            apply_result = run_ssh_command(
                config.vps.tailscale_hostname,
                config.vps.ssh_user,
                f"cd {hermes_agent_dir} && git apply --whitespace=nowarn {remote_patch}"
                f" && rm -f {remote_patch}",
                timeout=30,
            )
            if isinstance(apply_result, Err):
                logger.warning(
                    "Failed to apply patch %s: %s", patch_file.name, apply_result.error
                )
            else:
                logger.info("Applied patch: %s", patch_file.name)
    else:
        logger.debug("No patches_dir at %s, skipping patch step", config.overseer.patches_dir)

    # --- Step 6: Build hermes .env ---
    env_content_result = build_hermes_env_content(config.hermes_secrets.env_mapping)
    if isinstance(env_content_result, Err):
        return env_content_result

    # --- Step 7: Push .env ---
    env_path = f"{config.vps.hermes_home}/.env"
    env_push = push_file_content(
        config.vps.tailscale_hostname,
        config.vps.ssh_user,
        env_content_result.value,
        env_path,
        mode="0600",
    )
    if isinstance(env_push, Err):
        return env_push
    env_pushed = True

    # --- Step 8: Push config.yaml ---
    canonical_path = Path(config.cost.canonical_hermes_config)
    if not canonical_path.exists():
        return Err(
            f"Canonical hermes config not found: {canonical_path}",
            source="provision",
        )
    try:
        config_content = canonical_path.read_text()
    except OSError as exc:
        return Err(f"Failed to read canonical config: {exc}", source="provision")

    config_path = f"{config.vps.hermes_home}/config.yaml"
    config_push = push_file_content(
        config.vps.tailscale_hostname,
        config.vps.ssh_user,
        config_content,
        config_path,
        mode="0644",
    )
    if isinstance(config_push, Err):
        return config_push
    config_pushed = True

    # --- Step 8b: Push file-based secrets (Google OAuth etc.) — best-effort ---
    secrets_dir = Path(config.overseer.secrets_dir)
    for hermes_filename, overseer_filename in config.hermes_secrets.file_secrets.items():
        local_path = secrets_dir / overseer_filename
        if not local_path.exists():
            logger.info(
                "File secret %s not found in secrets_dir, skipping", overseer_filename
            )
            continue
        remote_path = f"{config.vps.hermes_home}/{hermes_filename}"
        file_push = push_file_content(
            config.vps.tailscale_hostname,
            config.vps.ssh_user,
            local_path.read_text(),
            remote_path,
            mode="0600",
        )
        if isinstance(file_push, Err):
            logger.warning("Failed to push file secret %s: %s", hermes_filename, file_push.error)
        else:
            logger.info("Pushed file secret: %s", hermes_filename)

    # --- Step 9: Start service (best-effort) ---
    start_cmd = (
        "systemctl --user daemon-reload"
        " && systemctl --user enable --now hermes-gateway.service"
    )
    start_result = run_ssh_command(
        config.vps.tailscale_hostname, config.vps.ssh_user, start_cmd, timeout=30
    )
    service_started = isinstance(start_result, Ok)
    if not service_started:
        logger.warning("Service start failed: %s", start_result)

    # --- Step 10: Verify (best-effort) ---
    if service_started:
        verify_result = run_ssh_command(
            config.vps.tailscale_hostname,
            config.vps.ssh_user,
            "systemctl --user is-active hermes-gateway.service",
            timeout=10,
        )
        if isinstance(verify_result, Ok) and "active" in verify_result.value:
            logger.info("hermes-gateway.service is active")
        else:
            logger.warning("Service verification inconclusive: %s", verify_result)
            service_started = False

    return Ok(ProvisionResult(
        rebuild_action=rebuild_action,
        config_pushed=config_pushed,
        env_pushed=env_pushed,
        service_started=service_started,
    ))
