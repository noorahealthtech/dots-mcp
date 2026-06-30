"""Smoke test: the server registers the expected tools + resource.

Guards against decorator/signature regressions and confirms FastMCP can generate
schemas for the tool parameter types under Python 3.10.
"""

from __future__ import annotations

import pytest

from dots_kms_mcp import server

EXPECTED_TOOLS = {
    "search_knowledge",
    "get_document",
    "list_content_types",
    "list_profile_types",
    "list_tag_types",
    "resolve_tag",
    "query_getdata",
    # group A — retrieval power
    "count_only",
    "facet_counts",
    "get_documents",
    "collect",
    "document_attachments",
    # group B — friction-reducers & composite
    "search_by_tag_name",
    "related_documents",
    "compare_regions",
}


async def test_expected_tools_registered():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


async def test_schema_resource_registered():
    resources = await server.mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "kms://schema" in uris


async def test_search_knowledge_runs_in_mock_mode():
    # The module-level client is mock by default (no creds in the test env).
    result = await server.search_knowledge(content_types=["articles"], limit=3)
    assert len(result["data"]) == 3


async def test_resolve_tag_hits_cache():
    result = await server.resolve_tag("states", "Karnataka")
    assert result["value"] == "karnataka"
    assert result["filter_field"] == "tagId"
    assert result["source"] == "cache"


async def test_get_document_found_and_not_found():
    found = await server.get_document("anything", content_type="articles")
    assert found["found"] is True
    # query_getdata raising a config error surfaces as ValueError to the model.
    with pytest.raises(ValueError):
        await server.query_getdata({})  # neither content nor profile types


async def test_every_returned_doc_has_a_source_url():
    result = await server.search_knowledge(content_types=["articles"], limit=3)
    assert result["data"]
    for doc in result["data"]:
        assert doc["source_url"].startswith("http")
        assert "/published-page/articles?id=" in doc["source_url"]
        assert doc["source_url"].endswith(doc["_id"])
        # attachments are opt-in, so not present by default
        assert "attachments" not in doc


async def test_include_attachments_adds_pdf_first():
    doc = (await server.get_document(
        "anything", content_type="articles", include_attachments=True
    ))["document"]
    assert doc["attachments"][0]["kind"] == "pdf"
    assert doc["attachments"][0]["url"].endswith(".pdf")
    assert {a["kind"] for a in doc["attachments"]} == {"pdf", "image", "link"}


async def test_document_attachments_tool_pdf_only_filter():
    res = await server.document_attachments(
        "anything", content_type="articles", kinds=["pdf"]
    )
    assert res["found"] is True
    assert res["count"] == 1 and res["attachments"][0]["kind"] == "pdf"
    assert "/published-page/articles?id=" in res["source_url"]
