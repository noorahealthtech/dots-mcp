"""Tests for count_only and facet_counts (group A — count/facet modes)."""

from __future__ import annotations

import pytest

from dots_kms_mcp import server
from dots_kms_mcp.configs import build_configs


async def test_count_only_returns_count():
    result = await server.count_only(content_types=["articles"])
    assert result == {"count": 47}


async def test_count_only_requires_a_type():
    with pytest.raises(ValueError):
        await server.count_only()


async def test_facet_counts_returns_buckets():
    result = await server.facet_counts(
        facets=[{"field": "category", "tagType": "category"}],
        content_types=["articles"],
    )
    assert result["count"] == 47
    assert "facets" in result
    cat = result["facets"]["category"]
    assert cat and cat[0]["value"] and isinstance(cat[0]["count"], int)


def test_build_configs_carries_facet_and_countdal():
    cfg = build_configs(
        content_types=["articles"], count=True,
        extra={"facet": [{"field": "category"}], "useCountDAL": True},
    )
    assert cfg["facet"] == [{"field": "category"}]
    assert cfg["useCountDAL"] is True
