# tests/conftest.py
"""Pytest configuration and shared fixtures."""
import asyncio
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: integration tests (needs network)"
    )
    config.addinivalue_line(
        "markers", "unit: pure unit tests"
    )
    config.addinivalue_line(
        "markers", "platform: platform tests"
    )
    config.addinivalue_line(
        "markers", "slow: slow tests"
    )