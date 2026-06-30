"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Keep the suite hermetic regardless of a developer's local setup: force MOCK mode and
# the packaged default schema, so live creds in .env or a generated kms_schema.json in
# the repo root don't bleed into tests. Set before dots_kms_mcp.server is imported (it
# resolves Settings.from_env() at import). Settings.from_env uses load_dotenv(override=
# False), so these explicit os.environ values win over .env. Tests that exercise
# from_env directly clear these via monkeypatch + load_env_file=False.
os.environ["KMS_MOCK"] = "1"
os.environ["KMS_SCHEMA_PATH"] = str(
    Path(__file__).resolve().parent.parent
    / "src" / "dots_kms_mcp" / "data" / "kms_schema.default.json"
)

from dots_kms_mcp.settings import Settings  # noqa: E402


@pytest.fixture
def live_settings() -> Settings:
    """Settings configured for the real client (mock disabled)."""
    return Settings(
        token="TEST_TOKEN",
        tenant="TEST_TENANT",
        base_url="https://api.example.test",
        web_url="https://web.example.test",
        mock=False,
        schema_path=None,
        timeout=5.0,
    )
