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

from .auth import build_auth
from .configs import build_configs, date_query, merge_find_query, tag_query
from .errors import KmsError
from .getdata_client import build_client
from .schema import (
    _cached_schema,
    document_citation,
    extract_doc_attachments,
    extract_doc_tag_ids,
    find_tag_type,
    get_content_types,
    get_profile_types,
    get_tag_types,
)
from .settings import Settings

# Resolve settings first (it logs the chosen mock/live mode to stderr) so the
# FastMCP instance can be configured with the HTTP host/port for remote mode.
_settings = Settings.from_env()

# Under the HTTP transport the connector may require OAuth (Google-delegated). Under
# stdio there is no auth — the spec says stdio takes creds from the environment — so
# the bridge is only built for HTTP, which also keeps the mock/stdio test path clean.
_auth = build_auth(_settings) if _settings.is_http else None

_fastmcp_kwargs: dict[str, Any] = {
    "instructions": (
        "Every document returned by these tools includes a `source_url` — a clickable link "
        "to that document's page in the KMS web app — and, when include_attachments is set "
        "or via document_attachments, an `attachments` list of file URLs (PDFs, images, "
        "links). ALWAYS cite your sources: when you state something from a document, link to "
        "its source_url (and relevant attachments) as clickable markdown links so the user "
        "can verify it. Never invent a source_url; only use ones present in tool results."
    ),
    # HTTP-transport settings (ignored under stdio). bind + streaming behaviour.
    "host": _settings.host,
    "port": _settings.port,
    "stateless_http": _settings.stateless_http,
    "json_response": _settings.json_response,
}
if _auth is not None:
    _fastmcp_kwargs["auth_server_provider"] = _auth.provider
    _fastmcp_kwargs["auth"] = _auth.auth_settings

mcp = FastMCP("dots-kms", **_fastmcp_kwargs)

if _auth is not None:
    # The Google OAuth redirect lands here; we complete the login (domain check + mint
    # our own code) and redirect the user-agent back to the MCP client. This is the
    # custom return-flow handler the provider's authorize() step expects.
    from mcp.server.auth.provider import AuthorizeError
    from starlette.requests import Request
    from starlette.responses import JSONResponse, RedirectResponse

    _provider = _auth.provider

    @mcp.custom_route(_auth.callback_path, methods=["GET"])
    async def _google_oauth_callback(request: "Request"):
        try:
            location = await _provider.complete_google_login(
                request.query_params.get("code"), request.query_params.get("state")
            )
        except AuthorizeError as exc:
            return JSONResponse(
                {"error": exc.error, "error_description": exc.error_description},
                status_code=400,
            )
        return RedirectResponse(location, status_code=302)

_client = build_client(_settings)

# Hard ceiling on how many documents `collect` will pull, to protect context size.
MAX_COLLECT = 200


def _schema() -> dict[str, Any]:
    return _cached_schema(_settings.schema_path)


def _raise_readable(exc: KmsError) -> NoReturn:
    """Re-raise a KMS error as a ValueError the model can read and recover from."""
    detail = getattr(exc, "detail", None) or str(exc)
    raise ValueError(detail) from exc


def _annotate(
    doc: Any, content_type: str | None = None, include_attachments: bool = False
) -> Any:
    """Add a citation `source_url` (always) and `attachments` (opt-in) to a document."""
    if not isinstance(doc, dict):
        return doc
    url = document_citation(doc, content_type, _settings.web_url)
    if url:
        doc["source_url"] = url
    if include_attachments:
        doc["attachments"] = extract_doc_attachments(doc)
    return doc


def _annotate_result(
    result: dict[str, Any], content_type: str | None = None, include_attachments: bool = False
) -> dict[str, Any]:
    """Annotate every document in a getData ``{"data": [...]}`` response in place."""
    for doc in result.get("data") or []:
        _annotate(doc, content_type, include_attachments)
    return result


def _ct_hint(content_types: list[str] | None) -> str | None:
    """Use a single content type as the citation hint; if ambiguous, let each doc's
    metadata.contentType decide."""
    return content_types[0] if content_types and len(content_types) == 1 else None


