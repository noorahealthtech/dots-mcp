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

from .configs import build_configs, date_range_filter, tag_filter
from .errors import KmsError
from .getdata_client import build_client
from .mock_client import mock_resolve_tag_candidates
from .schema import (
    _cached_schema,
    extract_doc_tag_ids,
    find_tag_type,
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

# Hard ceiling on how many documents `collect` will pull, to protect context size.
MAX_COLLECT = 200


def _schema() -> dict[str, Any]:
    return _cached_schema(_settings.schema_path)


def _raise_readable(exc: KmsError) -> NoReturn:
    """Re-raise a KMS error as a ValueError the model can read and recover from."""
    detail = getattr(exc, "detail", None) or str(exc)
    raise ValueError(detail) from exc


async def _resolve_tag(
    tag_type: str, name: str, name_path: str | None = None
) -> dict[str, Any]:
    """Resolve a tag name -> ObjectId. Shared by resolve_tag, search_by_tag_name,
    compare_regions. Cache first, then mock/live fallback. May raise KmsError.
    """
    schema = _schema()
    cached = resolve_tag_from_cache(schema, tag_type, name)
    if cached:
        return {"tag_type": tag_type, "name": name, "id": cached, "source": "cache"}

    entry = find_tag_type(schema, tag_type)
    path = name_path or (entry or {}).get("name_path") or "meta.title"

    if _settings.mock:
        candidates = mock_resolve_tag_candidates(name, None)
        return {
            "tag_type": tag_type, "name": name, "id": candidates[0]["_id"],
            "source": "mock", "candidates": candidates,
        }

    # Best-effort live fallback (speculative — the API has no dedicated tags endpoint).
    configs = build_configs(
        content_types=[tag_type], find_query={path: name},
        projection={path: 1}, limit=10, count=False,
    )
    result = await _client.get_data(configs)
    candidates = result.get("data") or []
    if not candidates:
        return {"tag_type": tag_type, "name": name, "source": "not_found", "candidates": []}
    return {
        "tag_type": tag_type, "name": name, "id": candidates[0].get("_id"),
        "source": "query", "candidates": candidates,
    }


async def _collect(
    *,
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    find_query: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    max_results: int = 50,
    page_size: int = 25,
) -> dict[str, Any]:
    """Auto-paginate getData up to ``max_results``. Shared by the collect tool and
    compare_regions. Honors the returned ``skip``; stops at the cap or last page.
    """
    cap = min(int(max_results), MAX_COLLECT)
    size = min(int(page_size) or 1, cap)
    collected: list[dict[str, Any]] = []
    skip = 0
    pages = 0
    total: int | None = None

    while len(collected) < cap:
        configs = build_configs(
            content_types=content_types, profile_types=profile_types,
            search_term=search_term, filters=filters, find_query=find_query,
            sort=sort, projection=projection,
            limit=min(size, cap - len(collected)), skip=skip, count=True,
        )
        result = await _client.get_data(configs)
        pages += 1
        data = result.get("data") or []
        if total is None:
            total = result.get("count")
        collected.extend(data)
        next_skip = result.get("skip")
        if not data or next_skip is None:
            break
        skip = next_skip

    truncated = total is not None and len(collected) >= cap and total > len(collected)
    if truncated:
        print(
            f"[dots-kms-mcp] collect: truncated at {cap} of {total} total results.",
            file=sys.stderr,
        )
    return {
        "data": collected[:cap], "count": total,
        "pages_fetched": pages, "truncated": truncated,
    }


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
    try:
        return await _resolve_tag(tag_type, name, name_path)
    except KmsError as exc:
        _raise_readable(exc)


# --------------------------------------------------------------------------- #
# Retrieval power tools (group A): unlock getData features + reliable big pulls
# --------------------------------------------------------------------------- #
@mcp.tool()
async def count_only(
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    find_query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ONLY the total number of matching documents — no documents fetched.

    Uses the API's optimized count mode (useCountDAL), so it's cheap. Call this when
    the user asks "how many…", or before a large pull to decide how many pages to
    fetch. Provide exactly one of content_types / profile_types (filters/search
    optional). Returns {"count": int}.
    """
    try:
        configs = build_configs(
            content_types=content_types, profile_types=profile_types,
            search_term=search_term, filters=filters, find_query=find_query,
            limit=0, count=True, extra={"useCountDAL": True},
        )
        result = await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)
    return {"count": result.get("count")}


@mcp.tool()
async def facet_counts(
    facets: list[dict[str, Any]],
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    search_term: str | None = None,
) -> dict[str, Any]:
    """Get counts grouped by a field/tag, WITHOUT fetching documents.

    `facets` is a list like [{"field": "category", "tagType": "category"}]; each entry
    groups results and returns a count per bucket. Great for overviews and comparisons
    ("how many articles per category", distributions per region). Provide exactly one
    of content_types / profile_types. Returns the API's faceted counts.
    """
    try:
        configs = build_configs(
            content_types=content_types, profile_types=profile_types,
            search_term=search_term, filters=filters, count=True,
            extra={"facet": facets, "useCountDAL": True},
        )
        return await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)


@mcp.tool()
async def get_documents(
    document_ids: list[str],
    content_type: str | None = None,
    profile_type: str | None = None,
    projection: dict[str, Any] | None = None,
    populate: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fetch several full documents by their _ids in one call.

    Provide the type they belong to (one of content_type / profile_type) and a list of
    document_ids. `populate` optionally expands reference fields (joins), e.g.
    [{"path": "meta.kp_contributed_by", "select": "name email"}]. Use after a search
    returns ids when you need full document bodies for synthesis.
    Returns {"documents": [...], "found": int, "missing": [...ids]}.
    """
    try:
        configs = build_configs(
            content_types=[content_type] if content_type else None,
            profile_types=[profile_type] if profile_type else None,
            find_query={"_id": {"$in": list(document_ids)}},
            projection=projection,
            limit=len(document_ids) or 1, count=False,
            extra={"population": populate} if populate else None,
        )
        result = await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)
    docs = result.get("data") or []
    returned = {d.get("_id") for d in docs}
    missing = [i for i in document_ids if i not in returned]
    return {"documents": docs, "found": len(docs), "missing": missing}


@mcp.tool()
async def collect(
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    sort: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    max_results: int = 50,
    page_size: int = 25,
) -> dict[str, Any]:
    """Gather up to `max_results` matching documents in ONE call — the server
    paginates for you, so you don't manage `skip`.

    Use for "a bunch of…" or "a representative sample" requests. Capped at 200 to
    protect context size (the response notes if it truncated). Provide exactly one of
    content_types / profile_types. Returns
    {"data": [...], "count": <total>, "pages_fetched": int, "truncated": bool}.
    """
    try:
        return await _collect(
            content_types=content_types, profile_types=profile_types,
            search_term=search_term, filters=filters, sort=sort,
            projection=projection, max_results=max_results, page_size=page_size,
        )
    except KmsError as exc:
        _raise_readable(exc)


# --------------------------------------------------------------------------- #
# Friction-reducers & composite (group B)
# --------------------------------------------------------------------------- #
@mcp.tool()
async def search_by_tag_name(
    content_types: list[str],
    tag_type: str,
    tag_name: str,
    search_term: str | None = None,
    sort: dict[str, Any] | None = None,
    limit: int = 10,
    skip: int = 0,
) -> dict[str, Any]:
    """Search content filtered by a tag NAME in one step.

    e.g. tag_type="states", tag_name="Karnataka". Resolves the name to its ObjectId
    for you, then filters — saving the separate resolve_tag + search_knowledge dance.
    Returns the search result plus "resolved_tag": {name, id, source}.
    """
    try:
        tag = await _resolve_tag(tag_type, tag_name)
        if not tag.get("id"):
            raise ValueError(
                f"Could not resolve tag '{tag_name}' in tag type '{tag_type}'."
            )
        configs = build_configs(
            content_types=content_types, search_term=search_term,
            filters=[tag_filter(tag_type, [tag["id"]])],
            sort=sort, limit=limit, skip=skip,
        )
        result = await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)
    result = dict(result)
    result["resolved_tag"] = tag
    return result


