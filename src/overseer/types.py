"""Frozen domain types. All modules communicate through these."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class AlertTier(Enum):
    """Three-tier response model. Ordered by severity."""

    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, AlertTier):
            return NotImplemented
        order = {AlertTier.YELLOW: 0, AlertTier.ORANGE: 1, AlertTier.RED: 2}
        return order[self] >= order[other]

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, AlertTier):
            return NotImplemented
        order = {AlertTier.YELLOW: 0, AlertTier.ORANGE: 1, AlertTier.RED: 2}
        return order[self] > order[other]

    def __le__(self, other: object) -> bool:
        if not isinstance(other, AlertTier):
            return NotImplemented
        order = {AlertTier.YELLOW: 0, AlertTier.ORANGE: 1, AlertTier.RED: 2}
        return order[self] <= order[other]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, AlertTier):
            return NotImplemented
        order = {AlertTier.YELLOW: 0, AlertTier.ORANGE: 1, AlertTier.RED: 2}
        return order[self] < order[other]


@dataclass(frozen=True)
class Signal:
    """A single monitoring observation that may require a response."""

    source: str  # e.g. "metrics", "connections", "config_drift", "cost"
    tier: AlertTier
    message: str
    timestamp: datetime

    @staticmethod
    def now(source: str, tier: AlertTier, message: str) -> Signal:
        return Signal(
            source=source,
            tier=tier,
            message=message,
            timestamp=datetime.now(UTC),
        )


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err:
    error: str
    source: str = ""


Result = Ok[T] | Err
"""Railway-style result type. Functions return Ok(value) or Err(message)."""


@dataclass(frozen=True)
class DiffResult:
    """Result of diffing a monitored file against its last-known-good copy."""

    file_path: str
    changed: bool
    diff_content: str  # unified diff, empty if unchanged
    tier: AlertTier | None  # None if unchanged


@dataclass(frozen=True)
class ConnectionInfo:
    """A parsed outbound connection from ss -tnp."""

    local_addr: str
    remote_addr: str
    remote_host: str  # resolved hostname or raw IP
    remote_port: int
    process: str


@dataclass(frozen=True)
class PollState:
    """Minimal mutable state carried between poll cycles."""

    sustained_unknown_count: int = 0
    last_poll_time: datetime | None = None