def _resolve_tag(tag_type: str, name: str) -> dict[str, Any]:
    """Resolve a tag NAME to the identifier used for filtering, from the schema cache.

    Tags filter via findQuery on ``tags.<collection>.data.<filter_field>`` (see
    ``tag_query``), where ``filter_field`` is ``tagId`` for slugged collections or
    ``_id`` for slug-less ones (e.g. nooraUsers). This returns what ``name`` maps to,
    so callers can report it. Match is case-insensitive on the display name.

    Returns {"tag_type", "name", "display"?, "value"?, "filter_field"?,
    "source": "cache"|"not_found", "candidates"?: [near-matches]}.
    """
    entry = find_tag_type(_schema(), tag_type) or {}
    field = entry.get("filter_field", "tagId")
    values: dict[str, str] = entry.get("values", {}) or {}
    by_display = {disp.lower(): (disp, val) for disp, val in values.items()}

    hit = by_display.get(name.lower())
    if hit:
        disp, val = hit
        return {
            "tag_type": tag_type, "name": name, "display": disp,
            "value": val, "filter_field": field, "source": "cache",
        }
    # Not cached: tag_query will still match it on `display`. Offer near-matches.
    candidates = [disp for disp in values if name.lower() in disp.lower()][:8]
    return {"tag_type": tag_type, "name": name, "source": "not_found", "candidates": candidates}


