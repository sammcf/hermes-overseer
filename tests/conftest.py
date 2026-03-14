"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from overseer.config import Config, load_config

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "overseer.example.yaml"


@pytest.fixture
def example_config_path() -> Path:
    return EXAMPLE_CONFIG


@pytest.fixture
def example_config(example_config_path: Path) -> Config:  # noqa: ARG001
    """Load example config with required env var warnings suppressed."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_config(EXAMPLE_CONFIG)
