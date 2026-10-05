"""Tests for env-driven settings resolution and the schema loader."""

from __future__ import annotations

import pytest

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
        "KMS_CREATE_ENABLED",
        "KMS_CREATE_CONTENT_TYPES",
        "KMS_CREATE_SCHEMA_PATH",
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


def test_creation_defaults_off(monkeypatch):
    _clear_env(monkeypatch)
    settings = Settings.from_env(load_env_file=False)
    assert settings.create_enabled is False
    assert settings.create_content_types == ()
    assert settings.create_schema_path is None


def test_creation_settings_parse_allowlist(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_CREATE_ENABLED", "1")
    monkeypatch.setenv("KMS_CREATE_CONTENT_TYPES", "routineVisits, reports")
    monkeypatch.setenv("KMS_CREATE_SCHEMA_PATH", "/run/secrets/create-schema.json")
    settings = Settings.from_env(load_env_file=False)
    assert settings.create_enabled is True
    assert settings.create_content_types == ("routineVisits", "reports")
    assert settings.create_schema_path == "/run/secrets/create-schema.json"


def test_creation_allowlist_removes_blanks_and_duplicates_in_order(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(
        "KMS_CREATE_CONTENT_TYPES",
        " reports, routineVisits, reports, ,routineVisits,articles ",
    )
    settings = Settings.from_env(load_env_file=False)
    assert settings.create_content_types == ("reports", "routineVisits", "articles")


@pytest.mark.parametrize(
    ("transport", "oauth_configured", "content_types", "error"),
    [
        ("stdio", True, "reports", "streamable-http"),
        ("streamable-http", False, "reports", "OAuth"),
        ("streamable-http", True, "", "content type"),
    ],
)
def test_enabled_creation_rejects_unsafe_runtime(
    monkeypatch, transport, oauth_configured, content_types, error
):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_CREATE_ENABLED", "1")
    monkeypatch.setenv("KMS_TRANSPORT", transport)
    monkeypatch.setenv("KMS_CREATE_CONTENT_TYPES", content_types)
    settings = Settings.from_env(load_env_file=False)

    with pytest.raises(ValueError, match=error):
        settings.validate_create_runtime(oauth_configured)


def test_enabled_creation_accepts_safe_runtime(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_CREATE_ENABLED", "1")
    monkeypatch.setenv("KMS_TRANSPORT", "streamable-http")
    monkeypatch.setenv("KMS_CREATE_CONTENT_TYPES", "reports")
    settings = Settings.from_env(load_env_file=False)

    settings.validate_create_runtime(oauth_configured=True)


def test_disabled_creation_skips_runtime_validation(monkeypatch):
    _clear_env(monkeypatch)
    settings = Settings.from_env(load_env_file=False)

    settings.validate_create_runtime(oauth_configured=False)


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
