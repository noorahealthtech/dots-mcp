from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest

from dots_kms_mcp.audit import CreateActor, emit_create_audit


def test_create_actor_is_immutable():
    actor = CreateActor(subject="google-sub-123", email="writer@noorahealth.org")

    with pytest.raises(FrozenInstanceError):
        actor.email = "other@noorahealth.org"


def test_success_audit_has_actor_and_content_id_but_no_document(capsys):
    actor = CreateActor(subject="google-sub-123", email="writer@noorahealth.org")
    emit_create_audit(
        actor=actor,
        content_type="routineVisits",
        title="A visit",
        document={"main": {"title": "A visit", "secretNotes": "do not log"}},
        outcome="success",
        content_id="6ac288dd870810e6b6d21048",
        error=None,
        status_code=200,
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    line = captured.err.strip()
    prefix = "[dots-kms-mcp.audit] "
    assert line.startswith(prefix)
    assert line.count("\n") == 0
    event = json.loads(line[len(prefix):])
    assert event["event"] == "content_create"
    assert event["google_subject"] == "google-sub-123"
    assert event["google_email"] == "writer@noorahealth.org"
    assert event["content_type"] == "routineVisits"
    assert event["title"] == "A visit"
    assert event["outcome"] == "success"
    assert event["content_id"] == "6ac288dd870810e6b6d21048"
    assert event["status_code"] == 200
    assert event["error"] is None
    assert event["request_sha256"] == (
        "697079f7b9700cb00d9d14224be6c8c458857f1ab065e63cc5a21dde1b69894b"
    )
    assert event["timestamp"].endswith("Z")
    timestamp = datetime.fromisoformat(event["timestamp"].removesuffix("Z") + "+00:00")
    assert timestamp.utcoffset() == timedelta(0)
    assert "secretNotes" not in line
    assert "do not log" not in line


def test_failure_audit_has_bounded_error_but_no_document_token(capsys):
    token = "Bearer kms-secret-token"
    emit_create_audit(
        actor=CreateActor(subject="google-sub-123", email="writer@noorahealth.org"),
        content_type="routineVisits",
        title="Failed visit",
        document={"authorization": token, "body": "private content"},
        outcome="failure",
        content_id=None,
        error="x" * 350,
        status_code=503,
    )

    line = capsys.readouterr().err.strip()
    prefix = "[dots-kms-mcp.audit] "
    event = json.loads(line[len(prefix):])
    assert event["outcome"] == "failure"
    assert event["content_id"] is None
    assert event["status_code"] == 503
    assert event["error"] == "x" * 300
    assert token not in line
    assert "private content" not in line


@pytest.mark.parametrize(
    "error,secret,expected",
    [
        (
            "Request failed: Authorization: bEaReR bearer-secret while publishing",
            "bearer-secret",
            "Request failed: Authorization: bEaReR [REDACTED] while publishing",
        ),
        (
            "Upload failed, X-AUTH-TOKEN: header-secret, retry later",
            "header-secret",
            "Upload failed, X-AUTH-TOKEN: [REDACTED], retry later",
        ),
        (
            "API rejected ToKeN=query-secret; request denied",
            "query-secret",
            "API rejected ToKeN=[REDACTED]; request denied",
        ),
        (
            "Refresh failed ACCESS_TOKEN=access-secret details follow",
            "access-secret",
            "Refresh failed ACCESS_TOKEN=[REDACTED] details follow",
        ),
        (
            "OAuth error CLIENT_SECRET=client-secret, configuration invalid",
            "client-secret",
            "OAuth error CLIENT_SECRET=[REDACTED], configuration invalid",
        ),
    ],
)
def test_failure_audit_redacts_secrets_from_error(
    capsys, error, secret, expected
):
    emit_create_audit(
        actor=CreateActor(subject="google-sub-123", email="writer@noorahealth.org"),
        content_type="routineVisits",
        title="Failed visit",
        document={"main": {"title": "Failed visit"}},
        outcome="failure",
        content_id=None,
        error=error,
        status_code=401,
    )

    line = capsys.readouterr().err.strip()
    prefix = "[dots-kms-mcp.audit] "
    event = json.loads(line[len(prefix):])
    assert secret not in line
    assert event["error"] == expected
