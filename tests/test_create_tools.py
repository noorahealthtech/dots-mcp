from __future__ import annotations

import asyncio
import copy
import os
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dots_kms_mcp import server
from dots_kms_mcp.audit import CreateActor
from dots_kms_mcp.create_schema import (
    ContentCreateSchema,
    CreateSchemaRegistry,
    FieldRule,
)
from dots_kms_mcp.errors import KmsApiError


TAG_ID = "657663f98fe0ed6bdaf8db43"
ACTOR = CreateActor("google-sub-123", "writer@noorahealth.org")


class RecordingClient:
    def __init__(self) -> None:
        self.create_calls: list[tuple[str, dict]] = []
        self.created = {
            "content": {"_id": "created-123", "main": {"title": "A visit"}}
        }
        self.create_error: KmsApiError | None = None
        self.tags = {
            TAG_ID: {
                "_id": TAG_ID,
                "main": {"title": "Bangladesh"},
                "tagId": "bangladesh",
            }
        }

    async def get_data(self, configs):
        requested = configs["findQuery"]["_id"]["$in"]
        return {"data": [self.tags[item] for item in requested if item in self.tags]}

    async def create_and_publish(self, content_type, document):
        self.create_calls.append((content_type, copy.deepcopy(document)))
        if self.create_error is not None:
            raise self.create_error
        return copy.deepcopy(self.created)


@pytest.fixture
def complete_registry():
    return CreateSchemaRegistry(
        version="test",
        content_types={
            "routineVisits": ContentCreateSchema(
                content_type="routineVisits",
                commit_ready=True,
                missing_contract=(),
                fields={
                    "main.title": FieldRule(
                        path="main.title",
                        component="TitleInput",
                        required=True,
                        writable=True,
                    ),
                    "tags.country": FieldRule(
                        path="tags.country",
                        component="TagsInputSingle",
                        required=False,
                        writable=True,
                        cardinality="single",
                        collection_id="country",
                    ),
                },
                conditional_requirements=(),
            )
        },
    )


@pytest.fixture
def client():
    return RecordingClient()


@pytest.fixture(autouse=True)
def create_runtime(monkeypatch, complete_registry, client):
    monkeypatch.setattr(
        server,
        "_settings",
        replace(
            server._settings,
            create_enabled=True,
            create_content_types=("routineVisits",),
        ),
    )
    monkeypatch.setattr(server, "_create_registry", complete_registry, raising=False)
    monkeypatch.setattr(server, "_client", client)
    identity = SimpleNamespace(email=ACTOR.email)
    provider = SimpleNamespace(identity_for_subject=lambda subject: identity)
    monkeypatch.setattr(server, "_auth", SimpleNamespace(provider=provider))
    monkeypatch.setattr(
        server,
        "get_access_token",
        lambda: SimpleNamespace(subject=ACTOR.subject),
    )


async def test_preview_validates_without_creating(client):
    result = await server.preview_content_creation(
        "routineVisits", {"main": {"title": "A visit"}}
    )

    assert result == {
        "valid": True,
        "commit_ready": True,
        "document": {"main": {"title": "A visit"}},
        "errors": [],
        "warnings": [],
        "actor": {"email": "writer@noorahealth.org"},
        "publishes_immediately": True,
        "publication_warning": (
            "Creating this content publishes it immediately; preview does not create it."
        ),
    }
    assert client.create_calls == []


async def test_preview_rejects_disallowed_content_type(client):
    with pytest.raises(ValueError, match="not enabled for content creation"):
        await server.preview_content_creation("reports", {"main": {"title": "No"}})
    assert client.create_calls == []


async def test_preview_returns_exact_validation_issues():
    result = await server.preview_content_creation("routineVisits", {"main": {}})

    assert result["valid"] is False
    assert result["errors"] == [
        {
            "path": "main.title",
            "code": "required",
            "message": "Required field is missing or empty",
        }
    ]


async def test_preview_reports_incomplete_schema_warning(monkeypatch, complete_registry):
    schema = complete_registry.content_types["routineVisits"]
    incomplete = replace(
        schema, commit_ready=False, missing_contract=("required_fields",)
    )
    monkeypatch.setattr(
        server,
        "_create_registry",
        replace(complete_registry, content_types={"routineVisits": incomplete}),
    )

    result = await server.preview_content_creation(
        "routineVisits", {"main": {"title": "A visit"}}
    )

    assert result["valid"] is True
    assert result["commit_ready"] is False
    assert result["warnings"] == [
        {
            "path": "content_type",
            "code": "schema_incomplete",
            "message": "routineVisits is not commit-ready: required_fields",
        }
    ]


