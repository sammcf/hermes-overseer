"""Periodic state snapshots: create, restore, and prune tar.gz archives of hermes state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from overseer.ssh import rsync_pull_file, rsync_push, run_ssh_command
from overseer.types import Err, Ok, Result

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Overridable in tests for deterministic timestamps
_now: Callable[[], datetime] = _utcnow

_EXCLUDES = [
    "hermes-agent",
    "sandboxes",
    "bin",
    "image_cache",
    "document_cache",
]


def take_snapshot(
    hostname: str,
    user: str,
    hermes_home: str,
    backup_dir: str,
) -> Result[str]:
    """SSH to VPS: create tar.gz of hermes state (excluding code). rsync it locally.

    Returns Ok(local_archive_path) on success.
    Excludes hermes-agent, sandboxes, bin, image_cache, document_cache.
    Best-effort remote cleanup after download.
    """
    timestamp = _now().strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"hermes-state-{timestamp}.tar.gz"
    remote_archive = f"/tmp/{archive_name}"
    local_archive = str(Path(backup_dir) / archive_name)

    hermes_path = Path(hermes_home)
    hermes_parent = str(hermes_path.parent)
    hermes_dir = hermes_path.name

    excludes = " ".join(f"--exclude='{hermes_dir}/{ex}'" for ex in _EXCLUDES)
    tar_cmd = f"tar czf {remote_archive} -C {hermes_parent} {excludes} {hermes_dir}/"

    tar_result = run_ssh_command(hostname, user, tar_cmd, timeout=120)
    if isinstance(tar_result, Err):
        return tar_result

    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    pull_result = rsync_pull_file(hostname, user, remote_archive, backup_dir, timeout=120)
    if isinstance(pull_result, Err):
        return pull_result

    # Best-effort cleanup
    run_ssh_command(hostname, user, f"rm -f {remote_archive}", timeout=10)

    return Ok(local_archive)


def restore_snapshot(
    hostname: str,
    user: str,
    archive_path: str,
    hermes_home: str,
) -> Result[str]:
    """Upload a snapshot archive to the VPS and extract it under hermes_home's parent.

    Fixes ownership after extraction.
    Returns Ok(archive_name) on success.
    """
    archive_name = Path(archive_path).name
    remote_archive = f"/tmp/{archive_name}"

    hermes_path = Path(hermes_home)
    hermes_parent = str(hermes_path.parent)
    hermes_dir = hermes_path.name

    push_result = rsync_push(hostname, user, archive_path, "/tmp/", timeout=120)
    if isinstance(push_result, Err):
        return push_result

    extract_cmd = (
        f"tar xzf {remote_archive} -C {hermes_parent}/"
        f" && chown -R {user}:{user} {hermes_parent}/{hermes_dir}/"
    )
    extract_result = run_ssh_command(hostname, user, extract_cmd, timeout=120)
    if isinstance(extract_result, Err):
        return extract_result

    # Best-effort cleanup
    run_ssh_command(hostname, user, f"rm -f {remote_archive}", timeout=10)

    return Ok(f"restored {archive_name}")


def find_latest_snapshot(backup_dir: str) -> str | None:
    """Return path of most recent snapshot archive, or None if none exist."""
    backup_path = Path(backup_dir)
    if not backup_path.exists():
        return None
    archives = sorted(backup_path.glob("hermes-state-*.tar.gz"))
    return str(archives[-1]) if archives else None


def prune_snapshots(backup_dir: str, retention_count: int) -> int:
    """Delete oldest snapshots beyond retention_count. Returns count deleted."""
    backup_path = Path(backup_dir)
    if not backup_path.exists():
        return 0
    archives = sorted(backup_path.glob("hermes-state-*.tar.gz"))
    to_delete = archives[:-retention_count] if retention_count > 0 else archives
    for archive in to_delete:
        archive.unlink()
    return len(to_delete)
