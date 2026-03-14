"""Read-only BinaryLane API queries. All functions return Result[T]."""

from __future__ import annotations

from typing import Any

import httpx

from overseer.binarylane.client import api_get
from overseer.types import Err, Ok, Result


def get_server(client: httpx.Client, server_id: int) -> Result[dict[str, Any]]:
    """GET /v2/servers/{server_id} — full server object."""
    result = api_get(client, f"/v2/servers/{server_id}")
    if isinstance(result, Err):
        return result
    data = result.value.get("server")
    if data is None:
        return Err(f"No 'server' key in response for server {server_id}", source="binarylane")
    return Ok(data)


def get_metrics(client: httpx.Client, server_id: int) -> Result[dict[str, Any]]:
    """GET /v2/samplesets/{server_id}/latest — latest metrics sample."""
    result = api_get(client, f"/v2/samplesets/{server_id}/latest")
    if isinstance(result, Err):
        return result
    data = result.value.get("sample_set")
    if data is None:
        return Err(
            f"No 'sample_set' key in metrics response for server {server_id}",
            source="binarylane",
        )
    return Ok(data)


def get_threshold_alerts(client: httpx.Client) -> Result[list[dict[str, Any]]]:
    """GET /v2/servers/threshold_alerts — all configured threshold alerts."""
    result = api_get(client, "/v2/servers/threshold_alerts")
    if isinstance(result, Err):
        return result
    data = result.value.get("threshold_alerts")
    if data is None:
        return Err("No 'threshold_alerts' key in response", source="binarylane")
    return Ok(data)


def list_backups(client: httpx.Client, server_id: int) -> Result[list[dict[str, Any]]]:
    """GET /v2/servers/{server_id}/backups — list of backup images."""
    result = api_get(client, f"/v2/servers/{server_id}/backups")
    if isinstance(result, Err):
        return result
    data = result.value.get("backups")
    if data is None:
        return Err(
            f"No 'backups' key in response for server {server_id}",
            source="binarylane",
        )
    return Ok(data)
