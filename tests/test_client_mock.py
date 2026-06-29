"""Tests for the mock client (used when no creds are present)."""

from __future__ import annotations

import pytest

from dots_kms_mcp.errors import KmsConfigError
from dots_kms_mcp.mock_client import MockKmsClient


async def test_returns_data_and_count():
    client = MockKmsClient()
    result = await client.get_data({"contentTypes": ["articles"], "limit": 10, "countData": True})
    assert isinstance(result["data"], list)
    assert len(result["data"]) == 10
    assert result["count"] == 47
    doc = result["data"][0]
    assert doc["_id"] and len(doc["_id"]) == 24
    assert "title" in doc["meta"]


async def test_pagination_skip_advances_and_drops_on_last_page():
    client = MockKmsClient()
    first = await client.get_data({"contentTypes": ["articles"], "limit": 20, "skip": 0})
    assert first["skip"] == 20
    last = await client.get_data({"contentTypes": ["articles"], "limit": 20, "skip": 40})
    assert len(last["data"]) == 7  # 47 - 40
    assert "skip" not in last  # no more pages


async def test_count_false_omits_count():
    client = MockKmsClient()
    result = await client.get_data({"contentTypes": ["articles"], "limit": 5, "countData": False})
    assert "count" not in result


async def test_content_vs_profile_changes_pool():
    client = MockKmsClient()
    articles = await client.get_data({"contentTypes": ["articles"], "limit": 1})
    vols = await client.get_data({"profileTypes": ["volunteers"], "limit": 1})
    assert "articles" in articles["data"][0]["meta"]["title"]
    assert "volunteers" in vols["data"][0]["meta"]["title"]


async def test_search_term_folded_into_titles():
    client = MockKmsClient()
    result = await client.get_data(
        {"contentTypes": ["articles"], "searchTerm": "breastfeeding", "limit": 3}
    )
    assert all("Breastfeeding" in d["meta"]["title"] for d in result["data"])


async def test_deterministic_ids():
    client = MockKmsClient()
    a = await client.get_data({"contentTypes": ["articles"], "limit": 3})
    b = await client.get_data({"contentTypes": ["articles"], "limit": 3})
    assert [d["_id"] for d in a["data"]] == [d["_id"] for d in b["data"]]


async def test_validation_still_fires():
    client = MockKmsClient()
    with pytest.raises(KmsConfigError):
        await client.get_data({})  # neither content nor profile types
