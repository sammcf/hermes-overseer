"""Tests for overseer.monitor.cost."""

from __future__ import annotations

import httpx
import respx

from overseer.config import CostConfig, ProviderCostConfig
from overseer.monitor.cost import (
    check_all_providers,
    check_openrouter_balance,
    check_rolling_window_usage,
)
from overseer.types import AlertTier

_KEY_URL = "https://openrouter.ai/api/v1/auth/key"


def _balance_response(usage: float, limit: float | None) -> dict:
    data: dict = {"usage": usage}
    if limit is not None:
        data["limit"] = limit
    return {"data": data}


def _provider_config(
    *,
    yellow: float | None = None,
    orange: float | None = None,
) -> ProviderCostConfig:
    return ProviderCostConfig(
        type="prepaid_wallet",
        yellow_remaining_usd=yellow,
        orange_remaining_usd=orange,
    )


# ---------------------------------------------------------------------------
# check_openrouter_balance
# ---------------------------------------------------------------------------


@respx.mock
def test_openrouter_plenty_of_balance_no_signals() -> None:
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=5.0, limit=100.0))
    )
    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    # remaining = 95.0 — well above both thresholds
    assert signals == []


@respx.mock
def test_openrouter_at_yellow_threshold() -> None:
    # remaining = 10.0, exactly at yellow threshold
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=90.0, limit=100.0))
    )
    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert signals[0].source == "cost"
    assert "10.00" in signals[0].message


@respx.mock
def test_openrouter_below_yellow_threshold() -> None:
    # remaining = 8.0 — below yellow=10.0 but above orange=5.0
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=92.0, limit=100.0))
    )
    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW


@respx.mock
def test_openrouter_at_orange_threshold() -> None:
    # remaining = 5.0, exactly at orange threshold
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=95.0, limit=100.0))
    )
    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.ORANGE
    assert signals[0].source == "cost"
    assert "5.00" in signals[0].message


@respx.mock
def test_openrouter_below_orange_threshold() -> None:
    # remaining = 2.0 — below orange=5.0
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=98.0, limit=100.0))
    )
    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.ORANGE


@respx.mock
def test_openrouter_orange_takes_precedence_over_yellow() -> None:
    """When remaining is below both thresholds, ORANGE is returned (not YELLOW)."""
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=98.0, limit=100.0))
    )
    # remaining = 2.0 — below both yellow=10 and orange=5
    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.ORANGE


@respx.mock
def test_openrouter_no_limit_configured_no_signals() -> None:
    """If the key has no budget cap, there's nothing to check."""
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=50.0, limit=None))
    )
    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert signals == []


@respx.mock
def test_openrouter_api_error_yields_yellow_fallback() -> None:
    respx.get(_KEY_URL).mock(return_value=httpx.Response(500))

    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "openrouter balance check failed" in signals[0].message


@respx.mock
def test_openrouter_network_error_yields_yellow_fallback() -> None:
    respx.get(_KEY_URL).mock(side_effect=httpx.ConnectError("timeout"))

    cfg = _provider_config(yellow=10.0, orange=5.0)

    with httpx.Client() as client:
        signals = check_openrouter_balance(client, "sk-test", cfg)

    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    assert "openrouter balance check failed" in signals[0].message


# ---------------------------------------------------------------------------
# check_rolling_window_usage (stub)
# ---------------------------------------------------------------------------


def test_rolling_window_stub_returns_empty() -> None:
    cfg = ProviderCostConfig(
        type="rolling_window",
        yellow_percent=80,
        orange_percent=95,
    )
    with httpx.Client() as client:
        result = check_rolling_window_usage("anthropic", client, "sk-ant-test", cfg)
    assert result == []


# ---------------------------------------------------------------------------
# check_all_providers
# ---------------------------------------------------------------------------


@respx.mock
def test_check_all_providers_routes_prepaid_wallet() -> None:
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=95.0, limit=100.0))
    )
    cost_config = CostConfig(
        providers={
            "openrouter": ProviderCostConfig(
                type="prepaid_wallet",
                yellow_remaining_usd=10.0,
                orange_remaining_usd=5.0,
            )
        }
    )

    with httpx.Client() as client:
        signals = check_all_providers(cost_config, client, {"openrouter": "sk-test"})

    # remaining = 5.0, at orange threshold
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.ORANGE


@respx.mock
def test_check_all_providers_routes_rolling_window() -> None:
    cost_config = CostConfig(
        providers={
            "anthropic": ProviderCostConfig(
                type="rolling_window",
                yellow_percent=80,
                orange_percent=95,
            )
        }
    )

    with httpx.Client() as client:
        signals = check_all_providers(cost_config, client, {"anthropic": "sk-ant-test"})

    # Rolling window is a stub — expect empty
    assert signals == []


@respx.mock
def test_check_all_providers_skips_missing_key() -> None:
    cost_config = CostConfig(
        providers={
            "openrouter": ProviderCostConfig(
                type="prepaid_wallet",
                yellow_remaining_usd=10.0,
            )
        }
    )

    with httpx.Client() as client:
        # No key provided for openrouter
        signals = check_all_providers(cost_config, client, {})

    assert signals == []


@respx.mock
def test_check_all_providers_isolates_failures() -> None:
    """A failure in one provider should not prevent other providers from being checked."""
    # openrouter will fail
    respx.get(_KEY_URL).mock(side_effect=httpx.ConnectError("down"))

    cost_config = CostConfig(
        providers={
            "openrouter": ProviderCostConfig(
                type="prepaid_wallet",
                yellow_remaining_usd=10.0,
                orange_remaining_usd=5.0,
            ),
            "anthropic": ProviderCostConfig(
                type="rolling_window",
                yellow_percent=80,
            ),
        }
    )

    with httpx.Client() as client:
        signals = check_all_providers(
            cost_config,
            client,
            {"openrouter": "sk-test", "anthropic": "sk-ant-test"},
        )

    # openrouter fallback YELLOW + anthropic stub (empty) → 1 signal total
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
    msg = signals[0].message.lower()
    assert "openrouter" in msg or "balance check failed" in msg


@respx.mock
def test_check_all_providers_collects_across_multiple() -> None:
    """Multiple providers each return signals; all are aggregated."""
    # First openrouter call → balance low
    respx.get(_KEY_URL).mock(
        return_value=httpx.Response(200, json=_balance_response(usage=92.0, limit=100.0))
    )

    cost_config = CostConfig(
        providers={
            "openrouter": ProviderCostConfig(
                type="prepaid_wallet",
                yellow_remaining_usd=10.0,
                orange_remaining_usd=5.0,
            ),
            "anthropic": ProviderCostConfig(
                type="rolling_window",
                yellow_percent=80,
            ),
        }
    )

    with httpx.Client() as client:
        signals = check_all_providers(
            cost_config,
            client,
            {"openrouter": "sk-test", "anthropic": "sk-ant-test"},
        )

    # openrouter YELLOW (remaining=8.0 <= yellow=10.0), anthropic stub = []
    assert len(signals) == 1
    assert signals[0].tier == AlertTier.YELLOW
