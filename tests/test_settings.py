"""Tests for env-driven settings resolution and the schema loader."""

from __future__ import annotations

from dots_kms_mcp.schema import load_schema, resolve_tag_from_cache
from dots_kms_mcp.settings import Settings


def _clear_env(monkeypatch):
    for key in [
        "KMS_AUTH_TOKEN",
        "KMS_TENANT",
        "KMS_BASE_URL",
        "KMS_MOCK",
        "KMS_SCHEMA_PATH",
        "KMS_TIMEOUT",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_auto_mock_when_creds_absent(monkeypatch):
    _clear_env(monkeypatch)
    settings = Settings.from_env(load_env_file=False)
    assert settings.mock is True


def test_live_when_creds_present(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_AUTH_TOKEN", "tok")
    monkeypatch.setenv("KMS_TENANT", "ten")
    settings = Settings.from_env(load_env_file=False)
    assert settings.mock is False
    assert settings.token == "tok"
    assert settings.tenant == "ten"


def test_explicit_mock_overrides_present_creds(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_AUTH_TOKEN", "tok")
    monkeypatch.setenv("KMS_TENANT", "ten")
    monkeypatch.setenv("KMS_MOCK", "1")
    settings = Settings.from_env(load_env_file=False)
    assert settings.mock is True


def test_explicit_mock_false_with_creds(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_AUTH_TOKEN", "tok")
    monkeypatch.setenv("KMS_TENANT", "ten")
    monkeypatch.setenv("KMS_MOCK", "0")
    settings = Settings.from_env(load_env_file=False)
    assert settings.mock is False


def test_base_url_and_timeout_overrides(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_BASE_URL", "https://custom.example/")
    monkeypatch.setenv("KMS_TIMEOUT", "12.5")
    settings = Settings.from_env(load_env_file=False)
    assert settings.base_url == "https://custom.example/"
    assert settings.getdata_url == "https://custom.example/api/discovery/getData"
    assert settings.timeout == 12.5


def test_bad_timeout_falls_back_to_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_TIMEOUT", "not-a-number")
    settings = Settings.from_env(load_env_file=False)
    assert settings.timeout == 30.0


def test_schema_loads_packaged_default(tmp_path, monkeypatch):
    # Run from a clean cwd so the packaged-default fallback is reached (not a developer's
    # generated ./kms_schema.json).
    monkeypatch.chdir(tmp_path)
    schema = load_schema(None)
    ids = {c["id"] for c in schema["content_types"]}
    assert "articles" in ids
    assert resolve_tag_from_cache(schema, "states", "Karnataka") == "karnataka"
    # Case-insensitive lookup works too.
    assert resolve_tag_from_cache(schema, "states", "karnataka") == "karnataka"
    assert resolve_tag_from_cache(schema, "states", "Nowhere") is None