async def test_preview_reports_missing_tag(client):
    client.tags = {}
    result = await server.preview_content_creation(
        "routineVisits",
        {
            "main": {"title": "A visit"},
            "tags": {
                "country": {
                    "collectionId": "country",
                    "data": [
                        {
                            "_id": TAG_ID,
                            "display": "Bangladesh",
                            "tagId": "bangladesh",
                        }
                    ],
                }
            },
        },
    )

    assert result["valid"] is False
    assert result["errors"][0]["code"] == "tag_not_found"


async def test_preview_does_not_expose_service_credentials():
    result = await server.preview_content_creation(
        "routineVisits", {"main": {"title": "A visit"}}
    )
    serialized = repr(result)
    assert "google-sub-123" not in serialized
    for service_credential in (server._settings.token, server._settings.tenant):
        if service_credential:
            assert service_credential not in serialized


async def test_create_requires_explicit_confirmation_before_client(client):
    with pytest.raises(ValueError, match="confirm_publish=True"):
        await server.create_and_publish_content(
            "routineVisits", {"main": {"title": "A visit"}}
        )
    assert client.create_calls == []


async def test_create_rejects_incomplete_registry_before_client(
    monkeypatch, complete_registry, client
):
    schema = complete_registry.content_types["routineVisits"]
    incomplete = replace(
        schema, commit_ready=False, missing_contract=("required_fields",)
    )
    monkeypatch.setattr(
        server,
        "_create_registry",
        replace(complete_registry, content_types={"routineVisits": incomplete}),
    )

    with pytest.raises(ValueError, match="not commit-ready"):
        await server.create_and_publish_content(
            "routineVisits",
            {"main": {"title": "A visit"}},
            confirm_publish=True,
        )
    assert client.create_calls == []


async def test_create_rejects_invalid_candidate_before_client(client):
    with pytest.raises(ValueError, match="main.title"):
        await server.create_and_publish_content(
            "routineVisits", {"main": {}}, confirm_publish=True
        )
    assert client.create_calls == []


