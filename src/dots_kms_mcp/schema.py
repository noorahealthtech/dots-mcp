"""Load and query the developer-maintained discovery schema.

The getData API has no live endpoint to list content/profile/tag types or to
resolve tag names to ObjectIds, so this local JSON file is the source of truth.
Resolution order for the active schema:

1. ``KMS_SCHEMA_PATH`` (or the ``schema_path`` passed in), if set.
2. A ``kms_schema.json`` in the current working directory, if present.
3. The packaged default (``data/kms_schema.default.json``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

_DEFAULT_RESOURCE = "kms_schema.default.json"


def _load_packaged_default() -> dict[str, Any]:
    with resources.files("dots_kms_mcp.data").joinpath(_DEFAULT_RESOURCE).open(
        "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def load_schema(schema_path: str | None = None) -> dict[str, Any]:
    """Load the discovery schema dict.

    If ``schema_path`` is given it must exist (a missing explicit path is an
    error, so misconfiguration is loud rather than silently falling back).
    """
    if schema_path:
        path = Path(schema_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"KMS schema file not found at {path}. "
                "Copy kms_schema.example.json to that location, or unset KMS_SCHEMA_PATH."
            )
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    cwd_schema = Path.cwd() / "kms_schema.json"
    if cwd_schema.is_file():
        with cwd_schema.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    return _load_packaged_default()


@lru_cache(maxsize=8)
def _cached_schema(schema_path: str | None) -> dict[str, Any]:
    return load_schema(schema_path)


def get_content_types(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(schema.get("content_types", []))


def get_profile_types(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(schema.get("profile_types", []))


def get_tag_types(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Return tag types with their cached name->id maps included."""
    return list(schema.get("tag_types", []))


def find_tag_type(schema: dict[str, Any], tag_type_id: str) -> dict[str, Any] | None:
    for entry in schema.get("tag_types", []):
        if entry.get("id") == tag_type_id:
            return entry
    return None


def resolve_tag_from_cache(
    schema: dict[str, Any], tag_type_id: str, name: str
) -> str | None:
    """Look up a tag's ObjectId by name from the schema cache (case-insensitive).

    Returns the id string, or None if the tag type or name is not cached.
    """
    entry = find_tag_type(schema, tag_type_id)
    if not entry:
        return None
    values: dict[str, str] = entry.get("values", {}) or {}
    if name in values:
        return values[name]
    lowered = {k.lower(): v for k, v in values.items()}
    return lowered.get(name.lower())