async def _collect(
    *,
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    find_query: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    max_results: int = 50,
    page_size: int = 25,
    include_attachments: bool = False,
) -> dict[str, Any]:
    """Auto-paginate getData up to ``max_results``. Shared by the collect tool and
    compare_regions. Honors the returned ``skip``; stops at the cap or last page.
    """
    if tags:
        find_query = merge_find_query(find_query, tag_query(tags, _schema()))
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
    docs = collected[:cap]
    for doc in docs:
        _annotate(doc, _ct_hint(content_types), include_attachments)
    return {
        "data": docs, "count": total,
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
    tags: dict[str, list[str]] | None = None,
    find_query: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    limit: int | None = 10,
    skip: int = 0,
    count: bool = True,
    include_attachments: bool = False,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Search and filter the knowledge base. This is the primary query tool.

    Query EITHER content OR profiles — never both in one call:
      - content_types: e.g. ["reports"], ["routineVisits"]. Use list_content_types to discover.
      - profile_types: e.g. ["volunteers"]. Use list_profile_types to discover.
    Exactly one of content_types / profile_types must be provided.

    search_term: full-text query (e.g. "breastfeeding"); results come back in
      relevance order. Omit to browse/filter without text search.

    tags: THE way to filter by tag — a map of tag collection -> list of values
      (human display names or slugs), e.g.
      {"country": ["Indonesia"], "conditionAreas": ["Antenatal Care (ANC)"]}.
      Multiple values in a list are OR'd; multiple collections are AND'd. Discover
      collections and their valid values with list_tag_types (values differ by
      content type — see each tag type's "content_types"). No ObjectIds needed.

    Pagination: pass limit (default 10) and skip (default 0). The response may
    include a "skip" value — pass that exact value back as `skip` to get the next
    page. When the response has no "skip", you've reached the last page. The
    response "count" is the total number of matching documents.

    sort: {"<field>": 1 | -1}, e.g. {"kp_date_created": -1} for newest first.
    projection: MongoDB projection to limit returned fields,
      e.g. {"meta.title": 1, "main.giveASummary": 1, "kp_date_created": 1}.
    find_query: raw MongoDB conditions merged into the query (AND'd with `tags`),
      e.g. {"kp_published_status": "published"} or a date range
      {"kp_date_created": {"$gte": "2024-01-01", "$lte": "2025-01-01"}}. Tag
      filtering also goes through findQuery under the hood
      ({"tags.<collection>.data.tagId": {"$in": [...]}}); prefer the `tags` param.

    filters: ADVANCED/raw activeFilters passthrough. NOTE: the tagType/activeFilters
      mechanism is NOT supported by this tenant's API (returns HTTP 500) — use `tags`
      and `find_query` instead. Left here only for raw experimentation.

    include_attachments: when true, each returned document also gets an `attachments`
      list (PDFs/images/links with direct urls).

    Returns {"data": [...documents], "count"?: int, "skip"?: int}. EVERY returned
    document carries a `source_url` (its KMS web-app page) — cite it as a clickable
    link when you use the document. For advanced options (population/joins, facet,
    aggregation), use query_getdata.
    """
    try:
        if tags:
            find_query = merge_find_query(find_query, tag_query(tags, _schema()))
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
        result = await _client.get_data(configs)
        return _annotate_result(result, _ct_hint(content_types), include_attachments)
    except KmsError as exc:
        _raise_readable(exc)


@mcp.tool()
async def get_document(
    document_id: str,
    content_type: str | None = None,
    profile_type: str | None = None,
    projection: dict[str, Any] | None = None,
    include_attachments: bool = False,
) -> dict[str, Any]:
    """Fetch a single document by its _id.

    Provide the type the document belongs to: exactly one of content_type (e.g.
    "reports") or profile_type (e.g. "volunteers"). projection optionally limits
    returned fields. include_attachments adds an `attachments` list (PDFs/images/links).

    Returns {"document": {...}} when found (the document carries a `source_url` to its
    KMS page — cite it), or {"document": None, "found": false} when no document matches.
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
    return {"document": _annotate(data[0], content_type, include_attachments), "found": True}


# --------------------------------------------------------------------------- #
# Discovery (from the local, developer-maintained schema)
# --------------------------------------------------------------------------- #
@mcp.tool()
async def list_content_types() -> list[dict[str, Any]]:
    """List the content types you can query (e.g. reports, routineVisits, successStory).

    Sourced from a local, developer-maintained schema (kms_schema.json), not a
    live endpoint. Call this before building a content query if you are unsure
    which content_types exist (each entry includes a doc count). Each entry:
    {id, name, description, count?}.
    """
    return get_content_types(_schema())


@mcp.tool()
async def list_profile_types() -> list[dict[str, Any]]:
    """List the profile types you can query (people/entities, as opposed to content).

    Sourced from the local kms_schema.json. A query targets content OR profiles,
    never both. NOTE: this tenant exposes no readable profile types (the list may be
    empty) — most queries use content_types. Each entry: {id, name, description}.
    """
    return get_profile_types(_schema())


@mcp.tool()
async def list_tag_types() -> list[dict[str, Any]]:
    """List tag collections available for filtering (e.g. country, states, districts,
    conditionAreas, subject, stakeholder, teams, nooraUsers, type, lens).

    Sourced from the local kms_schema.json (harvested from live data). Pass a tag
    type's id and one of its display values to the `tags` param of search_knowledge /
    collect / count_only / facet_counts, e.g. tags={"country": ["Indonesia"]}.
    Each entry: {id, name, description, name_path, filter_field, values: {display: id},
    content_types: [...]}. "content_types" lists which content types actually carry
    that collection — tag vocabularies differ by content type.
    """
    return get_tag_types(_schema())


@mcp.tool()
async def resolve_tag(
    tag_type: str,
    name: str,
) -> dict[str, Any]:
    """Look up what a tag NAME maps to in a collection (mostly a sanity check).

    You usually DON'T need this — just pass display names straight to the `tags`
    param of search_knowledge (e.g. tags={"states": ["East Java"]}); it resolves
    names for you. Use this only to confirm a name exists or to see near-matches.

    Resolves from the local schema cache (case-insensitive on the display name).
    Returns {"tag_type", "name", "display"?, "value"?, "filter_field"?,
    "source": "cache"|"not_found", "candidates"?: [near-matches]}.
    """
    return _resolve_tag(tag_type, name)


# --------------------------------------------------------------------------- #
# Retrieval power tools (group A): unlock getData features + reliable big pulls
# --------------------------------------------------------------------------- #
@mcp.tool()
async def count_only(
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    tags: dict[str, list[str]] | None = None,
    find_query: dict[str, Any] | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return ONLY the total number of matching documents — no documents fetched.

    Uses the API's optimized count mode (useCountDAL), so it's cheap. Call this when
    the user asks "how many…", or before a large pull to decide how many pages to
    fetch. Provide exactly one of content_types / profile_types. Filter by tag with
    `tags`, e.g. tags={"country": ["Indonesia"]} (see search_knowledge). Returns
    {"count": int}.
    """
    try:
        if tags:
            find_query = merge_find_query(find_query, tag_query(tags, _schema()))
        configs = build_configs(
            content_types=content_types, profile_types=profile_types,
            search_term=search_term, filters=filters, find_query=find_query,
            limit=0, count=True, extra={"useCountDAL": True},
        )
        result = await _client.get_data(configs)
    except KmsError as exc:
        _raise_readable(exc)
    # useCountDAL returns the count nested per type ({"data": [{"count": N, ...}]});
    # the mock returns a top-level "count". Read top-level first, else sum the rows.
    count = result.get("count")
    if count is None:
        rows = result.get("data") or []
        per_type = [r["count"] for r in rows if isinstance(r, dict) and isinstance(r.get("count"), int)]
        count = sum(per_type) if per_type else None
    return {"count": count}


@mcp.tool()
async def facet_counts(
    facets: list[dict[str, Any]],
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    tags: dict[str, list[str]] | None = None,
    find_query: dict[str, Any] | None = None,
    search_term: str | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Get counts grouped by a field/tag, WITHOUT fetching documents.

    `facets` is a list like [{"field": "category", "tagType": "category"}]; each entry
    groups results and returns a count per bucket. Great for overviews and comparisons
    ("how many reports per conditionArea", distributions per region). Provide exactly
    one of content_types / profile_types. Narrow the population first with `tags`
    (e.g. tags={"country": ["India"]}) or `find_query`. Returns the API's faceted counts.
    """
    try:
        if tags:
            find_query = merge_find_query(find_query, tag_query(tags, _schema()))
        configs = build_configs(
            content_types=content_types, profile_types=profile_types,
            search_term=search_term, filters=filters, find_query=find_query, count=True,
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
    include_attachments: bool = False,
) -> dict[str, Any]:
    """Fetch several full documents by their _ids in one call.

    Provide the type they belong to (one of content_type / profile_type) and a list of
    document_ids. `populate` optionally expands reference fields (joins), e.g.
    [{"path": "meta.kp_contributed_by", "select": "name email"}]. include_attachments
    adds an `attachments` list per doc. Use after a search returns ids when you need
    full document bodies for synthesis. Each document carries a `source_url` to cite.
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
    for doc in docs:
        _annotate(doc, content_type, include_attachments)
    returned = {d.get("_id") for d in docs}
    missing = [i for i in document_ids if i not in returned]
    return {"documents": docs, "found": len(docs), "missing": missing}


@mcp.tool()
async def collect(
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    tags: dict[str, list[str]] | None = None,
    find_query: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    max_results: int = 50,
    page_size: int = 25,
    include_attachments: bool = False,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Gather up to `max_results` matching documents in ONE call — the server
    paginates for you, so you don't manage `skip`.

    Use for "a bunch of…" or "a representative sample" requests. Capped at 200 to
    protect context size (the response notes if it truncated). Provide exactly one of
    content_types / profile_types. Filter by tag with `tags`, e.g.
    tags={"states": ["East Java"]} (see search_knowledge). include_attachments adds an
    `attachments` list per doc; every doc carries a `source_url` to cite. Returns
    {"data": [...], "count": <total>, "pages_fetched": int, "truncated": bool}.
    """
    try:
        return await _collect(
            content_types=content_types, profile_types=profile_types,
            search_term=search_term, filters=filters, find_query=find_query,
            tags=tags, sort=sort, projection=projection,
            max_results=max_results, page_size=page_size,
            include_attachments=include_attachments,
        )
    except KmsError as exc:
        _raise_readable(exc)


@mcp.tool()
async def document_attachments(
    document_id: str,
    content_type: str | None = None,
    profile_type: str | None = None,
    kinds: list[str] | None = None,
) -> dict[str, Any]:
    """Get the attachments (PDFs, images, videos, external links) of one document.

    Pulls the file/link references out of the document — each as
    {kind: "pdf"|"image"|"video"|"file"|"link", filename, url, content_type, size}.
    PDFs come first. `kinds` filters (e.g. ["pdf"] for just PDFs). The urls are direct
    and openable; surface them to the user as clickable links. Provide the type the
    document belongs to (one of content_type / profile_type).
    Returns {"document_id", "found", "source_url", "attachments": [...], "count": int}.
    """
    try:
        result = await _client.get_data(
            build_configs(
                content_types=[content_type] if content_type else None,
                profile_types=[profile_type] if profile_type else None,
                find_query={"_id": document_id}, limit=1, count=False,
            )
        )
    except KmsError as exc:
        _raise_readable(exc)
    doc = (result.get("data") or [None])[0]
    if not doc:
        return {"document_id": document_id, "found": False, "attachments": [], "count": 0}
    attachments = extract_doc_attachments(doc, kinds=kinds)
    return {
        "document_id": document_id, "found": True,
        "source_url": document_citation(doc, content_type, _settings.web_url),
        "attachments": attachments, "count": len(attachments),
    }


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
    """Search content filtered by a single tag NAME in one step.

    e.g. tag_type="conditionAreas", tag_name="Antenatal Care (ANC)". Builds the tag
    findQuery for you — equivalent to search_knowledge with tags={tag_type:[tag_name]}.
    For multiple tags/collections at once, use search_knowledge's `tags` param directly.
    Returns the search result plus "resolved_tag": {tag_type, name, value?, source}.
    """
    tag = _resolve_tag(tag_type, tag_name)
    try:
        configs = build_configs(
            content_types=content_types, search_term=search_term,
            find_query=tag_query({tag_type: [tag_name]}, _schema()),
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
    documents sharing those tag ids via findQuery (excluding the source). `by_tag_type`
    limits which tag collection defines "related" (default: every collection on the
    document — a match must share a tag in EACH). Each related doc carries a `source_url`
    to cite. Returns {"document_id", "found", "filtered_on": {tagType: [ids]}, "related": [...]}.
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
        find_query: dict[str, Any] = {"_id": {"$ne": document_id}}
        for tt, ids in tag_ids.items():
            if ids:
                find_query[f"tags.{tt}.data._id"] = {"$in": ids}
        if len(find_query) == 1:  # only the _id exclusion -> nothing to match on
            return {
                "document_id": document_id, "found": True, "filtered_on": {},
                "related": [], "note": "source document has no extractable tags to match on",
            }

        rel_res = await _client.get_data(
            build_configs(
                content_types=[content_type], find_query=find_query,
                limit=limit, count=False,
            )
        )
    except KmsError as exc:
        _raise_readable(exc)
    related = rel_res.get("data") or []
    for doc in related:
        _annotate(doc, content_type)
    return {
        "document_id": document_id, "found": True,
        "filtered_on": tag_ids, "related": related,
    }


@mcp.tool()
async def compare_regions(
    region_a: str,
    region_b: str,
    content_types: list[str] | None = None,
    tag_type: str = "states",
    period: dict[str, Any] | None = None,
    sample_size: int = 20,
    date_field: str = "kp_date_created",
) -> dict[str, Any]:
    """Pull comparable samples of content for two regions so you can cross-synthesize them.

    Filters each region by `tag_type` (default "states") via tag findQuery, plus an
    optional date window, and collects up to `sample_size` documents per region.
    `period` is an optional {"start": ISO8601, "end": ISO8601} window on `date_field`
    (default "kp_date_created"; note this data has no "createdAt"). The server
    guarantees symmetric, complete retrieval; YOU do the synthesis — cite _id + title,
    and only cite ids present in the results. Returns
    {"region_a": {region, resolved_tag, count, documents}, "region_b": {...},
     "shared_tag_type", "content_types"}.
    """
    content_types = content_types or ["reports"]
    period = period or {}
    try:
        out: dict[str, Any] = {"shared_tag_type": tag_type, "content_types": content_types}
        for key, region in (("region_a", region_a), ("region_b", region_b)):
            tag = _resolve_tag(tag_type, region)
            find_query = tag_query({tag_type: [region]}, _schema())
            if period.get("start") or period.get("end"):
                find_query = merge_find_query(
                    find_query, date_query(date_field, period.get("start"), period.get("end"))
                )
            collected = await _collect(
                content_types=content_types, find_query=find_query,
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
      - facet: [{"field": "conditionAreas", "tagType": "conditionAreas"}]  (counts per group)
      - useAggregation: {"groupByWithLookup": true}
      - lookupConfig, taggedResourcesCount, ksConfig, etc.

    `configs` must still contain exactly one of contentTypes / profileTypes. Tag filters
    go in findQuery, e.g. {"tags.country.data.tagId": {"$in": ["indonesia"]}}.
    Example: {"contentTypes": ["reports"], "limit": 10,
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
        "to pull a symmetric sample for each region. (Or manually: collect(content_types=..., "
        "tags={\"states\": [region]}) for each.)\n"
        "2. Read both sets; identify common themes AND notable differences.\n"
        "3. Write a cross-synthesis. Cite every document you reference as a clickable "
        "link using its `source_url` (e.g. [title](source_url)); ONLY cite source_urls "
        "that appear in the tool results — never invent one.\n"
        "4. If either region returned few results, say so explicitly."
    )


@mcp.prompt()
def kms_research(topic: str, content_types: str = "reports") -> str:
    """Guided research recipe: a thorough, cited brief on a topic."""
    return (
        f'Research the topic "{topic}" in the knowledge base '
        f"(content type(s): {content_types}).\n\n"
        "Steps:\n"
        f'1. Run search_knowledge with search_term="{topic}".\n'
        "2. Broaden recall: run 2-3 more searches with synonyms / related terms.\n"
        "3. Use collect (or paginate) to gather a representative sample, not just page one.\n"
        "4. Synthesize a concise brief grouped by sub-theme. Cite each claim with a "
        "clickable link to the document's `source_url` (e.g. [title](source_url)); only "
        "cite source_urls present in tool results.\n"
        "5. Note any gaps or thin areas you found."
    )


@mcp.prompt()
def kms_brief(content_type: str) -> str:
    """Overview/digest recipe for one content type."""
    return (
        f'Produce an overview digest of the "{content_type}" content type.\n\n'
        "Steps:\n"
        f"1. Use count_only to get the total number of {content_type}.\n"
        '2. Use facet_counts (e.g. facets=[{"field": "conditionAreas", "tagType": "conditionAreas"}]) '
        "for a breakdown.\n"
        '3. Use collect with sort={"kp_date_created": -1} to gather the most recent items.\n'
        "4. Summarize: total volume, distribution across categories, and 3-5 recent "
        "highlights — cite each as a clickable link to its `source_url`."
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
    """Console entry point: run the server over the configured transport.

    Defaults to stdio (Claude Desktop / local). Set ``KMS_TRANSPORT=streamable-http``
    (plus ``KMS_HOST``/``KMS_PORT``) to serve the remote MCP connector over HTTP.
    """
    transport = _settings.transport
    if _settings.is_http:
        where = f"http://{_settings.host}:{_settings.port}/mcp"
        auth_state = "OAuth ON" if _auth is not None else "NO AUTH"
        pub_state = "published-only" if _settings.published_only else "ALL docs (incl. drafts)"
        print(
            f"[dots-kms-mcp] starting (mock={_settings.mock}) — {transport} on {where} "
            f"[{auth_state}; {pub_state}].",
            file=sys.stderr,
        )
        if _auth is None:
            print(
                "[dots-kms-mcp] WARNING: HTTP transport with NO authentication — the /mcp "
                "endpoint is OPEN. Set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET (+ KMS_PUBLIC_URL) "
                "to enable the OAuth bridge before exposing real data publicly.",
                file=sys.stderr,
            )
    else:
        print(
            f"[dots-kms-mcp] starting (mock={_settings.mock}) — {transport} transport.",
            file=sys.stderr,
        )
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
