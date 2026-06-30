"""Tests for search_by_tag_name (group B — name -> findQuery tag filter, one step)."""

from __future__ import annotations

from dots_kms_mcp import server


async def test_filters_by_cached_tag_and_returns_matching_docs():
    result = await server.search_by_tag_name(
        content_types=["articles"], tag_type="states", tag_name="Karnataka", limit=3
    )
    assert result["resolved_tag"]["value"] == "karnataka"
    assert result["resolved_tag"]["source"] == "cache"
    assert len(result["data"]) == 3
    # The returned docs are actually tagged with the requested tag (not just any docs).
    assert all(
        d["tags"]["states"]["data"][0]["display"] == "Karnataka" for d in result["data"]
    )


async def test_unknown_tag_name_reports_not_found_and_filters_to_nothing():
    result = await server.search_by_tag_name(
        content_types=["articles"], tag_type="states", tag_name="Nowhere", limit=5
    )
    # Not in the cache -> reported as not_found; the query matches the name on
    # `display`, which no mock doc carries, so nothing comes back.
    assert result["resolved_tag"]["source"] == "not_found"
    assert result["data"] == []
