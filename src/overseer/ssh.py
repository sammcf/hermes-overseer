"""SSH and rsync operations via subprocess. No paramiko."""

from __future__ import annotations

import subprocess
import time

from overseer.types import Err, Ok, Result

# Overridable in tests (same pattern as binarylane/actions.py)
_sleep = time.sleep


def run_ssh_command(
    hostname: str,
    user: str,
    command: str,
    timeout: int = 30,
) -> Result[str]:
    """Run a command on a remote host via SSH.

    Returns Ok(stdout) or Err(message) on failure/timeout.
    """
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{user}@{hostname}",
        command,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        return Ok(result.stdout)
    except subprocess.TimeoutExpired:
        return Err(f"SSH command timed out after {timeout}s: {command}")
    except subprocess.CalledProcessError as exc:
        return Err(exc.stderr or f"SSH command failed with exit code {exc.returncode}")


def rsync_pull(
    hostname: str,
    user: str,
    remote_paths: list[str],
    local_dest: str,
    timeout: int = 120,
) -> Result[str]:
    """Pull files from a remote host using rsync.

    Uses --relative so the full remote path structure is preserved under local_dest.
    Returns Ok(stdout) or Err(message) on failure/timeout.
    """
    sources = [f"{user}@{hostname}:{path}" for path in remote_paths]
    cmd = ["rsync", "-az", "--relative", *sources, local_dest]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # Exit code 23 = some files couldn't be transferred (e.g. missing on remote).
        # This is expected when monitored files don't exist yet.
        if result.returncode == 0 or result.returncode == 23:
            return Ok(result.stdout)
        return Err(result.stderr or f"rsync failed with exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        return Err(f"rsync timed out after {timeout}s")


def push_file_content(
    hostname: str,
    user: str,
    content: str,
    remote_path: str,
    mode: str = "0600",
    timeout: int = 30,
) -> Result[str]:
    """Write content to a file on a remote host via SSH.

    Creates parent directories, writes content via stdin pipe, and sets permissions.
    Returns Ok(remote_path) or Err(message) on failure.
    """
    remote_cmd = (
        f"mkdir -p $(dirname {remote_path})"
        f" && cat > {remote_path}"
        f" && chmod {mode} {remote_path}"
    )
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{user}@{hostname}",
        remote_cmd,
    ]
    try:
        result = subprocess.run(
            cmd,
            input=content.encode(),
            capture_output=True,
            text=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace") if result.stderr else ""
            return Err(
                f"push_file_content failed (exit {result.returncode}): {stderr}",
                source="ssh",
            )
        return Ok(remote_path)
    except subprocess.TimeoutExpired:
        return Err(f"push_file_content timed out after {timeout}s", source="ssh")


def rsync_push(
    hostname: str,
    user: str,
    local_path: str,
    remote_dir: str,
    timeout: int = 120,
) -> Result[str]:
    """Push a local file to a remote directory via rsync.

    Returns Ok(remote_dir) or Err on failure/timeout.
    """
    cmd = ["rsync", "-az", local_path, f"{user}@{hostname}:{remote_dir}"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return Ok(remote_dir)
        return Err(result.stderr or f"rsync failed with exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        return Err(f"rsync timed out after {timeout}s")


def rsync_pull_file(
    hostname: str,
    user: str,
    remote_path: str,
    local_dir: str,
    timeout: int = 120,
) -> Result[str]:
    """Pull a single file from a remote host into a local directory.

    Unlike rsync_pull (which uses --relative for monitored file diffs), this
    places the file flat in local_dir.
    Returns Ok(local_dir) or Err on failure/timeout.
    """
    cmd = ["rsync", "-az", f"{user}@{hostname}:{remote_path}", local_dir]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return Ok(local_dir)
        return Err(result.stderr or f"rsync failed with exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        return Err(f"rsync timed out after {timeout}s")


def wait_for_ssh(
    hostname: str,
    user: str,
    timeout: float = 300.0,
    poll_interval: float = 10.0,
) -> Result[str]:
    """Poll SSH connectivity until the host responds or timeout expires.

    Clears stale known_hosts entries first (host key changes on rebuild).
    Uses time.monotonic() deadline (same pattern as binarylane poll_action).
    Returns Ok(hostname) on success, Err on timeout.
    """
    # Clear stale host keys (rebuild changes the host key)
    subprocess.run(
        ["ssh-keygen", "-R", hostname],
        capture_output=True,
        timeout=5,
    )

    deadline = time.monotonic() + timeout

    while True:
        result = run_ssh_command(hostname, user, "true", timeout=5)
        if isinstance(result, Ok):
            return Ok(hostname)

        if time.monotonic() >= deadline:
            return Err(
                f"SSH to {user}@{hostname} not available after {timeout}s",
                source="ssh",
            )

        _sleep(poll_interval)
