"""Connection monitoring via SSH + ss -tnpH."""

from __future__ import annotations

import re
import socket

from overseer.ssh import run_ssh_command
from overseer.types import AlertTier, ConnectionInfo, Err, Ok, Result, Signal

# Matches lines from `ss -tnpH`, e.g.:
# ESTAB 0 0 10.0.0.1:12345 93.184.216.34:443 users:(("python3",pid=1234,fd=5))
_SS_LINE_RE = re.compile(
    r"^\S+"           # state (ESTAB, etc.)
    r"\s+\d+"         # recv-q
    r"\s+\d+"         # send-q
    r"\s+\S+"         # local address:port
    r"\s+(\S+)"       # remote address:port  — group 1
    r"(?:\s+(.*))?$"  # optional remainder (users:...)  — group 2
)

_USERS_PROC_RE = re.compile(r'users:\(\("([^"]+)"')


def _split_addr_port(addr_port: str) -> tuple[str, int]:
    """Split 'ip:port' or '[ipv6]:port' into (host, port)."""
    if addr_port.startswith("["):
        # IPv6: [::1]:443
        bracket_end = addr_port.index("]")
        host = addr_port[1:bracket_end]
        port = int(addr_port[bracket_end + 2:])
    else:
        last_colon = addr_port.rfind(":")
        host = addr_port[:last_colon]
        port = int(addr_port[last_colon + 1:])
    return host, port


def parse_ss_output(raw: str) -> list[ConnectionInfo]:
    """Parse the stdout of `ss -tnpH` into a list of ConnectionInfo objects.

    Skips lines that don't match the expected format (e.g. headers left in by
    some ss versions, or malformed lines).
    """
    connections: list[ConnectionInfo] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _SS_LINE_RE.match(line)
        if not m:
            continue
        remote_addr_port = m.group(1)
        remainder = m.group(2) or ""

        try:
            remote_host, remote_port = _split_addr_port(remote_addr_port)
        except (ValueError, IndexError):
            continue

        proc_match = _USERS_PROC_RE.search(remainder)
        process = proc_match.group(1) if proc_match else ""

        # local_addr is the 4th whitespace-separated field
        fields = line.split()
        local_addr = fields[3] if len(fields) >= 4 else ""

        connections.append(
            ConnectionInfo(
                local_addr=local_addr,
                remote_addr=remote_addr_port,
                remote_host=remote_host,
                remote_port=remote_port,
                process=process,
            )
        )
    return connections


def _is_tailscale_ip(ip: str) -> bool:
    """Check if an IP is in the Tailscale CGNAT range (100.64.0.0/10)."""
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        first, second = int(parts[0]), int(parts[1])
        return first == 100 and 64 <= second <= 127
    except (ValueError, IndexError):
        return False


def _resolve_allowlist(hostnames: list[str]) -> set[str]:
    """Resolve allowlist hostnames to a set of IPs (and keep raw entries too).

    Entries that are already IPs pass through. DNS failures are silently skipped
    (the hostname stays in the set for direct comparison).
    """
    resolved: set[str] = set(hostnames)
    for hostname in hostnames:
        try:
            infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for info in infos:
                resolved.add(info[4][0])
        except (socket.gaierror, OSError):
            pass
    return resolved


def check_connections(
    hostname: str,
    user: str,
    allowlist: list[str],
) -> Result[list[Signal]]:
    """SSH to the VPS, run `ss -tnpH`, and flag any connections to unknown hosts.

    Returns Ok(list[Signal]) where each signal has tier=YELLOW for an unknown host.
    Returns Err if the SSH command itself fails.
    """
    result = run_ssh_command(hostname, user, "ss -tnpH")
    if isinstance(result, Err):
        return result  # propagate error

    connections = parse_ss_output(result.value)
    allowlist_ips = _resolve_allowlist(allowlist)

    signals: list[Signal] = []
    for conn in connections:
        if _is_tailscale_ip(conn.remote_host):
            continue
        if conn.remote_host not in allowlist_ips:
            signals.append(
                Signal.now(
                    source="connections",
                    tier=AlertTier.YELLOW,
                    message=(
                        f"Unknown outbound connection to {conn.remote_host}:{conn.remote_port}"
                        f" from process '{conn.process}'"
                    ),
                )
            )
    return Ok(signals)


def evaluate_sustained_unknowns(
    unknown_count: int,
    threshold: int,
) -> AlertTier | None:
    """Escalate to ORANGE if sustained unknown connections meet or exceed threshold.

    Returns None if below threshold.
    """
    if unknown_count >= threshold:
        return AlertTier.ORANGE
    return None
