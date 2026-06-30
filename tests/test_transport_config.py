"""Tests for transport selection settings (stdio vs streamable-http).

These drive the remote-deployment feature: the server stays stdio by default
(Claude Desktop) but can be flipped to an HTTP transport via env for the remote
connector. main() must pick the transport from settings without touching stdout.
"""

from __future__ import annotations

import pytest

from dots_kms_mcp.settings import Settings

_TRANSPORT_KEYS = [
    "KMS_AUTH_TOKEN",
    "KMS_TENANT",
    "KMS_MOCK",
    "KMS_SCHEMA_PATH",
    "KMS_TRANSPORT",
    "KMS_HOST",
    "KMS_PORT",
    "PORT",
    "KMS_STATELESS_HTTP",
    "KMS_JSON_RESPONSE",
]


def _clear_env(monkeypatch):
    for key in _TRANSPORT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_transport_defaults_to_stdio(monkeypatch):
    _clear_env(monkeypatch)
    s = Settings.from_env(load_env_file=False)
    assert s.transport == "stdio"
    assert s.host == "127.0.0.1"
    assert s.port == 8900
    # HTTP-mode niceties default on (simplest single-process behaviour).
    assert s.stateless_http is True
    assert s.json_response is True


def test_streamable_http_transport_selected(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_TRANSPORT", "streamable-http")
    s = Settings.from_env(load_env_file=False)
    assert s.transport == "streamable-http"


def test_transport_is_normalised(monkeypatch):
    # Tolerate casing/whitespace so a stray "  Streamable-HTTP " still works.
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_TRANSPORT", "  Streamable-HTTP ")
    s = Settings.from_env(load_env_file=False)
    assert s.transport == "streamable-http"


def test_host_and_port_overrides(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_HOST", "0.0.0.0")
    monkeypatch.setenv("KMS_PORT", "9001")
    s = Settings.from_env(load_env_file=False)
    assert s.host == "0.0.0.0"
    assert s.port == 9001


def test_port_falls_back_to_generic_PORT_env(monkeypatch):
    # Honour the conventional $PORT when KMS_PORT is unset (PaaS-friendly).
    _clear_env(monkeypatch)
    monkeypatch.setenv("PORT", "8080")
    s = Settings.from_env(load_env_file=False)
    assert s.port == 8080


def test_kms_port_wins_over_generic_PORT(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_PORT", "9001")
    monkeypatch.setenv("PORT", "8080")
    s = Settings.from_env(load_env_file=False)
    assert s.port == 9001


def test_bad_port_falls_back_to_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_PORT", "not-a-number")
    s = Settings.from_env(load_env_file=False)
    assert s.port == 8900


def test_stateless_and_json_response_can_be_disabled(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_STATELESS_HTTP", "0")
    monkeypatch.setenv("KMS_JSON_RESPONSE", "false")
    s = Settings.from_env(load_env_file=False)
    assert s.stateless_http is False
    assert s.json_response is False


def test_is_http_property(monkeypatch):
    _clear_env(monkeypatch)
    assert Settings.from_env(load_env_file=False).is_http is False
    monkeypatch.setenv("KMS_TRANSPORT", "streamable-http")
    assert Settings.from_env(load_env_file=False).is_http is True


# --- main() transport selection -------------------------------------------- #
# server is imported in mock+stdio mode (conftest), so importing it is safe.
import dataclasses  # noqa: E402

from dots_kms_mcp import server  # noqa: E402


def test_main_runs_configured_transport(monkeypatch):
    """main() must dispatch mcp.run with the transport from settings, not a
    hardcoded value."""
    for transport in ("stdio", "streamable-http"):
        monkeypatch.setattr(
            server, "_settings", dataclasses.replace(server._settings, transport=transport)
        )
        seen: dict[str, str] = {}
        monkeypatch.setattr(
            server.mcp, "run", lambda transport: seen.update(transport=transport)
        )
        server.main()
        assert seen["transport"] == transport
