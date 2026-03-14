"""BinaryLane metrics polling and threshold alert evaluation."""

from __future__ import annotations

from typing import Any

import httpx

from overseer.binarylane.queries import get_metrics, get_threshold_alerts
from overseer.types import AlertTier, Err, Signal

_CPU_THRESHOLD = 90.0
_MEMORY_THRESHOLD = 95.0


def check_bl_metrics(client: httpx.Client, server_id: int) -> list[Signal]:
    """Poll BinaryLane metrics and flag concerning resource usage.

    Returns a YELLOW signal if the API is unavailable.
    Returns YELLOW signals if CPU sustained >90% or memory >95%.
    """
    result = get_metrics(client, server_id)
    if isinstance(result, Err):
        return [Signal.now(source="metrics", tier=AlertTier.YELLOW, message="metrics unavailable")]

    metrics: dict[str, Any] = result.value
    signals: list[Signal] = []

    cpu = metrics.get("average_cpu")
    if isinstance(cpu, (int, float)) and cpu > _CPU_THRESHOLD:
        signals.append(
            Signal.now(
                source="metrics",
                tier=AlertTier.YELLOW,
                message=f"CPU sustained above {_CPU_THRESHOLD}%: current={cpu:.1f}%",
            )
        )

    memory = metrics.get("average_memory")
    if isinstance(memory, (int, float)) and memory > _MEMORY_THRESHOLD:
        signals.append(
            Signal.now(
                source="metrics",
                tier=AlertTier.YELLOW,
                message=f"Memory above {_MEMORY_THRESHOLD}%: current={memory:.1f}%",
            )
        )

    return signals


def check_bl_threshold_alerts(client: httpx.Client) -> list[Signal]:
    """Fetch BinaryLane threshold alerts and map each to a YELLOW signal.

    Returns an empty list if the API call fails or there are no alerts.
    """
    result = get_threshold_alerts(client)
    if isinstance(result, Err):
        return []

    alerts: list[dict[str, Any]] = result.value
    return [
        Signal.now(
            source="metrics",
            tier=AlertTier.YELLOW,
            message=(
                f"BinaryLane threshold alert: type={alert.get('type', 'unknown')}"
                f" threshold={alert.get('threshold', '?')}"
            ),
        )
        for alert in alerts
    ]
