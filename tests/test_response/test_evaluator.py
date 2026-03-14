"""Tests for overseer.response.evaluator — exhaustive coverage of evaluate()."""

from __future__ import annotations

from overseer.response.evaluator import evaluate
from overseer.types import AlertTier, Signal


def _sig(tier: AlertTier, source: str = "test") -> Signal:
    return Signal.now(source=source, tier=tier, message=f"{tier.value} signal from {source}")


Y = AlertTier.YELLOW
OR = AlertTier.ORANGE
R = AlertTier.RED


# ---------------------------------------------------------------------------
# Base cases
# ---------------------------------------------------------------------------


def test_no_signals_returns_none():
    assert evaluate([]) is None


def test_single_yellow_returns_yellow():
    assert evaluate([_sig(Y)]) == Y


def test_single_orange_returns_orange():
    assert evaluate([_sig(OR)]) == OR


def test_single_red_returns_red():
    assert evaluate([_sig(R)]) == R


# ---------------------------------------------------------------------------
# Escalation: multiple ORANGE → RED
# ---------------------------------------------------------------------------


def test_two_orange_escalates_to_red():
    assert evaluate([_sig(OR), _sig(OR)]) == R


def test_three_orange_escalates_to_red():
    assert evaluate([_sig(OR), _sig(OR), _sig(OR)]) == R


def test_one_orange_does_not_escalate():
    assert evaluate([_sig(OR)]) == OR


# ---------------------------------------------------------------------------
# RED dominates everything
# ---------------------------------------------------------------------------


def test_red_dominates_yellow():
    assert evaluate([_sig(Y), _sig(R)]) == R


def test_red_dominates_orange():
    assert evaluate([_sig(OR), _sig(R)]) == R


def test_red_dominates_mixed():
    assert evaluate([_sig(Y), _sig(OR), _sig(R)]) == R


def test_red_with_many_yellows():
    signals = [_sig(Y)] * 5 + [_sig(R)]
    assert evaluate(signals) == R


def test_red_checked_before_orange_escalation():
    """One RED + one ORANGE: result is RED (from RED signal, not escalation)."""
    assert evaluate([_sig(R), _sig(OR)]) == R


# ---------------------------------------------------------------------------
# ORANGE escalation takes priority over a single ORANGE
# ---------------------------------------------------------------------------


def test_two_orange_plus_yellow_is_red():
    assert evaluate([_sig(OR), _sig(OR), _sig(Y)]) == R


def test_two_orange_different_sources_escalates():
    assert evaluate([_sig(OR, "metrics"), _sig(OR, "connections")]) == R


# ---------------------------------------------------------------------------
# Mixed signals without RED
# ---------------------------------------------------------------------------


def test_yellow_and_orange_is_orange():
    """Single ORANGE + YELLOW should be ORANGE (not RED)."""
    assert evaluate([_sig(Y), _sig(OR)]) == OR


def test_multiple_yellows_stay_yellow():
    assert evaluate([_sig(Y), _sig(Y), _sig(Y)]) == Y


def test_yellow_only_returns_yellow():
    assert evaluate([_sig(Y)] * 10) == Y


# ---------------------------------------------------------------------------
# Order independence
# ---------------------------------------------------------------------------


def test_order_does_not_matter_red():
    fwd = evaluate([_sig(Y), _sig(OR), _sig(R)])
    rev = evaluate([_sig(R), _sig(OR), _sig(Y)])
    assert fwd == rev == R


def test_order_does_not_matter_orange_escalation():
    fwd = evaluate([_sig(Y), _sig(OR), _sig(OR)])
    rev = evaluate([_sig(OR), _sig(OR), _sig(Y)])
    assert fwd == rev == R
