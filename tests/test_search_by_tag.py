"""Tests for search_by_tag_name (group B — name->id then filter, one step)."""

from __future__ import annotations

from dots_kms_mcp import server


async def test_resolves_cached_tag_then_filters():
    result = await server.search_by_tag_name(
        content_types=["articles"], tag_type="states", tag_name="Karnataka", limit=3
    )
    assert result["resolved_tag"]["id"] == "673d8531d6ef55f9b7958e6d"
    assert result["resolved_tag"]["source"] == "cache"
    assert len(result["data"]) == 3


async def test_uncached_tag_uses_fallback():
    result = await server.search_by_tag_name(
        content_types=["articles"], tag_type="states", tag_name="Punjab", limit=2
    )
    # Punjab isn't cached -> mock fallback (live would be a speculative query).
    assert result["resolved_tag"]["source"] == "mock"
    assert result["resolved_tag"]["id"]
    assert len(result["data"]) == 2