@mcp.tool()
async def related_documents(
    document_id: str,
    content_type: str,
    by_tag_type: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Find documents related to a given one by shared tags.

    Reads the source document's tags, then searches the same content_type for other
    documents sharing those tags (excluding the source). `by_tag_type` limits which
    tag type defines "related" (default: any tag type on the document).
    Returns {"document_id", "found", "filtered_on": {tagType: [ids]}, "related": [...]}.
    """
    try:
        src_res = await _client.get_data(
            build_configs(
                content_types=[content_type],
                find_query={"_id": document_id}, limit=1, count=False,
            )
        )
        src = (src_res.get("data") or [None])[0]
        if not src:
            return {"document_id": document_id, "found": False, "related": []}

        tag_ids = extract_doc_tag_ids(src, by_tag_type)
        filters = [tag_filter(tt, ids) for tt, ids in tag_ids.items() if ids]
        if not filters:
            return {
                "document_id": document_id, "found": True, "filtered_on": {},
                "related": [], "note": "source document has no extractable tags to match on",
            }

        rel_res = await _client.get_data(
            build_configs(
                content_types=[content_type], filters=filters,
                find_query={"_id": {"$ne": document_id}}, limit=limit, count=False,
            )
        )
    except KmsError as exc:
        _raise_readable(exc)
    return {
        "document_id": document_id, "found": True,
        "filtered_on": tag_ids, "related": rel_res.get("data") or [],
    }


@mcp.tool()
async def compare_regions(
    region_a: str,
    region_b: str,
    content_types: list[str] | None = None,
    tag_type: str = "states",
    period: dict[str, Any] | None = None,
    sample_size: int = 20,
) -> dict[str, Any]:
    """Pull comparable samples of content for two regions so you can cross-synthesize them.

    Resolves each region name to its tag id, applies a `tag_type` filter (plus an
    optional date window), and collects up to `sample_size` documents per region.
    `period` is an optional {"start": ISO8601, "end": ISO8601} window on createdAt.
    The server guarantees symmetric, complete retrieval; YOU do the synthesis — cite
    _id + title, and only cite ids present in the results. Returns
    {"region_a": {region, resolved_tag, count, documents}, "region_b": {...},
     "shared_tag_type", "content_types"}.
    """
    content_types = content_types or ["articles"]
    period = period or {}
    try:
        out: dict[str, Any] = {"shared_tag_type": tag_type, "content_types": content_types}
        for key, region in (("region_a", region_a), ("region_b", region_b)):
            tag = await _resolve_tag(tag_type, region)
            filters = [tag_filter(tag_type, [tag.get("id")])]
            if period.get("start") or period.get("end"):
                filters.append(
                    date_range_filter("createdAt", period.get("start"), period.get("end"))
                )
            collected = await _collect(
                content_types=content_types, filters=filters,
                max_results=sample_size, page_size=min(int(sample_size), 25),
            )
            out[key] = {
                "region": region, "resolved_tag": tag,
                "count": collected.get("count"), "documents": collected.get("data"),
            }
        return out
    except KmsError as exc:
        _raise_readable(exc)


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
# Prompts (group C): user-triggered recipes — appear as slash commands.
# In Claude Code: /mcp__dots-kms__<name>. The model does not auto-invoke these;
# the user runs them, and the returned text steers the model through the tools.
# --------------------------------------------------------------------------- #
@mcp.prompt()
def kms_compare(region_a: str, region_b: str, period: str = "") -> str:
    """Cross-synthesis recipe: compare two regions' content, with citations."""
    window = f" within {period}" if period else ""
    period_arg = f', period covering {period}' if period else ""
    return (
        f"Compare the knowledge base content for {region_a} vs {region_b}{window}.\n\n"
        "Steps:\n"
        f"1. Call compare_regions(region_a=\"{region_a}\", region_b=\"{region_b}\"{period_arg}) "
        "to pull a symmetric sample for each region. (Or manually: resolve_tag each "
        "region, then collect ~20 docs each with the tag filter.)\n"
        "2. Read both sets; identify common themes AND notable differences.\n"
        "3. Write a cross-synthesis. Cite specific documents by _id and title, and "
        "ONLY cite ids that appear in the tool results — never invent a citation.\n"
        "4. If either region returned few results, say so explicitly."
    )


@mcp.prompt()
def kms_research(topic: str, content_types: str = "articles") -> str:
    """Guided research recipe: a thorough, cited brief on a topic."""
    return (
        f'Research the topic "{topic}" in the knowledge base '
        f"(content type(s): {content_types}).\n\n"
        "Steps:\n"
        f'1. Run search_knowledge with search_term="{topic}".\n'
        "2. Broaden recall: run 2-3 more searches with synonyms / related terms.\n"
        "3. Use collect (or paginate) to gather a representative sample, not just page one.\n"
        "4. Synthesize a concise brief grouped by sub-theme. Cite each claim with the "
        "source _id + title; only cite ids present in tool results.\n"
        "5. Note any gaps or thin areas you found."
    )


@mcp.prompt()
def kms_brief(content_type: str) -> str:
    """Overview/digest recipe for one content type."""
    return (
        f'Produce an overview digest of the "{content_type}" content type.\n\n'
        "Steps:\n"
        f"1. Use count_only to get the total number of {content_type}.\n"
        '2. Use facet_counts (e.g. facets=[{"field": "category", "tagType": "category"}]) '
        "for a category breakdown.\n"
        '3. Use collect with sort={"createdAt": -1} to gather the most recent items.\n'
        "4. Summarize: total volume, distribution across categories, and 3-5 recent "
        "highlights (cite each by _id + title)."
    )


# --------------------------------------------------------------------------- #
# Resources (group C): app/host-loaded context (not model-invoked actions).
# `kms://schema` is static (no params); the two below are TEMPLATED (path params
# map to function args) — they surface via list_resource_templates(), not list_resources().
# --------------------------------------------------------------------------- #
@mcp.resource("kms://schema")
def schema_resource() -> dict[str, Any]:
    """The full discovery schema (content/profile/tag types + cached tag ids)."""
    return _schema()


@mcp.resource("kms://content-type/{content_type}", mime_type="application/json")
async def content_type_resource(content_type: str) -> dict[str, Any]:
    """A content type's schema entry plus a small recent sample, as loadable context."""
    schema = _schema()
    entry = next(
        (c for c in get_content_types(schema) if c.get("id") == content_type),
        {"id": content_type, "note": "not described in kms_schema.json"},
    )
    sample = await _client.get_data(
        build_configs(
            content_types=[content_type], sort={"createdAt": -1},
            projection={"meta.title": 1, "createdAt": 1}, limit=5, count=True,
        )
    )
    return {
        "content_type": entry, "total": sample.get("count"),
        "recent_sample": sample.get("data"),
    }


@mcp.resource("kms://recent/{content_type}", mime_type="application/json")
async def recent_resource(content_type: str) -> dict[str, Any]:
    """The latest items of a content type (sorted by createdAt desc), as context."""
    res = await _client.get_data(
        build_configs(
            content_types=[content_type], sort={"createdAt": -1}, limit=10, count=True,
        )
    )
    return {"content_type": content_type, "total": res.get("count"), "recent": res.get("data")}


def main() -> None:
    """Console entry point: run the server over stdio."""
    print(
        f"[dots-kms-mcp] starting (mock={_settings.mock}) — stdio transport.",
        file=sys.stderr,
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
