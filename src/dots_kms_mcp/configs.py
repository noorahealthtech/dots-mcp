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
    """Build a `tagType` activeFilter entry matching documents tagged with any of
    ``tag_ids`` under ``tag_type``. Shared by search_by_tag_name and compare_regions.
    """
    return {"target": {"filterType": "tagType", "tagType": tag_type}, "values": list(tag_ids)}


def date_range_filter(
    path: str, start: str | None = None, end: str | None = None
) -> dict[str, Any]:
    """Build a `dateRangeType` activeFilter on ``path`` (ISO-8601 ``start``/``end``)."""
    bounds: dict[str, Any] = {}
    if start:
        bounds["start"] = start
    if end:
        bounds["end"] = end
    return {"target": {"filterType": "dateRangeType", "path": path}, "values": [bounds]}


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
