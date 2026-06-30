"""Tests for group C — Prompts and templated Resources.

Key gotcha exercised here: templated resources show up under
list_resource_templates(), NOT list_resources().
"""

from __future__ import annotations

from dots_kms_mcp import server


async def test_prompts_registered():
    names = {p.name for p in await server.mcp.list_prompts()}
    assert {"kms_compare", "kms_research", "kms_brief"} <= names


async def test_templated_resources_under_templates_not_resources():
    template_uris = {t.uriTemplate for t in await server.mcp.list_resource_templates()}
    assert "kms://content-type/{content_type}" in template_uris
    assert "kms://recent/{content_type}" in template_uris

    static_uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert "kms://schema" in static_uris
    # the templated ones must NOT appear in the static resource list
    assert "kms://content-type/{content_type}" not in static_uris


def test_prompt_function_renders_recipe_text():
    # The @mcp.prompt decorator returns the original function, so we can call it.
    text = server.kms_compare("Karnataka", "Punjab", period="2024")
    assert "Karnataka" in text and "Punjab" in text
    assert "2024" in text
    assert "cite" in text.lower()


async def test_templated_resource_function_returns_data():
    data = await server.recent_resource("articles")
    assert data["content_type"] == "articles"
    assert len(data["recent"]) == 10
    assert data["total"] == 47
