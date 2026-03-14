"""Hermes config drift detection against a canonical baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from overseer.types import AlertTier, Signal


def _get_nested(data: dict[str, Any], dot_path: str) -> Any:
    """Retrieve a value from a nested dict using dot-notation (e.g. 'model.default').

    Returns a sentinel object if any key in the path is missing.
    """
    _MISSING = object()
    parts = dot_path.split(".")
    node: Any = data
    for part in parts:
        if not isinstance(node, dict):
            return _MISSING
        node = node.get(part, _MISSING)
        if node is _MISSING:
            return _MISSING
    return node


def _load_yaml(path: str) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text())
    return raw if isinstance(raw, dict) else {}


def _collect_all_leaf_paths(
    data: dict[str, Any],
    prefix: str = "",
) -> dict[str, Any]:
    """Flatten a nested dict to dot-notation paths → leaf values."""
    leaves: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            leaves.update(_collect_all_leaf_paths(value, full_key))
        else:
            leaves[full_key] = value
    return leaves


def check_config_drift(
    current_config_path: str,
    canonical_config_path: str,
    policy_fields: list[str],
) -> list[Signal]:
    """Compare current Hermes config against the canonical baseline.

    - Policy field drift (dot-notation paths from policy_fields) → ORANGE signal.
    - Any other structural difference → YELLOW signal.

    Both YAML files are loaded and compared. Missing keys count as drift.
    """
    current = _load_yaml(current_config_path)
    canonical = _load_yaml(canonical_config_path)

    signals: list[Signal] = []
    policy_field_set = set(policy_fields)

    # Check policy fields explicitly.
    for field in policy_fields:
        current_val = _get_nested(current, field)
        canonical_val = _get_nested(canonical, field)
        if current_val != canonical_val:
            signals.append(
                Signal.now(
                    source="config_drift",
                    tier=AlertTier.ORANGE,
                    message=(
                        f"Policy field '{field}' drifted: "
                        f"canonical={canonical_val!r}, current={current_val!r}"
                    ),
                )
            )

    # Check all other leaf fields for non-policy drift.
    current_leaves = _collect_all_leaf_paths(current)
    canonical_leaves = _collect_all_leaf_paths(canonical)

    all_paths = set(current_leaves) | set(canonical_leaves)
    for path in sorted(all_paths):
        if path in policy_field_set:
            continue  # already handled above
        current_val = current_leaves.get(path, "<missing>")
        canonical_val = canonical_leaves.get(path, "<missing>")
        if current_val != canonical_val:
            signals.append(
                Signal.now(
                    source="config_drift",
                    tier=AlertTier.YELLOW,
                    message=(
                        f"Non-policy field '{path}' drifted: "
                        f"canonical={canonical_val!r}, current={current_val!r}"
                    ),
                )
            )

    return signals
