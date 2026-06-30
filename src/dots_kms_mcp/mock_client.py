"""In-memory mock of the getData API.

Returns deterministic, realistically-shaped sample documents so the server can be
loaded into Claude and demonstrate the full tool-calling loop before real
credentials exist. It honors the parts of ``configs`` that make a demo feel real:
content-vs-profile selection, ``limit``/``skip`` pagination, ``countData``, and
``searchTerm`` (folded into titles so relevance ordering is visible).

Determinism note: ids are derived from a hash of the content type + index (no
randomness), so the same query always yields the same data.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .configs import validate_configs

# Synthetic corpus size per type, matching the docs' example count.
_TOTAL = 47

# Topic words to vary titles/descriptions deterministically.
_TOPICS = [
    "maternal health",
    "newborn care",
    "breastfeeding",
    "nutrition",
    "immunization",
    "hygiene",
    "danger signs",
    "postpartum recovery",
    "family planning",
    "mental wellbeing",
]

# Deterministic tag vocabularies (display, tagId) matching the packaged default schema,
# so tag findQuery filtering is exercised in mock mode.
_COUNTRIES = [("India", "india"), ("Indonesia", "indonesia"), ("Bangladesh", "bangladesh")]
_STATES = [("Karnataka", "karnataka"), ("Punjab", "punjab"), ("Maharashtra", "maharashtra")]
_CONDITIONS = [("Antenatal Care (ANC)", "antenatal_care_anc"), ("Newborn Care", "newborn_care")]


def _values_at(node: Any, parts: list[str]) -> list[Any]:
    """Resolve a dotted path, flattening through arrays (mini-Mongo path lookup)."""
    if not parts:
        return [node]
    if isinstance(node, list):
        out: list[Any] = []
        for item in node:
            out.extend(_values_at(item, parts))
        return out
    if isinstance(node, dict) and parts[0] in node:
        return _values_at(node[parts[0]], parts[1:])
    return []


def _match_field(values: list[Any], cond: Any) -> bool:
    """Match the actual values at a path against a scalar or operator condition."""
    if isinstance(cond, dict):
        for op, operand in cond.items():
            if op == "$in":
                if not any(v in operand for v in values):
                    return False
            elif op == "$ne":
                if any(v == operand for v in values):
                    return False
            elif op == "$gte":
                if not any(isinstance(v, str) and v >= operand for v in values):
                    return False
            elif op == "$lte":
                if not any(isinstance(v, str) and v <= operand for v in values):
                    return False
            elif op == "$elemMatch":
                # values here are the array elements; each is matched as a sub-doc.
                if not any(_doc_matches(v, operand) for v in values if isinstance(v, dict)):
                    return False
            else:
                return False
        return True
    return cond in values


def _doc_matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    """Evaluate a findQuery (incl. $and/$or and dotted tag/date paths) against a doc."""
    for key, cond in query.items():
        if key == "$and":
            if not all(_doc_matches(doc, sub) for sub in cond):
                return False
        elif key == "$or":
            if not any(_doc_matches(doc, sub) for sub in cond):
                return False
        else:
            if not _match_field(_values_at(doc, key.split(".")), cond):
                return False
    return True


def _object_id(seed: str) -> str:
    """A deterministic 24-hex-char id that looks like a Mongo ObjectId."""
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]


def _iso(day: int) -> str:
    """A stable ISO-8601 timestamp derived from an index (no real clock)."""
    # Spread documents across 2024 deterministically.
    month = (day % 12) + 1
    dom = (day % 27) + 1
    return f"2024-{month:02d}-{dom:02d}T10:30:00.000Z"


class MockKmsClient:
    """Drop-in replacement for ``KmsClient`` that never touches the network."""

    async def get_data(self, configs: dict[str, Any]) -> dict[str, Any]:
        # Behave like the real API on the content/profile invariant.
        validate_configs(configs)

        types = configs.get("contentTypes") or configs.get("profileTypes") or []
        primary = types[0] if types else "items"
        search_term = configs.get("searchTerm")
        find_query = configs.get("findQuery") or {}
        facets = configs.get("facet")

        # --- Count-only / faceting mode (useCountDAL) ---
        # count_only and facet_counts hit this branch: no documents are fetched.
        if configs.get("useCountDAL") or facets:
            if find_query:
                n = sum(
                    1 for i in range(_TOTAL)
                    if _doc_matches(self._doc(primary, i, search_term), find_query)
                )
            else:
                n = _TOTAL
            result: dict[str, Any] = {"count": n}
            if facets:
                result["facets"] = self._facets(facets)
                result["data"] = []
            return result

        # --- Fetch-by-id (get_document / get_documents) ---
        id_cond = find_query.get("_id")
        if isinstance(id_cond, str):
            return {"data": [self._doc_for_id(primary, id_cond)], "count": 1}
        if isinstance(id_cond, dict) and "$in" in id_cond:
            ids = id_cond.get("$in") or []
            return {"data": [self._doc_for_id(primary, i) for i in ids], "count": len(ids)}

        # --- Normal paginated listing ---
        limit = configs.get("limit")
        skip = int(configs.get("skip") or 0)
        want_count = configs.get("countData", True)

        all_docs = [self._doc(primary, i, search_term) for i in range(_TOTAL)]
        # Honor findQuery conditions: tag filters (tags.<coll>.data.<field>), the
        # {"_id": {"$ne": id}} exclusion used by related_documents, date ranges, $and/$or.
        if find_query:
            all_docs = [d for d in all_docs if _doc_matches(d, find_query)]

        if limit is None:
            page = all_docs[skip:]
            next_skip = None
        else:
            limit = int(limit)
            page = all_docs[skip : skip + limit]
            consumed = skip + len(page)
            next_skip = consumed if consumed < len(all_docs) else None

        listing: dict[str, Any] = {"data": page}
        if want_count:
            listing["count"] = len(all_docs)
        if next_skip is not None:
            listing["skip"] = next_skip
        return listing

    @staticmethod
    def _facets(facets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Deterministic faceted buckets (counts roughly summing to the corpus)."""
        presets = {
            "category": [("Maternal Health", 14), ("Newborn Care", 12), ("Nutrition", 11), ("Hygiene", 10)],
            "states": [("Karnataka", 16), ("Punjab", 12), ("Maharashtra", 10), ("Other", 9)],
        }
        default = [("Group A", 18), ("Group B", 15), ("Group C", 14)]
        out: dict[str, list[dict[str, Any]]] = {}
        for f in facets:
            field = f.get("field") or f.get("tagType") or "field"
            buckets = presets.get(field) or presets.get(f.get("tagType", "")) or default
            out[field] = [{"value": v, "count": c} for v, c in buckets]
        return out

    @staticmethod
    def _doc(type_id: str, index: int, search_term: str | None) -> dict[str, Any]:
        topic = _TOPICS[index % len(_TOPICS)]
        if search_term:
            title = f"{search_term.title()}: {topic} ({type_id} #{index + 1})"
        else:
            title = f"{topic.title()} ({type_id} #{index + 1})"
        country = _COUNTRIES[index % len(_COUNTRIES)]
        state = _STATES[index % len(_STATES)]
        cond = _CONDITIONS[index % len(_CONDITIONS)]
        return {
            "_id": _object_id(f"{type_id}:{index}"),
            "metadata": {"contentType": type_id},
            "meta": {
                "title": title,
                "description": (
                    f"[MOCK DATA] A sample {type_id} document about {topic}. "
                    "Replace with live results by setting KMS_AUTH_TOKEN/KMS_TENANT."
                ),
            },
            # Synthetic attachments (GCS-object shape + a bare doc link) so the
            # attachment extractor / include_attachments / document_attachments work.
            "main": {
                "uploadDocumentInPDFFormat": [{
                    "kind": "storage#object", "contentType": "application/pdf",
                    "originalFilename": f"{type_id}_{index}.pdf",
                    "publicUrl": f"https://storage.googleapis.com/mock-bucket/{type_id}_{index}.pdf",
                    "mediaLink": f"https://storage.googleapis.com/download/{type_id}_{index}.pdf",
                    "size": 1000 + index,
                }],
                "uploadImages": [{
                    "kind": "storage#object", "contentType": "image/jpeg",
                    "originalFilename": f"{type_id}_{index}.jpeg",
                    "publicUrl": f"https://storage.googleapis.com/mock-bucket/{type_id}_{index}.jpeg",
                    "size": 500 + index,
                }],
                "attachALinkToTheDocument": f"https://docs.google.com/document/d/mock{index}",
            },
            # Deterministic tag groups (display + tagId + _id) so findQuery tag filters,
            # related_documents, and population demos work in mock mode.
            "tags": {
                "country": {"collectionId": "country", "data": [
                    {"display": country[0], "tagId": country[1],
                     "_id": _object_id(f"country:{country[1]}")}]},
                "states": {"collectionId": "states", "data": [
                    {"display": state[0], "tagId": state[1],
                     "_id": _object_id(f"states:{state[1]}")}]},
                "conditionAreas": {"collectionId": "conditionAreas", "data": [
                    {"display": cond[0], "tagId": cond[1],
                     "_id": _object_id(f"conditionAreas:{cond[1]}")}]},
            },
            "kp_published_status": "published" if index % 3 == 0 else "draft",
            "kp_date_created": _iso(index),
            "createdAt": _iso(index),
            "updatedAt": _iso(index + 1),
        }

    @staticmethod
    def _doc_for_id(type_id: str, doc_id: str) -> dict[str, Any]:
        """A stable document carrying a caller-requested ``_id`` (for fetch-by-id)."""
        index = int(_object_id(f"id:{doc_id}"), 16) % _TOTAL
        doc = MockKmsClient._doc(type_id, index, None)
        doc["_id"] = doc_id
        return doc


def mock_resolve_tag_candidates(name: str, cached_id: str | None) -> list[dict[str, Any]]:
    """Mock candidates for resolve_tag when nothing is cached."""
    return [
        {
            "_id": cached_id or _object_id(f"tag:{name.lower()}"),
            "meta": {"title": name},
            "_mock": True,
        }
    ]
