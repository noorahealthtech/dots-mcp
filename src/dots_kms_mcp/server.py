"""FastMCP server exposing the Noora KMS getData API as chat-callable tools.

Tool docstrings ARE the model's interface — they are what an LLM reads to decide
when and how to call each tool — so they carry the full contract (parameters,
invariants, examples) rather than just a one-liner.

Run as a local stdio server: ``dots-kms-mcp`` (or ``python -m dots_kms_mcp``).
"""

from __future__ import annotations

import sys
from typing import Any, NoReturn

from mcp.server.fastmcp import FastMCP

from .configs import build_configs
from .errors import KmsError
from .getdata_client import build_client
from .mock_client import mock_resolve_tag_candidates
from .schema import (
    _cached_schema,
    get_content_types,
    get_profile_types,
    get_tag_types,
    resolve_tag_from_cache,
)
from .settings import Settings

mcp = FastMCP("dots-kms")

# Resolve settings + client + schema once at import time. Settings.from_env logs
# the chosen mode (mock vs live) to stderr.
_settings = Settings.from_env()
_client = build_client(_settings)


def _schema() -> dict[str, Any]:
    return _cached_schema(_settings.schema_path)


def _raise_readable(exc: KmsError) -> NoReturn:
    """Re-raise a KMS error as a ValueError the model can read and recover from."""
    detail = getattr(exc, "detail", None) or str(exc)
    raise ValueError(detail) from exc


# --------------------------------------------------------------------------- #
# Querying
# --------------------------------------------------------------------------- #
@mcp.tool()
async def search_knowledge(
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    find_query: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    limit: int | None = 10,
    skip: int = 0,
    count: bool = True,
) -> dict[str, Any]:
    """Search and filter the knowledge base. This is the primary query tool.

    Query EITHER content OR profiles — never both in one call:
      - content_types: e.g. ["articles"], ["stories"]. Use list_content_types to discover.
      - profile_types: e.g. ["volunteers"]. Use list_profile_types to discover.
    Exactly one of content_types / profile_types must be provided.

    search_term: full-text query (e.g. "breastfeeding"); results come back in
      relevance order. Omit to browse/filter without text search.

    Pagination: pass limit (default 10) and skip (default 0). The response may
    include a "skip" value — pass that exact value back as `skip` to get the next
    page. When the response has no "skip", you've reached the last page. The
    response "count" is the total number of matching documents.

    sort: {"<field>": 1 | -1}, e.g. {"createdAt": -1} for newest first.
    projection: MongoDB projection to limit returned fields,
      e.g. {"meta.title": 1, "meta.description": 1, "createdAt": 1}.
    find_query: raw MongoDB conditions merged into the query,
      e.g. {"status": "published"}.

    filters: structured filters as a list of {"target": {...}, "values": [...]}.
    IMPORTANT: tag-based filters need MongoDB ObjectId tag IDs, NOT human names —
    use resolve_tag to turn a name like "Karnataka" into its id first. Filter types:
      - tagType: {"target": {"filterType": "tagType", "tagType": "countries"},
                  "values": ["<tagId>", ...]}
      - valuePathType: match a field value at a path,
          {"target": {"filterType": "valuePathType", "valuePath": "meta.status"},
           "values": ["published"]}
      - dateRangeType: {"target": {"filterType": "dateRangeType", "path": "createdAt"},
          "values": [{"start": "2024-01-01T00:00:00.000Z", "end": "2024-12-31T23:59:59.999Z"}]}
      - numberRangeType: {"target": {"filterType": "numberRangeType", "path": "meta.rating"},
          "values": [{"min": 3, "max": 5}]}  (or [{"exact": 4}])
      - rollupRelationshipType: filter content by a tag on a related user profile,
          {"target": {"filterType": "rollupRelationshipType", "tagType": "states",
           "relationshipValuePath": "meta.kp_contributed_by"}, "values": ["<stateTagId>"]}
      - nestedRollupTagType: traverse a tag hierarchy bottom->top,
          {"target": {"filterType": "nestedRollupTagType", "rollupPath": ["cities", "states"]},
           "values": ["<stateTagId>"]}
      - rollupValuePathType: filter by a field in a referenced collection,
          {"target": {"filterType": "rollupValuePathType", "collectionToRollup": "users",
           "valuePathToRollup": "meta.kp_contributed_by", "valuePathInRolledUpCollection": "role"},
           "values": ["superAdmin"]}

    Returns {"data": [...documents], "count"?: int, "skip"?: int}.
    For advanced options (population/joins, facet, aggregation), use query_getdata.
    """
    try:
        configs = build_configs(
            content_types=content_types,
            profile_types=profile_types,
            search_term=search_term,
            filters=filters,
            find_query=find_query,
            sort=sort,
            projection=projection,
            limit=limit,
            skip=skip,
            count=count,
        )
        return await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)


