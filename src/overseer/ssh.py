"""SSH and rsync operations via subprocess. No paramiko."""

from __future__ import annotations

import subprocess

from overseer.types import Err, Ok, Result


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
