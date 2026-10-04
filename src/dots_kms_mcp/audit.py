"""Safe audit events for content creation."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass(frozen=True)
class CreateActor:
    subject: str
    email: str


def emit_create_audit(
    *,
    actor: CreateActor,
    content_type: str,
    title: str,
    document: dict[str, Any],
    outcome: Literal["success", "failure"],
    content_id: str | None,
    error: str | None,
    status_code: int | None,
) -> None:
    canonical_document = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "content_create",
        "google_subject": actor.subject,
        "google_email": actor.email,
        "content_type": content_type,
        "title": title,
        "outcome": outcome,
        "request_sha256": hashlib.sha256(
            canonical_document.encode("utf-8")
        ).hexdigest(),
        "content_id": content_id,
        "status_code": status_code,
        "error": error[:300] if error is not None else None,
    }
    print(
        f"[dots-kms-mcp.audit] {json.dumps(event, separators=(',', ':'), ensure_ascii=False)}",
        file=sys.stderr,
    )