@mcp.tool()
async def get_document(
    document_id: str,
    content_type: str | None = None,
    profile_type: str | None = None,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch a single document by its _id.

    Provide the type the document belongs to: exactly one of content_type (e.g.
    "articles") or profile_type (e.g. "volunteers"). projection optionally limits
    returned fields.

    Returns {"document": {...}} when found, or {"document": None, "found": false}
    when no document matches that id within the given type.
    """
    try:
        configs = build_configs(
            content_types=[content_type] if content_type else None,
            profile_types=[profile_type] if profile_type else None,
            find_query={"_id": document_id},
            projection=projection,
            limit=1,
            count=False,
        )
        result = await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)

    data = result.get("data") or []
    if not data:
        return {"document": None, "found": False, "document_id": document_id}
    return {"document": data[0], "found": True}


# --------------------------------------------------------------------------- #
# Discovery (from the local, developer-maintained schema)
# --------------------------------------------------------------------------- #
@mcp.tool()
async def list_content_types() -> list[dict[str, Any]]:
    """List the content types you can query (e.g. articles, stories, reports).

    Sourced from a local, developer-maintained schema (kms_schema.json), not a
    live endpoint. Call this before building a content query if you are unsure
    which content_types exist. Each entry: {id, name, description}.
    """
    return get_content_types(_schema())


@mcp.tool()
async def list_profile_types() -> list[dict[str, Any]]:
    """List the profile types you can query (e.g. volunteers, coreTeam, researchers).

    Sourced from the local kms_schema.json. Profiles are people/entities; content
    is articles/stories/etc. A query targets one or the other, never both.
    Each entry: {id, name, description}.
    """
    return get_profile_types(_schema())


@mcp.tool()
async def list_tag_types() -> list[dict[str, Any]]:
    """List tag types available for filtering (e.g. countries, states, cities, category).

    Sourced from the local kms_schema.json. Each entry includes any pre-cached
    name->ObjectId mappings under "values"; tag filters in search_knowledge need
    those ObjectIds (use resolve_tag for names that aren't cached).
    Each entry: {id, name, description, name_path, values: {name: objectId}}.
    """
    return get_tag_types(_schema())


@mcp.tool()
async def resolve_tag(
    tag_type: str,
    name: str,
    name_path: str | None = None,
) -> dict[str, Any]:
    """Resolve a human tag name (e.g. "Karnataka") to its MongoDB ObjectId for use
    in search_knowledge filters.

    Resolution order:
      1. The local schema cache (authoritative, developer-curated).
      2. If not cached AND running against a live KMS, a best-effort query that
         treats the tag type as a queryable collection and matches `name` at
         `name_path` (default from the schema, else "meta.title").

    NOTE: the live fallback is SPECULATIVE — the documented API exposes only
    getData with no dedicated tags endpoint, so candidates returned by the
    fallback should be confirmed before relying on them. Prefer adding confirmed
    mappings to kms_schema.json so future lookups hit the cache.

    Returns {"tag_type", "name", "id"?, "source": "cache"|"query"|"mock"|"not_found",
    "candidates"?: [...]}.
    """
    schema = _schema()
    cached = resolve_tag_from_cache(schema, tag_type, name)
    if cached:
        return {"tag_type": tag_type, "name": name, "id": cached, "source": "cache"}

    # Determine the path to match the name against.
    entry = next((t for t in get_tag_types(schema) if t.get("id") == tag_type), None)
    path = name_path or (entry or {}).get("name_path") or "meta.title"

    if _settings.mock:
        candidates = mock_resolve_tag_candidates(name, None)
        return {
            "tag_type": tag_type,
            "name": name,
            "id": candidates[0]["_id"],
            "source": "mock",
            "candidates": candidates,
        }

    # Best-effort live fallback (speculative — see docstring).
    try:
        configs = build_configs(
            content_types=[tag_type],
            find_query={path: name},
            projection={path: 1},
            limit=10,
            count=False,
        )
        result = await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)

    candidates = result.get("data") or []
    if not candidates:
        return {"tag_type": tag_type, "name": name, "source": "not_found", "candidates": []}
    return {
        "tag_type": tag_type,
        "name": name,
        "id": candidates[0].get("_id"),
        "source": "query",
        "candidates": candidates,
    }


# --------------------------------------------------------------------------- #
# Raw escape hatch
# --------------------------------------------------------------------------- #
@mcp.tool()
async def query_getdata(configs: dict[str, Any]) -> dict[str, Any]:
    """Advanced: run a raw getData `configs` object (still validated + correctly encoded).

    Use this for capabilities search_knowledge doesn't expose directly, such as:
      - population: [{"path": "meta.kp_contributed_by", "select": "name email"}]  (joins)
      - facet: [{"field": "category", "tagType": "category"}]  (counts per group)
      - useAggregation: {"groupByWithLookup": true}
      - lookupConfig, taggedResourcesCount, ksConfig, etc.

    `configs` must still contain exactly one of contentTypes / profileTypes.
    Example: {"contentTypes": ["articles"], "limit": 10,
              "population": [{"path": "meta.kp_contributed_by", "select": "name"}]}

    Returns the raw API response: {"data": [...], "count"?: int, "skip"?: int}.
    """
    try:
        return await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)


# --------------------------------------------------------------------------- #
# Resource: the whole discovery schema, for clients that embed resources
# --------------------------------------------------------------------------- #
@mcp.resource("kms://schema")
def schema_resource() -> dict[str, Any]:
    """The full discovery schema (content/profile/tag types + cached tag ids)."""
    return _schema()


def main() -> None:
    """Console entry point: run the server over stdio."""
    print(
        f"[dots-kms-mcp] starting (mock={_settings.mock}) — stdio transport.",
        file=sys.stderr,
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
