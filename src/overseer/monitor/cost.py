"""Per-provider cost/usage monitoring."""

from __future__ import annotations

import httpx

from overseer.config import CostConfig, ProviderCostConfig
from overseer.types import AlertTier, Signal

_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"


def check_openrouter_balance(
    client: httpx.Client,
    api_key: str,
    provider_config: ProviderCostConfig,
) -> list[Signal]:
    """Check OpenRouter prepaid wallet balance against configured thresholds.

    GET /api/v1/auth/key returns:
      { "data": { "usage": <total_spent>, "limit": <budget_cap> } }

    Remaining = limit - usage. Compared against orange_remaining_usd and
    yellow_remaining_usd thresholds — orange takes precedence if both apply.

    On any API error, emits a YELLOW signal rather than propagating the exception.
    """
    try:
        response = client.get(
            _OPENROUTER_KEY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        data = response.json().get("data", {})
        usage: float = float(data.get("usage", 0))
        limit: float | None = data.get("limit")
    except Exception:
        return [
            Signal.now(
                source="cost",
                tier=AlertTier.YELLOW,
                message="openrouter balance check failed",
            )
        ]

    if limit is None:
        # No budget cap configured on this key — nothing to check.
        return []

    remaining = limit - usage

    if (
        provider_config.orange_remaining_usd is not None
        and remaining <= provider_config.orange_remaining_usd
    ):
        return [
            Signal.now(
                source="cost",
                tier=AlertTier.ORANGE,
                message=(
                    f"OpenRouter balance critically low: ${remaining:.2f} remaining"
                    f" (threshold: ${provider_config.orange_remaining_usd:.2f})"
                ),
            )
        ]

    if (
        provider_config.yellow_remaining_usd is not None
        and remaining <= provider_config.yellow_remaining_usd
    ):
        return [
            Signal.now(
                source="cost",
                tier=AlertTier.YELLOW,
                message=(
                    f"OpenRouter balance low: ${remaining:.2f} remaining"
                    f" (threshold: ${provider_config.yellow_remaining_usd:.2f})"
                ),
            )
        ]

    return []


def check_rolling_window_usage(
    provider_name: str,
    client: httpx.Client,
    api_key: str,
    provider_config: ProviderCostConfig,
) -> list[Signal]:
    """Check rolling-window usage against configured thresholds.

    STUB — not yet implemented.

    Providers with rolling-window rate limits (Anthropic, OpenAI, Gemini) each
    expose usage via different API endpoints and response schemas. Once the
    correct endpoint and auth pattern is confirmed for each provider, this stub
    should be replaced with per-provider dispatch or individual checker functions.

    Returns an empty list until that integration is in place.
    """
    return []


def check_all_providers(
    cost_config: CostConfig,
    http_client: httpx.Client,
    provider_keys: dict[str, str],
) -> list[Signal]:
    """Collect cost/usage signals across all configured providers.

    Routes each provider to the appropriate checker by type:
      - "prepaid_wallet"  → check_openrouter_balance
      - "rolling_window"  → check_rolling_window_usage (stub, returns [])

    Provider failures are isolated — one provider erroring does not block others.
    Providers with no API key in provider_keys are skipped silently.
    """
    signals: list[Signal] = []

    for provider_name, provider_config in cost_config.providers.items():
        api_key = provider_keys.get(provider_name)
        if not api_key:
            continue

        try:
            if provider_config.type == "prepaid_wallet":
                provider_signals = check_openrouter_balance(
                    http_client, api_key, provider_config
                )
            elif provider_config.type == "rolling_window":
                provider_signals = check_rolling_window_usage(
                    provider_name, http_client, api_key, provider_config
                )
            else:
                provider_signals = []
        except Exception:
            provider_signals = [
                Signal.now(
                    source="cost",
                    tier=AlertTier.YELLOW,
                    message=f"{provider_name} cost check failed unexpectedly",
                )
            ]

        signals.extend(provider_signals)

    return signals
