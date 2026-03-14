"""BinaryLane API client factory and request wrappers.

All functions are pure/IO-explicit: the client is a parameter, not global state.
Retry logic uses exponential backoff with jitter on 429/5xx responses.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from overseer.config import BinaryLaneConfig, resolve_secret
from overseer.types import Err, Ok, Result

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Seconds to sleep between retries — overridable in tests via monkeypatch
_sleep = time.sleep


def create_client(config: BinaryLaneConfig) -> httpx.Client:
    """Return a configured httpx.Client with Bearer auth and base URL set."""
    token = resolve_secret(config.api_token_env)
    return httpx.Client(
        base_url=config.base_url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    )


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter: [0, 2^attempt) seconds, capped at 60s."""
    cap = 60.0
    base = min(cap, 2.0**attempt)
    return random.uniform(0, base)


def _is_retryable(status: int) -> bool:
    return status in _RETRYABLE_STATUS


def api_get(
    client: httpx.Client,
    path: str,
    max_retries: int = 5,
) -> Result[dict[str, Any]]:
    """GET `path`, returning Ok(parsed JSON) or Err on failure."""
    return _request(client, "GET", path, body=None, max_retries=max_retries)


def api_post(
    client: httpx.Client,
    path: str,
    body: dict[str, Any],
    max_retries: int = 5,
) -> Result[dict[str, Any]]:
    """POST `body` to `path`, returning Ok(parsed JSON) or Err on failure."""
    return _request(client, "POST", path, body=body, max_retries=max_retries)


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    max_retries: int,
) -> Result[dict[str, Any]]:
    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            if method == "GET":
                response = client.get(path)
            else:
                response = client.post(path, json=body)
        except httpx.RequestError as exc:
            last_error = f"Request error on {method} {path}: {exc}"
            delay = _backoff_delay(attempt)
            _sleep(delay)
            continue

        if response.is_success:
            try:
                return Ok(response.json())
            except Exception as exc:
                return Err(f"Failed to parse JSON from {method} {path}: {exc}", source="binarylane")

        if not _is_retryable(response.status_code):
            return Err(
                f"{method} {path} failed with {response.status_code}: {response.text}",
                source="binarylane",
            )

        last_error = f"{method} {path} failed with {response.status_code}"
        if attempt < max_retries:
            delay = _backoff_delay(attempt)
            _sleep(delay)

    return Err(
        f"{method} {path} failed after {max_retries + 1} attempts: {last_error}",
        source="binarylane",
    )
