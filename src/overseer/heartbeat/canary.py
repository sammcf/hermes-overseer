"""Overseer → VPS heartbeat canary via SSH."""

from __future__ import annotations

import time

from overseer.ssh import run_ssh_command
from overseer.types import Err, Ok, Result


def touch_canary(
    hostname: str,
    user: str,
    canary_path: str = "/tmp/overseer-canary",
) -> Result[str]:
    """SSH to VPS and touch the canary file. Returns Ok("touched") or Err."""
    result = run_ssh_command(hostname, user, f"touch {canary_path}")
    if isinstance(result, Err):
        return result
    return Ok("touched")


def check_canary_stale(
    hostname: str,
    user: str,
    threshold_seconds: int,
    canary_path: str = "/tmp/overseer-canary",
) -> Result[bool]:
    """Check whether the canary file on VPS is stale.

    SSHes to VPS, reads the mtime of canary_path via ``stat -c %Y``, and
    compares it to the current wall-clock time.

    Returns Ok(True) if the file is older than threshold_seconds,
    Ok(False) if fresh, or Err if unreachable / stat fails.
    """
    result = run_ssh_command(hostname, user, f"stat -c %Y {canary_path}")
    if isinstance(result, Err):
        return result

    raw = result.value.strip()
    try:
        mtime = int(raw)
    except ValueError:
        return Err(
            f"Unexpected output from stat on {hostname}: {raw!r}",
            source="canary",
        )

    now = int(time.time())
    age = now - mtime
    return Ok(age > threshold_seconds)