async def test_create_revalidates_after_independent_preview(monkeypatch, client):
    await server.preview_content_creation(
        "routineVisits", {"main": {"title": "A visit"}}
    )
    original = server.validate_create_document
    calls = 0

    async def recording_validation(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(server, "validate_create_document", recording_validation)
    await server.create_and_publish_content(
        "routineVisits",
        {"main": {"title": "A visit"}},
        confirm_publish=True,
    )

    assert calls == 1
    assert len(client.create_calls) == 1


async def test_successful_create_adds_citation_and_attribution_warning(client):
    result = await server.create_and_publish_content(
        "routineVisits",
        {"main": {"title": "A visit"}},
        confirm_publish=True,
    )

    assert result["content"]["source_url"].endswith(
        "/published-page/routineVisits?id=created-123"
    )
    assert result["actor"] == {"email": "writer@noorahealth.org"}
    assert "service account" in result["attribution_warning"]


async def test_success_emits_one_audit_event(monkeypatch):
    events = []
    monkeypatch.setattr(server, "emit_create_audit", lambda **event: events.append(event))

    await server.create_and_publish_content(
        "routineVisits",
        {"main": {"title": "A visit"}},
        confirm_publish=True,
    )

    assert len(events) == 1
    assert events[0]["actor"] == ACTOR
    assert events[0]["outcome"] == "success"
    assert events[0]["content_id"] == "created-123"


async def test_client_failure_emits_one_failure_audit_event(
    monkeypatch, client
):
    events = []
    client.create_error = KmsApiError("publish failed", status_code=503)
    monkeypatch.setattr(server, "emit_create_audit", lambda **event: events.append(event))

    with pytest.raises(ValueError, match="publish failed"):
        await server.create_and_publish_content(
            "routineVisits",
            {"main": {"title": "A visit"}},
            confirm_publish=True,
        )

    assert len(events) == 1
    assert events[0]["outcome"] == "failure"
    assert events[0]["status_code"] == 503


async def test_validation_failure_after_actor_resolution_is_audited(
    monkeypatch, client
):
    events = []
    monkeypatch.setattr(server, "emit_create_audit", lambda **event: events.append(event))

    with pytest.raises(ValueError, match="main.title"):
        await server.create_and_publish_content(
            "routineVisits", {"main": {}}, confirm_publish=True
        )

    assert client.create_calls == []
    assert len(events) == 1
    assert events[0]["actor"] == ACTOR
    assert events[0]["outcome"] == "failure"


async def test_create_rejects_caller_supplied_meta(monkeypatch, client):
    events = []
    monkeypatch.setattr(server, "emit_create_audit", lambda **event: events.append(event))

    with pytest.raises(ValueError, match="meta"):
        await server.create_and_publish_content(
            "routineVisits",
            {
                "main": {"title": "A visit"},
                "meta": {"kp_contributed_by": "someone-else"},
            },
            confirm_publish=True,
        )

    assert client.create_calls == []
    assert events[0]["outcome"] == "failure"


def test_actor_is_resolved_from_mcp_auth_context(monkeypatch):
    identity = SimpleNamespace(email="writer@noorahealth.org")
    provider = SimpleNamespace(identity_for_subject=lambda subject: identity)
    monkeypatch.setattr(server, "_auth", SimpleNamespace(provider=provider))
    monkeypatch.setattr(
        server, "get_access_token", lambda: SimpleNamespace(subject="google-sub-123")
    )

    assert server._require_create_actor() == ACTOR


@pytest.mark.parametrize(
    ("auth", "token", "message"),
    [
        (None, SimpleNamespace(subject="google-sub-123"), "OAuth is not configured"),
        (SimpleNamespace(provider=object()), None, "authenticated MCP access token"),
        (
            SimpleNamespace(provider=object()),
            SimpleNamespace(subject=None),
            "authenticated Google subject",
        ),
    ],
)
def test_actor_resolution_fails_closed(monkeypatch, auth, token, message):
    monkeypatch.setattr(server, "_auth", auth)
    monkeypatch.setattr(server, "get_access_token", lambda: token)

    with pytest.raises(ValueError, match=message):
        server._require_create_actor()


def test_actor_resolution_requires_provider_identity(monkeypatch):
    provider = SimpleNamespace(identity_for_subject=lambda subject: None)
    monkeypatch.setattr(server, "_auth", SimpleNamespace(provider=provider))
    monkeypatch.setattr(
        server, "get_access_token", lambda: SimpleNamespace(subject="google-sub-123")
    )

    with pytest.raises(ValueError, match="Google identity"):
        server._require_create_actor()


def _registration_subprocess_env(**overrides):
    env = os.environ.copy()
    for name in (
        "KMS_CREATE_ENABLED",
        "KMS_CREATE_CONTENT_TYPES",
        "KMS_TRANSPORT",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
    ):
        env.pop(name, None)
    env.update({"KMS_MOCK": "1", **overrides})
    return env


def _import_server(env):
    code = (
        "import asyncio; from dots_kms_mcp import server; "
        "print(','.join(sorted(t.name for t in asyncio.run(server.mcp.list_tools()))))"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_create_tools_register_only_in_enabled_safe_runtime():
    result = _import_server(
        _registration_subprocess_env(
            KMS_CREATE_ENABLED="1",
            KMS_CREATE_CONTENT_TYPES="routineVisits",
            KMS_TRANSPORT="streamable-http",
            GOOGLE_CLIENT_ID="test-client",
            GOOGLE_CLIENT_SECRET="test-secret",
        )
    )

    assert result.returncode == 0, result.stderr
    names = set(result.stdout.strip().split(","))
    assert {"preview_content_creation", "create_and_publish_content"} <= names


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "KMS_CREATE_ENABLED": "1",
                "KMS_CREATE_CONTENT_TYPES": "routineVisits",
                "KMS_TRANSPORT": "stdio",
                "GOOGLE_CLIENT_ID": "test-client",
                "GOOGLE_CLIENT_SECRET": "test-secret",
            },
            "Content creation requires KMS_TRANSPORT=streamable-http",
        ),
        (
            {
                "KMS_CREATE_ENABLED": "1",
                "KMS_CREATE_CONTENT_TYPES": "routineVisits",
                "KMS_TRANSPORT": "streamable-http",
            },
            "Content creation requires configured OAuth",
        ),
    ],
)
def test_create_enabled_unsafe_runtime_fails_startup(overrides, message):
    result = _import_server(_registration_subprocess_env(**overrides))

    assert result.returncode != 0
    assert message in result.stderr
