"""Generate ``kms_schema.json`` from the live KMS getData API.

The getData API has no discovery endpoint, so the MCP server reads what is
queryable from a local ``kms_schema.json``. This script builds that file from the
live tenant: it confirms each content type is readable, fetches its document count,
and harvests the tag taxonomy (collections + ``display -> tagId`` value maps) from
real documents.

Usage::

    uv run python scripts/build_schema.py [extraContentType ...]

Pass extra content-type ids (e.g. ones newly found in the web UI) as arguments to
confirm and include them. Re-run any time to refresh counts and tag values as the
knowledge base grows.

Needs live creds in ``.env`` (``KMS_AUTH_TOKEN``, ``KMS_TENANT``, ``KMS_MOCK=0``).
This is a standalone script, not the stdio server, so printing to stdout is fine.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from dots_kms_mcp.configs import build_configs
from dots_kms_mcp.getdata_client import KmsClient
from dots_kms_mcp.settings import Settings

# Confirmed readable content types for tenant `nkms`. NOTE `organisationalReports`
# uses the British "s" — the web-UI URL spelling `organizationalReports` 401s.
CONTENT_TYPES = [
    "routineVisits",
    "reports",
    "trainingReports",
    "programmaticAssetsTemplates",
    "programPerformanceReports",
    "successStory",
    "researchAndEvaluationReports",
    "learningAndSharingSessions",
    "organisationalReports",
    "toolsAndCollaterals",
]

# Per-type sample size for harvesting tag values. Tag vocabularies (geography,
# teams, condition areas, ...) saturate well within a few hundred docs.
SAMPLE_LIMIT = 400

# Friendly names for known tag collections; unknown ones are humanized from the id.
TAG_NAMES = {
    "country": "Country",
    "states": "State",
    "districts": "District",
    "facility": "Facility",
    "facilityTypes": "Facility Type",
    "conditionAreas": "Condition Area",
    "stakeholder": "Stakeholder",
    "subject": "Subject",
    "teams": "Team",
    "nooraUsers": "Noora User",
    "type": "Insight Type",
    "lens": "Lens",
}

OUT_PATH = Path("kms_schema.json")


def humanize(identifier: str) -> str:
    """camelCase / snake_case id -> Title-cased words."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", identifier).replace("_", " ")
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return spaced[:1].upper() + spaced[1:]


def err(*args: Any) -> None:
    print(*args, file=sys.stderr)


