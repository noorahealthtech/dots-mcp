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
    assert result["id"] == "673d8531d6ef55f9b7958e6d"
    assert result["source"] == "cache"


async def test_get_document_found_and_not_found():
    found = await server.get_document("anything", content_type="articles")
    assert found["found"] is True
    # query_getdata raising a config error surfaces as ValueError to the model.
    with pytest.raises(ValueError):
        await server.query_getdata({})  # neither content nor profile types
