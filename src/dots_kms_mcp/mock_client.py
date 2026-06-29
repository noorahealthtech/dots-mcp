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
        limit = configs.get("limit")
        skip = int(configs.get("skip") or 0)
        want_count = configs.get("countData", True)

        # Build the full synthetic corpus, then page it.
        all_docs = [self._doc(primary, i, search_term) for i in range(_TOTAL)]

        if limit is None:
            page = all_docs[skip:]
            next_skip = None
        else:
            limit = int(limit)
            page = all_docs[skip : skip + limit]
            consumed = skip + len(page)
            next_skip = consumed if consumed < _TOTAL else None

        result: dict[str, Any] = {"data": page}
        if want_count:
            result["count"] = _TOTAL
        if next_skip is not None:
            result["skip"] = next_skip
        return result

    @staticmethod
    def _doc(type_id: str, index: int, search_term: str | None) -> dict[str, Any]:
        topic = _TOPICS[index % len(_TOPICS)]
        if search_term:
            title = f"{search_term.title()}: {topic} ({type_id} #{index + 1})"
        else:
            title = f"{topic.title()} ({type_id} #{index + 1})"
        return {
            "_id": _object_id(f"{type_id}:{index}"),
            "meta": {
                "title": title,
                "description": (
                    f"[MOCK DATA] A sample {type_id} document about {topic}. "
                    "Replace with live results by setting KMS_AUTH_TOKEN/KMS_TENANT."
                ),
            },
            "createdAt": _iso(index),
            "updatedAt": _iso(index + 1),
        }


def mock_resolve_tag_candidates(name: str, cached_id: str | None) -> list[dict[str, Any]]:
    """Mock candidates for resolve_tag when nothing is cached."""
    return [
        {
            "_id": cached_id or _object_id(f"tag:{name.lower()}"),
            "meta": {"title": name},
            "_mock": True,
        }
    ]
