"""Pure builder + validator for the getData ``configs`` object.

Kept free of I/O so it is trivially unit-testable. The double-stringify (wrapping
this dict in ``{"configs": json.dumps(...)}``) happens in the client, not here, so
``build_configs`` returns a plain, assertable dict.
"""

from __future__ import annotations

from typing import Any

from .errors import KmsConfigError


def build_configs(
    *,
    content_types: list[str] | None = None,
    profile_types: list[str] | None = None,
    search_term: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    find_query: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
    limit: int | None = None,
    skip: int | None = 0,
    count: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map friendly kwargs to the API's ``configs`` field names, omitting None.

    ``extra`` lets callers pass advanced fields verbatim (e.g. population, facet,
    useAggregation) without this function needing to know each one.
    """
    configs: dict[str, Any] = {}

    if content_types is not None:
        configs["contentTypes"] = content_types
    if profile_types is not None:
        configs["profileTypes"] = profile_types
    if search_term is not None:
        configs["searchTerm"] = search_term
    if filters is not None:
        configs["activeFilters"] = filters
    if find_query is not None:
        configs["findQuery"] = find_query
    if sort is not None:
        configs["activeSort"] = sort
    if projection is not None:
        configs["projection"] = projection
    if limit is not None:
        configs["limit"] = limit
    if skip is not None:
        configs["skip"] = skip
    # countData defaults to True in the API; we send it explicitly for clarity.
    configs["countData"] = bool(count)

    if extra:
        # Caller-supplied advanced fields win, but never let them break the
        # content/profile invariant — that is validated below.
        for key, value in extra.items():
            if value is not None:
                configs[key] = value

    validate_configs(configs)
    return configs


def tag_filter(tag_type: str, tag_ids: list[str]) -> dict[str, Any]:
    """DEPRECATED: builds a `tagType` activeFilter. The live getData API rejects this
    shape (HTTP 500) — use ``tag_query`` (findQuery) instead. Kept for back-compat only.
    """
    return {"target": {"filterType": "tagType", "tagType": tag_type}, "values": list(tag_ids)}


def date_range_filter(
    path: str, start: str | None = None, end: str | None = None
) -> dict[str, Any]:
    """DEPRECATED: builds a `dateRangeType` activeFilter. The live API rejects this —
    use ``date_query`` (findQuery range) instead. Kept for back-compat only.
    """
    bounds: dict[str, Any] = {}
    if start:
        bounds["start"] = start
    if end:
        bounds["end"] = end
    return {"target": {"filterType": "dateRangeType", "path": path}, "values": [bounds]}


def _find_tag_type(schema: dict[str, Any] | None, tag_type_id: str) -> dict[str, Any] | None:
    """Local tag_type lookup (kept here so configs.py stays free of schema.py imports)."""
    for entry in (schema or {}).get("tag_types", []):
        if entry.get("id") == tag_type_id:
            return entry
    return None


def tag_query(
    tags: dict[str, Any], schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a findQuery fragment filtering by embedded tag collections.

    ``tags`` maps a collection id to value(s) — display names or slugs, e.g.
    ``{"country": ["Indonesia"], "conditionAreas": ["Antenatal Care (ANC)"]}``.
    Each value is translated to the collection's filter field (``tagId`` slug, or
    ``_id`` for slug-less collections like nooraUsers) via the schema's cached
    ``values`` map; values not found there fall back to matching on ``display``.
    Multiple collections AND together; a collection that needs both an id match and
    a display fallback is combined with ``$or``.

    This is how tag filtering actually works on the live getData API — the
    activeFilters/tagType shape (``tag_filter``) is rejected with HTTP 500.
    """
    clauses: list[dict[str, Any]] = []
    for cid, raw in tags.items():
        values = [raw] if isinstance(raw, str) else list(raw or [])
        if not values:
            continue
        entry = _find_tag_type(schema, cid) or {}
        field = entry.get("filter_field", "tagId")
        cache: dict[str, str] = entry.get("values", {}) or {}
        by_display = {k.lower(): v for k, v in cache.items()}
        known_ids = set(cache.values())

        resolved: list[str] = []
        unresolved: list[str] = []
        for v in values:
            if v in known_ids:
                resolved.append(v)
            elif v.lower() in by_display:
                resolved.append(by_display[v.lower()])
            else:
                unresolved.append(v)

        field_clause = {f"tags.{cid}.data.{field}": {"$in": resolved}} if resolved else None
        disp_clause = {f"tags.{cid}.data.display": {"$in": unresolved}} if unresolved else None
        if field_clause and disp_clause:
            clauses.append({"$or": [field_clause, disp_clause]})
        elif field_clause:
            clauses.append(field_clause)
        elif disp_clause:
            clauses.append(disp_clause)

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    # Distinct keys per collection AND naturally; if any clause is an $or, AND
    # everything explicitly to avoid colliding on the "$or" key.
    if any("$or" in c for c in clauses):
        return {"$and": clauses}
    merged: dict[str, Any] = {}
    for clause in clauses:
        merged.update(clause)
    return merged


def date_query(
    field: str, start: str | None = None, end: str | None = None
) -> dict[str, Any]:
    """Build a findQuery date-range fragment: ``{field: {"$gte": start, "$lte": end}}``.

    ``start``/``end`` are ISO-8601 strings; omit either bound. Returns ``{}`` if both
    are absent.
    """
    bounds: dict[str, Any] = {}
    if start:
        bounds["$gte"] = start
    if end:
        bounds["$lte"] = end
    return {field: bounds} if bounds else {}


def merge_find_query(
    base: dict[str, Any] | None, extra: dict[str, Any] | None
) -> dict[str, Any]:
    """Combine two findQuery dicts. Disjoint keys merge (AND); colliding keys are
    wrapped in ``$and`` so neither is silently dropped.
    """
    base = base or {}
    extra = extra or {}
    if not base:
        return dict(extra)
    if not extra:
        return dict(base)
    if set(base) & set(extra):
        return {"$and": [base, extra]}
    merged = dict(base)
    merged.update(extra)
    return merged


def validate_configs(configs: dict[str, Any]) -> None:
    """Enforce the content-vs-profile invariant, mirroring the API's 400s.

    Raises ``KmsConfigError`` with wording aligned to the documented errors.
    """
    has_content = bool(configs.get("contentTypes"))
    has_profile = bool(configs.get("profileTypes"))

    if has_content and has_profile:
        raise KmsConfigError(
            "You cannot pass both content type and profile type together "
            "(contentTypes and profileTypes are mutually exclusive)."
        )
    if not has_content and not has_profile:
        raise KmsConfigError(
            "You need to specify a content type or profile type to get data "
            "(provide a non-empty contentTypes or profileTypes)."
        )
