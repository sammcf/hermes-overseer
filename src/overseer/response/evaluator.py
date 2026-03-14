"""Signal evaluation — pure function that determines the highest alert tier."""

from __future__ import annotations

from overseer.types import AlertTier, Signal


def evaluate(signals: list[Signal]) -> AlertTier | None:
    """Determine the highest response tier from a list of signals.

    Rules (in precedence order):
    - Any RED signal → RED
    - Two or more ORANGE signals → RED (escalation)
    - Any ORANGE signal → ORANGE
    - Any YELLOW signal → YELLOW
    - No signals → None
    """
    if not signals:
        return None

    tiers = [s.tier for s in signals]

    if AlertTier.RED in tiers:
        return AlertTier.RED

    orange_count = tiers.count(AlertTier.ORANGE)
    if orange_count >= 2:
        return AlertTier.RED

    if orange_count == 1:
        return AlertTier.ORANGE

    if AlertTier.YELLOW in tiers:
        return AlertTier.YELLOW

    return None
