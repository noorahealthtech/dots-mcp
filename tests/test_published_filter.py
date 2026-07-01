"""Tests for the published-only filter used on the public remote connector.

The connector runs on a single shared KMS service token, so without this every
Noora member would see unpublished drafts (~73% of docs) org-wide. In HTTP mode we
restrict CONTENT queries to `kp_published_status == "published"` (confirmed the live
value). Profile queries are left untouched, and the filter is a hard AND that can't
be overridden to reveal drafts.
"""

from __future__ import annotations

from dots_kms_mcp.configs import build_configs
from dots_kms_mcp.getdata_client import (
    PUBLISHED_FIELD,
    PUBLISHED_VALUE,
    PublishedOnlyClient,
    build_client,
)
from dots_kms_mcp.mock_client import MockKmsClient
from dots_kms_mcp.settings import Settings

_ENV_KEYS = ["KMS_MOCK", "KMS_TRANSPORT", "KMS_PUBLISHED_ONLY"]


def _clear(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


class _SpyClient:
    """Records the configs it was handed and returns an empty result."""

    def __init__(self) -> None:
        self.configs: dict | None = None

    async def get_data(self, configs: dict) -> dict:
        self.configs = configs
        return {"data": [], "count": 0}


# --- settings gating -------------------------------------------------------- #
def test_published_only_defaults_off_for_stdio(monkeypatch):
    _clear(monkeypatch)
    assert Settings.from_env(load_env_file=False).published_only is False


def test_published_only_defaults_on_for_http(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("KMS_TRANSPORT", "streamable-http")
    assert Settings.from_env(load_env_file=False).published_only is True


def test_published_only_explicit_override(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("KMS_TRANSPORT", "streamable-http")
    monkeypatch.setenv("KMS_PUBLISHED_ONLY", "0")   # opt out even under HTTP
    assert Settings.from_env(load_env_file=False).published_only is False
    monkeypatch.setenv("KMS_PUBLISHED_ONLY", "1")   # opt in even under stdio
    monkeypatch.delenv("KMS_TRANSPORT", raising=False)
    assert Settings.from_env(load_env_file=False).published_only is True


# --- the wrapper ------------------------------------------------------------ #
async def test_content_query_returns_only_published_docs():
    client = PublishedOnlyClient(MockKmsClient())
    configs = build_configs(content_types=["reports"], limit=25)
    result = await client.get_data(configs)
    docs = result["data"]
    assert docs, "expected some published docs"
    assert all(d.get(PUBLISHED_FIELD) == PUBLISHED_VALUE for d in docs)


async def test_unwrapped_mock_still_returns_drafts():
    # Sanity: the filter is what removes drafts, not the mock itself.
    plain = await MockKmsClient().get_data(build_configs(content_types=["reports"], limit=25))
    assert any(d.get(PUBLISHED_FIELD) == "draft" for d in plain["data"])


async def test_existing_find_query_is_preserved():
    spy = _SpyClient()
    client = PublishedOnlyClient(spy)
    configs = build_configs(
        content_types=["reports"],
        find_query={"tags.country.data.tagId": {"$in": ["indonesia"]}},
    )
    await client.get_data(configs)
    fq = spy.configs["findQuery"]
    # Both the caller's tag filter and the published filter must survive.
    assert "tags.country.data.tagId" in str(fq)
    assert PUBLISHED_VALUE in str(fq)


async def test_profile_query_is_not_filtered():
    spy = _SpyClient()
    client = PublishedOnlyClient(spy)
    configs = build_configs(profile_types=["volunteers"])
    await client.get_data(configs)
    assert PUBLISHED_FIELD not in str(spy.configs.get("findQuery") or {})


async def test_does_not_mutate_callers_configs():
    spy = _SpyClient()
    client = PublishedOnlyClient(spy)
    configs = build_configs(content_types=["reports"])
    before = dict(configs)
    await client.get_data(configs)
    assert configs == before  # caller's dict untouched (we copy)


# --- build_client wiring ---------------------------------------------------- #
def _settings(published_only: bool) -> Settings:
    return Settings(
        token=None, tenant=None, base_url="x", web_url="x", mock=True,
        schema_path=None, timeout=5.0, published_only=published_only,
    )


def test_build_client_wraps_when_published_only():
    assert isinstance(build_client(_settings(True)), PublishedOnlyClient)


def test_build_client_unwrapped_when_disabled():
    assert not isinstance(build_client(_settings(False)), PublishedOnlyClient)