async def _post(http: httpx.AsyncClient, s: Settings, headers: dict[str, str],
                configs: dict[str, Any]) -> tuple[int | None, Any]:
    body = {"configs": json.dumps(configs, separators=(",", ":"), ensure_ascii=False)}
    try:
        r = await http.post(s.getdata_url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        return None, {"errors": [{"msg": f"{type(exc).__name__}: {exc}"}]}
    try:
        return r.status_code, r.json()
    except (json.JSONDecodeError, ValueError):
        return r.status_code, None


async def count_type(http, s, headers, ct: str) -> int | None:
    """Total docs for a content type, or None if denied/invalid."""
    _, pl = await _post(http, s, headers, build_configs(
        content_types=[ct], count=True, limit=1_000_000, extra={"useCountDAL": True}))
    if isinstance(pl, dict) and not pl.get("errors"):
        data = pl.get("data")
        if isinstance(data, list) and data:
            return data[0].get("count")
    return None


async def sample_docs(http, s, headers, ct: str) -> list[dict[str, Any]]:
    _, pl = await _post(http, s, headers, build_configs(
        content_types=[ct], count=False, limit=SAMPLE_LIMIT))
    docs = pl.get("data") if isinstance(pl, dict) else None
    return docs if isinstance(docs, list) else []


def harvest_doc(doc: Any, values: dict[str, dict[str, dict[str, str]]], cids_out: set[str]) -> None:
    """Recursively find tag groups ({collectionId, data:[{display, tagId, _id}]}).

    Records, per collection, ``display -> {tagId, _id}`` so the schema builder can pick
    a filter field (``tagId`` when slugs exist, else ``_id`` for slug-less collections
    like nooraUsers).
    """
    if isinstance(doc, dict):
        cid, data = doc.get("collectionId"), doc.get("data")
        if isinstance(cid, str) and isinstance(data, list):
            cids_out.add(cid)
            for item in data:
                if isinstance(item, dict):
                    disp = item.get("display")
                    if disp:
                        entry = values[cid].setdefault(disp, {})
                        if item.get("tagId"):
                            entry["tagId"] = item["tagId"]
                        if item.get("_id"):
                            entry["_id"] = item["_id"]
        for v in doc.values():
            harvest_doc(v, values, cids_out)
    elif isinstance(doc, list):
        for item in doc:
            harvest_doc(item, values, cids_out)


async def main() -> int:
    extra = sys.argv[1:]
    candidates = CONTENT_TYPES + [c for c in extra if c not in CONTENT_TYPES]

    s = Settings.from_env()
    if s.mock:
        err("[build_schema] ERROR: running in MOCK mode. Set KMS_AUTH_TOKEN/KMS_TENANT "
            "and KMS_MOCK=0 in .env to query the live KMS.")
        return 2
    headers = KmsClient(s)._headers

    content_types: list[dict[str, Any]] = []
    values: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    tagtype_in_types: dict[str, set[str]] = defaultdict(set)
    denied: list[str] = []

    async with httpx.AsyncClient(timeout=s.timeout) as http:
        for ct in candidates:
            total = await count_type(http, s, headers, ct)
            if total is None:
                denied.append(ct)
                err(f"[build_schema] WARN: '{ct}' is not readable (denied or wrong name "
                    f"— check spelling, e.g. organisationalReports vs organizationalReports). Skipping.")
                continue
            content_types.append({
                "id": ct,
                "name": humanize(ct),
                "description": f"{humanize(ct)} ({total} documents).",
                "count": total,
            })
            docs = await sample_docs(http, s, headers, ct)
            cids: set[str] = set()
            for d in docs:
                harvest_doc(d, values, cids)
            for cid in cids:
                tagtype_in_types[cid].add(ct)
            err(f"[build_schema] {ct}: {total} docs, {len(cids)} tag collections "
                f"(sampled {len(docs)}).")

    tag_types: list[dict[str, Any]] = []
    for cid in sorted(values):
        name = TAG_NAMES.get(cid, humanize(cid))
        items = values[cid]
        # Prefer the stable tagId slug; fall back to _id for slug-less collections.
        field = "tagId" if any(info.get("tagId") for info in items.values()) else "_id"
        value_map = {disp: info[field] for disp, info in items.items() if info.get(field)}
        tag_types.append({
            "id": cid,
            "name": name,
            "description": (f"{name} tags. Filter content with "
                            f"tags={{\"{cid}\": [\"<value>\"]}}."),
            "name_path": f"tags.{cid}.data.display",
            "filter_field": field,
            "values": dict(sorted(value_map.items())),
            "content_types": sorted(tagtype_in_types[cid]),
        })

    schema = {
        "_comment": (
            "Generated by scripts/build_schema.py from the live KMS getData API. "
            "content_types confirmed readable with live counts; tag_types harvested "
            "from real documents (values map display -> filter_field id; filter_field is "
            "'tagId' for slugged collections, '_id' for slug-less ones like nooraUsers). profile_types are "
            "empty because no profileType is accessible to this token. Re-run the script "
            "to refresh."
        ),
        "content_types": content_types,
        "profile_types": [],
        "tag_types": tag_types,
    }

    OUT_PATH.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_PATH} — {len(content_types)} content types, "
          f"{len(tag_types)} tag collections.")
    if denied:
        print(f"Skipped (not readable): {', '.join(denied)}")
    for tt in tag_types:
        print(f"  tag {tt['id']:<15} {len(tt['values']):>3} values "
              f"(by {tt['filter_field']}) on {len(tt['content_types'])} type(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
