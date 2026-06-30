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


def extract_doc_tag_ids(
    doc: dict[str, Any], tag_type: str | None = None
) -> dict[str, list[str]]:
    """Best-effort: pull tag ObjectIds out of a document's ``tags`` field.

    Returns ``{tagType: [ids]}`` (limited to ``tag_type`` when given). Used by
    related_documents to find docs sharing a source document's tags.

    NOTE: the exact shape of the ``tags`` field is deployment-specific (the docs
    reference both ``tags.<type>.data._id`` and array forms), so this handles the
    common shapes and should be confirmed against real documents.
    """
    tags = doc.get("tags")
    out: dict[str, list[str]] = {}
    if not isinstance(tags, dict):
        return out
    items = tags.items() if tag_type is None else [(tag_type, tags.get(tag_type))]
    for tt, value in items:
        ids: list[str] = []
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, list):
                ids = [d["_id"] for d in data if isinstance(d, dict) and d.get("_id")]
        elif isinstance(value, list):
            for d in value:
                if isinstance(d, dict) and d.get("_id"):
                    ids.append(d["_id"])
                elif isinstance(d, str):
                    ids.append(d)
        if ids:
            out[tt] = ids
    return out


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


# --------------------------------------------------------------------------- #
# Attachments + citations (best-effort document post-processing)
# --------------------------------------------------------------------------- #
DEFAULT_WEB_URL = "https://knowledge.noorahealth.org"

# Order attachments are surfaced in (PDFs first).
_KIND_ORDER = {"pdf": 0, "image": 1, "video": 2, "file": 3, "link": 4}
# Bare URL strings are only treated as attachments under these key-name hints,
# so we don't scoop up every URL embedded in rich-text bodies.
_LINK_KEY_HINTS = ("link", "attach", "document", "drive", "file", "pdf")


def _is_http(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _classify(content_type: str | None, url: str) -> str:
    if content_type:
        if content_type == "application/pdf":
            return "pdf"
        if content_type.startswith("image/"):
            return "image"
        if content_type.startswith("video/"):
            return "video"
        return "file"
    if url.split("?", 1)[0].lower().endswith(".pdf"):
        return "pdf"
    return "link"


def extract_doc_attachments(
    doc: dict[str, Any], kinds: list[str] | None = None
) -> list[dict[str, Any]]:
    """Best-effort: pull attachment links out of a document, anywhere in its tree.

    Handles the two shapes the getData API uses: Google Cloud Storage upload objects
    (``kind == "storage#object"`` with ``publicUrl`` + ``contentType``) and external
    link objects/strings (``{"url": ...}`` or a bare URL). Field names vary by content
    type, so this is structural rather than name-based. Returns a list, PDFs first,
    deduped by url::

        {"kind": "pdf"|"image"|"video"|"file"|"link", "filename", "url",
         "content_type", "size", "field"}

    ``kinds`` optionally restricts the result (e.g. ``["pdf"]``).
    """
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(url: str | None, filename: Any, content_type: Any, size: Any, field: str) -> None:
        if not isinstance(url, str) or url in seen:
            return
        seen.add(url)
        found.append({
            "kind": _classify(content_type if isinstance(content_type, str) else None, url),
            "filename": filename if isinstance(filename, str) else None,
            "url": url,
            "content_type": content_type if isinstance(content_type, str) else None,
            "size": size if isinstance(size, int) else None,
            "field": field,
        })

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            is_storage = node.get("kind") == "storage#object" or (
                "publicUrl" in node and "contentType" in node
            )
            if is_storage:
                add(node.get("publicUrl") or node.get("mediaLink"),
                    node.get("originalFilename") or node.get("name"),
                    node.get("contentType"), node.get("size"), path)
                return  # don't descend (avoids re-emitting mediaLink/selfLink)
            if _is_http(node.get("url")):
                meta = node.get("metadata")
                title = meta.get("title") if isinstance(meta, dict) else None
                add(node["url"], title, None, None, path)
                return  # don't descend (avoids preview-thumbnail urls)
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        elif _is_http(node):
            key = path.rsplit(".", 1)[-1].lower()
            if any(hint in key for hint in _LINK_KEY_HINTS):
                add(node, None, None, None, path)

    walk(doc, "")
    found.sort(key=lambda a: _KIND_ORDER.get(a["kind"], 9))
    if kinds:
        allowed = set(kinds)
        found = [a for a in found if a["kind"] in allowed]
    return found


def document_citation(
    doc: dict[str, Any],
    content_type: str | None = None,
    web_base: str = DEFAULT_WEB_URL,
) -> str | None:
    """Build a clickable KMS web-app deep link for a document, or None.

    Pattern: ``{web_base}/published-page/{content_type}?id={_id}``. The content type
    is taken from the ``content_type`` hint, else ``doc.metadata.contentType``, else
    ``doc.meta.kp_content_type``. Returns None if the id or content type is unknown
    (e.g. a projection stripped ``_id``).
    """
    doc_id = doc.get("_id") or doc.get("id")
    ct = (
        content_type
        or (doc.get("metadata") or {}).get("contentType")
        or (doc.get("meta") or {}).get("kp_content_type")
    )
    if not doc_id or not ct:
        return None
    return f"{web_base.rstrip('/')}/published-page/{ct}?id={doc_id}"
