"""Shared test fixtures."""

from __future__ import annotations

import pytest

from dots_kms_mcp.settings import Settings


@pytest.fixture
def live_settings() -> Settings:
    """Settings configured for the real client (mock disabled)."""
    return Settings(
        token="TEST_TOKEN",
        tenant="TEST_TENANT",
        base_url="https://api.example.test",
        mock=False,
        schema_path=None,
        timeout=5.0,
    )
