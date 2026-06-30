"""Tests for collect (group A — server-side auto-pagination)."""

from __future__ import annotations

from dots_kms_mcp import server


async def test_collect_aggregates_across_pages():
    result = await server.collect(content_types=["articles"], max_results=47, page_size=10)
    assert len(result["data"]) == 47
    assert result["count"] == 47
    assert result["pages_fetched"] >= 5  # 10+10+10+10+7
    assert result["truncated"] is False


async def test_collect_caps_at_max_results():
    result = await server.collect(content_types=["articles"], max_results=15, page_size=10)
    assert len(result["data"]) == 15
    assert result["truncated"] is True  # 47 total > 15 pulled
    assert result["pages_fetched"] == 2


async def test_collect_stops_at_last_page_when_fewer_than_max():
    # Asking for more than exist: returns all 47, not truncated.
    result = await server.collect(content_types=["articles"], max_results=500, page_size=25)
    assert len(result["data"]) == 47
    assert result["truncated"] is False


async def test_collect_requires_a_type():
    import pytest

    with pytest.raises(ValueError):
        await server.collect()
