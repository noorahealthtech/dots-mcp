"""Tests for compare_regions, get_documents, related_documents (groups A & B)."""

from __future__ import annotations

from dots_kms_mcp import server


async def test_compare_regions_symmetric_samples():
    result = await server.compare_regions(
        "Karnataka", "Punjab", content_types=["articles"], sample_size=6
    )
    assert result["shared_tag_type"] == "states"
    assert result["region_a"]["resolved_tag"]["source"] == "cache"   # Karnataka cached
    assert result["region_b"]["resolved_tag"]["source"] == "mock"    # Punjab fallback
    assert len(result["region_a"]["documents"]) == 6
    assert len(result["region_b"]["documents"]) == 6


async def test_compare_regions_with_period():
    period = {"start": "2024-01-01T00:00:00.000Z", "end": "2024-12-31T23:59:59.999Z"}
    result = await server.compare_regions(
        "Karnataka", "Punjab", period=period, sample_size=4
    )
    assert len(result["region_a"]["documents"]) == 4


async def test_get_documents_batch_by_id():
    ids = ["aaa111aaa111aaa111aaa111", "bbb222", "ccc333"]
    result = await server.get_documents(ids, content_type="articles")
    assert result["found"] == 3
    assert {d["_id"] for d in result["documents"]} == set(ids)
    assert result["missing"] == []


async def test_related_documents_excludes_source():
    src = await server.search_knowledge(content_types=["articles"], limit=1)
    src_id = src["data"][0]["_id"]
    result = await server.related_documents(src_id, content_type="articles", limit=4)
    assert result["found"] is True
    assert result["filtered_on"]  # tags were extracted from the source doc
    assert len(result["related"]) <= 4
    assert all(d["_id"] != src_id for d in result["related"])
