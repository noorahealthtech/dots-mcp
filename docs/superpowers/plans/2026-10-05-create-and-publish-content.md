# Create And Publish Content Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add create-only MCP tools that preview, validate, audit, and immediately publish DOTS content without exposing update, delete, media-upload, tag-creation, or metadata-override behavior.

**Architecture:** A packaged creation registry and pure validation module define the content contract. The existing client gains one plain-JSON create method, while the server conditionally registers preview and create tools only under explicitly enabled authenticated HTTP. Google identity remains MCP-side provenance; DOTS contributor attribution remains controlled by the shared API token.

**Tech Stack:** Python 3.10+, FastMCP 1.x, httpx, dataclasses, pytest, pytest-asyncio, respx.

**Spec:** `docs/superpowers/specs/2026-10-05-create-and-publish-content-design.md`

## Global Constraints

- Create only through `POST /api/content/createAndPublishContent/:contentType`; do not implement `quickUpdateContent`, delete, or unpublish.
- Do not send or accept caller-provided DOTS system metadata, including `meta.kp_contributed_by`.
- Treat `main.author` as an ordinary template field, not the linked DOTS contributor byline.
- Creation is disabled by default and may be registered only for authenticated Streamable HTTP with Google OAuth.
- Every authenticated user accepted by the existing Google-domain policy may create; do not add a second writer allowlist.
- `KMS_CREATE_CONTENT_TYPES` and registry `commit_ready` jointly control commit eligibility.
- Preview never calls the create endpoint; commit always repeats validation.
- Use existing tags only; verify tag `_id`, `display`, and `tagId` through getData.
- Accept existing successful upload `fileData` objects unchanged; do not implement media upload.
- Fail closed when required fields, legal choices, conditional rules, or schema freshness are not authoritative.
- Never automatically retry create after a timeout or connection error.
- Audit Google subject/email and returned DOTS ID to stderr; never log tokens or full document bodies.
- Preserve all current read behavior, getData double-stringification, published-only filtering, citations, stdio defaults, and mock auto-selection.
- Do not add third-party runtime dependencies.
- Do not add co-author trailers to commits.

---

## File Structure

- Create `src/dots_kms_mcp/create_schema.py`: creation-registry models and loader.
- Create `src/dots_kms_mcp/create_validation.py`: pure field validation plus async existing-tag verification.
- Create `src/dots_kms_mcp/data/kms_create_schema.default.json`: packaged path/component registry, fail-closed coverage metadata.
- Create `src/dots_kms_mcp/audit.py`: structured create audit events written to stderr.
- Modify `src/dots_kms_mcp/getdata_client.py`: one create method on real, mock, and decorator protocols.
- Modify `src/dots_kms_mcp/mock_client.py`: stateful create records readable through getData.
- Modify `src/dots_kms_mcp/auth.py`: retain verified Google identities by stable subject.
- Modify `src/dots_kms_mcp/settings.py`: create endpoint, enablement, registry path, and content-type allowlist.
- Modify `src/dots_kms_mcp/server.py`: preview/create orchestration and conditional registration.
- Modify `.env.example` and `README.md`: safe activation, attribution boundary, validation, recovery, and examples.
- Create `tests/test_create_schema.py`, `tests/test_create_validation.py`, `tests/test_create_client.py`, `tests/test_create_audit.py`, and `tests/test_create_tools.py`.
- Modify `tests/test_auth.py`, `tests/test_settings.py`, `tests/test_client_mock.py`, and `tests/test_server_tools.py`.

---

### Task 1: Creation Registry And Fail-Closed Coverage

**Files:**
- Create: `src/dots_kms_mcp/create_schema.py`
- Create: `src/dots_kms_mcp/data/kms_create_schema.default.json`
- Create: `tests/test_create_schema.py`

**Interfaces:**
- Produces: `FieldRule`, `ConditionalRequirement`, `ContentCreateSchema`, `CreateSchemaRegistry`, `load_create_schema(path: str | None) -> CreateSchemaRegistry`.
- Consumes: the exact 159 path/component entries in the spec's "Documented Fields" section.

- [ ] **Step 1: Write failing loader and contract tests**

Create `tests/test_create_schema.py` with these assertions:

```python
from dots_kms_mcp.create_schema import load_create_schema


def test_packaged_create_schema_contains_documented_contract():
    registry = load_create_schema(None)
    assert set(registry.content_types) == {
        "learningAndSharingSessions",
        "organisationalReports",
        "programmaticAssetsTemplates",
        "programPerformanceReports",
        "reports",
        "researchAndEvaluationReports",
        "routineVisits",
        "successStory",
        "toolsAndCollaterals",
        "trainingReports",
    }
    assert sum(len(schema.fields) for schema in registry.content_types.values()) == 159
    routine = registry.content_types["routineVisits"]
    assert routine.fields["main.date"].component == "DatePicker"
    assert routine.fields["tags.country"].collection_id == "country"
    assert routine.fields["tags.country"].cardinality == "single"


def test_non_input_components_are_explicitly_not_writable():
    registry = load_create_schema(None)
    static = registry.content_types["researchAndEvaluationReports"].fields[
        "main.enterTheTitleOfTheFinalDocumentManuscript"
    ]
    repeater = registry.content_types["successStory"].fields["main.testimonials"]
    assert static.writable is False
    assert repeater.writable is False


def test_packaged_registry_fails_closed_until_contract_is_complete():
    registry = load_create_schema(None)
    routine = registry.content_types["routineVisits"]
    assert routine.commit_ready is False
    assert "choice_options:main.visitType" in routine.missing_contract


def test_explicit_missing_schema_path_is_loud(tmp_path):
    missing = tmp_path / "missing.json"
    try:
        load_create_schema(str(missing))
    except FileNotFoundError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing explicit creation schema must fail")
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `uv run pytest tests/test_create_schema.py -v`

Expected: collection fails with `ModuleNotFoundError: dots_kms_mcp.create_schema`.

- [ ] **Step 3: Implement immutable registry models and loader**

Create `create_schema.py` with frozen dataclasses, packaged-resource fallback, explicit-path failure, and no working-directory implicit override:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

Cardinality = Literal["single", "multi"]


@dataclass(frozen=True)
class FieldRule:
    path: str
    component: str
    required: bool
    writable: bool
    cardinality: Cardinality | None = None
    collection_id: str | None = None
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ConditionalRequirement:
    path: str
    equals: Any
    require: tuple[str, ...]


@dataclass(frozen=True)
class ContentCreateSchema:
    content_type: str
    commit_ready: bool
    missing_contract: tuple[str, ...]
    fields: dict[str, FieldRule]
    conditional_requirements: tuple[ConditionalRequirement, ...]


@dataclass(frozen=True)
class CreateSchemaRegistry:
    version: str
    content_types: dict[str, ContentCreateSchema]


def load_create_schema(path: str | None) -> CreateSchemaRegistry:
    if path:
        schema_path = Path(path).expanduser()
        if not schema_path.is_file():
            raise FileNotFoundError(f"KMS creation schema not found at {schema_path}")
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
    else:
        resource = resources.files("dots_kms_mcp.data").joinpath(
            "kms_create_schema.default.json"
        )
        raw = json.loads(resource.read_text(encoding="utf-8"))
    return _parse_registry(raw)
```

Implement `_parse_registry()` to reject malformed registry JSON, convert option objects to `(value, display)` tuples, and require `main.title` to be `TitleInput` and required for every content type.

- [ ] **Step 4: Add the complete packaged path/component registry**

Create `kms_create_schema.default.json` with all 159 entries from the spec. Use these exact defaults:

```json
{
  "version": "2026-10-05",
  "content_types": {
    "routineVisits": {
      "commit_ready": false,
      "missing_contract": [
        "choice_options:main.visitType",
        "required_fields",
        "conditional_rules"
      ],
      "conditional_requirements": [],
      "fields": {
        "main.title": {"component": "TitleInput", "required": true, "writable": true},
        "main.date": {"component": "DatePicker", "required": true, "writable": true},
        "main.visitType": {"component": "CheckboxList", "required": true, "writable": true, "options": []},
        "main.author": {"component": "TextInput", "required": true, "writable": true},
        "tags.country": {"component": "TagsInputSingle", "required": true, "writable": true, "cardinality": "single", "collection_id": "country"},
        "tags.states": {"component": "TagsInputSingle", "required": true, "writable": true, "cardinality": "single", "collection_id": "states"},
        "tags.districts": {"component": "TagsInputSingle", "required": true, "writable": true, "cardinality": "single", "collection_id": "districts"},
        "tags.stakeholder": {"component": "TagsInputMulti", "required": true, "writable": true, "cardinality": "multi", "collection_id": "stakeholder"}
      }
    }
  }
}
```

The snippet shows the required shape, not the whole file; add every path in the spec, set every title required, mark known Routine Visit requirements as shown, derive tag collection/cardinality from component and path, and mark all `StaticRichText`/`Repeater` fields non-writable. Keep every type `commit_ready: false` until its complete authoritative contract is supplied.

- [ ] **Step 5: Run registry tests**

Run: `uv run pytest tests/test_create_schema.py -v`

Expected: all tests pass, including exact type and field counts.

- [ ] **Step 6: Commit the registry unit**

```bash
git add src/dots_kms_mcp/create_schema.py src/dots_kms_mcp/data/kms_create_schema.default.json tests/test_create_schema.py
git commit -m "Add fail-closed content creation registry"
```

---

### Task 2: Component Validation And Existing-Tag Verification

**Files:**
- Create: `src/dots_kms_mcp/create_validation.py`
- Create: `tests/test_create_validation.py`

**Interfaces:**
- Consumes: `CreateSchemaRegistry`, `ContentCreateSchema`, and a client implementing `get_data(configs)`.
- Produces: `ValidationIssue`, `ValidationResult`, `validate_create_document(content_type, document, registry, client, require_commit_ready=False) -> ValidationResult`.

- [ ] **Step 1: Write failing shape, path, readiness, and normalization tests**

Create a test helper that loads a minimal complete registry fixture in memory, then add these named tests:

```python
async def test_unknown_path_is_rejected(complete_registry, mock_client):
    result = await validate_create_document(
        "routineVisits",
        {"main": {"title": "Visit", "notAField": "bad"}},
        complete_registry,
        mock_client,
    )
    assert [(e.path, e.code) for e in result.errors] == [
        ("main.notAField", "unknown_path")
    ]


async def test_commit_rejects_incomplete_registry(registry, mock_client):
    result = await validate_create_document(
        "routineVisits",
        {"main": {"title": "Visit"}},
        registry,
        mock_client,
        require_commit_ready=True,
    )
    assert result.valid is False
    assert any(e.code == "schema_incomplete" for e in result.errors)


async def test_preview_reports_incomplete_registry_as_warning(registry, mock_client):
    result = await validate_create_document(
        "routineVisits",
        {"main": {"title": "Visit"}},
        registry,
        mock_client,
        require_commit_ready=False,
    )
    assert any(w.code == "schema_incomplete" for w in result.warnings)
    assert not any(e.code == "schema_incomplete" for e in result.errors)


async def test_valid_tag_is_verified_without_mutating_payload(
    complete_registry, mock_client
):
    country = {
        "collectionId": "country",
        "data": [{"_id": "657663f98fe0ed6bdaf8db43", "display": "Bangladesh", "tagId": "bangladesh"}],
    }
    document = {"main": {"title": "Visit"}, "tags": {"country": country}}
    result = await validate_create_document(
        "routineVisits", document, complete_registry, mock_client
    )
    assert result.errors == ()
    assert result.document["tags"]["country"] == country
```

Also add exact tests for:

- missing/empty title;
- non-string text/title;
- URL without `http`/`https`;
- booleans and numeric strings rejected by `NumberInput`;
- timezone-free or malformed dates;
- date range length and reversed bounds;
- radio and checkbox object shape;
- unconfigured and unknown choice values;
- single-tag count not equal to one;
- tag collection mismatch, malformed tag triplet, missing referenced `_id`, and mismatched stored display/tagId;
- rich text missing keys, non-Lexical value, malformed root, and disagreement between `allText` and editor text;
- media not an array or missing successful-upload core fields;
- LinkEmbed object and URL validation;
- supplied StaticRichText/Repeater;
- `required` and `conditional_requirements` failures;
- document input remains byte-for-byte equivalent after validation except deliberate safe normalization of ISO `Z` parsing output, which must preserve the original string.

- [ ] **Step 2: Run the validation tests and verify failure**

Run: `uv run pytest tests/test_create_validation.py -v`

Expected: collection fails because `create_validation` does not exist.

- [ ] **Step 3: Implement issue/result types and top-level path extraction**

```python
@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    commit_ready: bool
    document: dict[str, Any]
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]


def _field_values(document: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for prefix in ("main", "tags"):
        section = document.get(prefix)
        if section is None:
            continue
        if not isinstance(section, dict):
            values[prefix] = section
            continue
        for key, value in section.items():
            values[f"{prefix}.{key}"] = value
    for key, value in document.items():
        if key not in {"main", "tags"}:
            values[key] = value
    return values
```

Deep-copy the candidate before processing. Never drop unknown keys from rich text, tag items, LinkEmbed metadata, or media objects after they pass checks.

- [ ] **Step 4: Implement deterministic component validators**

Use one dispatcher keyed by the documented component names. Parse dates with `datetime.fromisoformat(value.replace("Z", "+00:00"))`, require timezone information, and compare parsed date-range bounds. Reject `bool` for numbers. Validate URLs with `urllib.parse.urlparse` and schemes `http`/`https`.

For Lexical text, recursively collect text-node `text` values per root child, join block text with spaces, normalize whitespace with `" ".join(text.split())`, and compare that result to normalized `allText`.

For media entries require these keys without removing any additional upload metadata:

```python
MEDIA_REQUIRED_KEYS = {
    "name",
    "bucket",
    "contentType",
    "size",
    "publicUrl",
    "originalFilename",
    "mediaHost",
}
```

- [ ] **Step 5: Implement async tag verification through getData**

Group referenced tags by collection, issue one getData call per collection with `_id: {$in: ids}`, and project `_id`, `main.title`, and `tagId`. Return errors when an ID is missing or when returned title/tagId disagrees with the request. Use the exact collection ID as `contentTypes`.

- [ ] **Step 6: Run focused and full validation tests**

Run: `uv run pytest tests/test_create_validation.py -v`

Expected: every component and tag-verification case passes.

- [ ] **Step 7: Commit the validation unit**

```bash
git add src/dots_kms_mcp/create_validation.py tests/test_create_validation.py
git commit -m "Validate content creation payloads"
```

---

### Task 3: Plain-JSON Create Client And Stateful Mock

**Files:**
- Modify: `src/dots_kms_mcp/settings.py`
- Modify: `src/dots_kms_mcp/getdata_client.py`
- Modify: `src/dots_kms_mcp/mock_client.py`
- Create: `tests/test_create_client.py`
- Modify: `tests/test_client_mock.py`

**Interfaces:**
- Produces: `Settings.create_url(content_type: str) -> str` and `KmsClientProtocol.create_and_publish(content_type, document) -> dict[str, Any]`.
- Preserves: `get_data()` request encoding and `PublishedOnlyClient` read filtering.

- [ ] **Step 1: Write failing create wire-contract tests**

```python
import httpx
import pytest
import respx

from dots_kms_mcp.errors import KmsApiError
from dots_kms_mcp.getdata_client import KmsClient


@respx.mock
async def test_create_posts_plain_json_to_content_type_path(live_settings):
    document = {"main": {"title": "A routine visit"}}
    route = respx.post(
        "https://api.example.test/api/content/createAndPublishContent/routineVisits"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"content": {"_id": "6ac288dd870810e6b6d21048", **document}},
        )
    )
    result = await KmsClient(live_settings).create_and_publish(
        "routineVisits", document
    )
    request = route.calls[0].request
    assert request.headers["x-auth-token"] == "TEST_TOKEN"
    assert request.headers["tenant"] == "TEST_TENANT"
    assert request.content == b'{"main":{"title":"A routine visit"}}'
    assert result["content"]["_id"] == "6ac288dd870810e6b6d21048"


@respx.mock
async def test_create_timeout_is_not_retried(live_settings):
    route = respx.post(
        "https://api.example.test/api/content/createAndPublishContent/routineVisits"
    ).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(KmsApiError, match="outcome may be unknown"):
        await KmsClient(live_settings).create_and_publish(
            "routineVisits", {"main": {"title": "Visit"}}
        )
    assert route.call_count == 1
```

Add tests for structured `errors`, JSON `error`, HTML 500, 401 auth class, 404, 429 with `RateLimit-Reset`, malformed success JSON, and success missing `content`.

- [ ] **Step 2: Run create client tests and verify failure**

Run: `uv run pytest tests/test_create_client.py -v`

Expected: failure because `create_and_publish` and `create_url` do not exist.

- [ ] **Step 3: Add endpoint construction and create protocol method**

Add `CREATE_AND_PUBLISH_PATH = "/api/content/createAndPublishContent"` and:

```python
def create_url(self, content_type: str) -> str:
    return (
        self.base_url.rstrip("/")
        + CREATE_AND_PUBLISH_PATH
        + "/"
        + content_type
    )
```

Add `create_and_publish` to `KmsClientProtocol`, `KmsClient`, and `PublishedOnlyClient`. The decorator delegates creation unchanged; it must never inject `kp_published_status` into the body.

Give every new `Settings` dataclass field a default so the existing direct constructors in test fixtures remain source-compatible.

- [ ] **Step 4: Refactor response parsing without changing getData behavior**

Change `_parse_response` to accept `operation: str` and parse these shapes:

- `{"errors": [{"name", "msg"}]}`;
- `{"error": "message"}`;
- non-JSON/HTML body;
- status and `RateLimit-Reset`.

Keep every current error-parsing test passing. The create method validates that successful JSON contains a dictionary `content`.

- [ ] **Step 5: Write failing stateful mock create tests**

```python
async def test_mock_created_document_can_be_read_back():
    client = MockKmsClient()
    created = await client.create_and_publish(
        "routineVisits", {"main": {"title": "Mock visit"}}
    )
    doc_id = created["content"]["_id"]
    fetched = await client.get_data(
        {"contentTypes": ["routineVisits"], "findQuery": {"_id": doc_id}}
    )
    assert fetched["data"][0]["main"]["title"] == "Mock visit"
    assert fetched["data"][0]["kp_published_status"] == "published"
    assert fetched["data"][0]["meta"]["kp_content_type"] == "routineVisits"
```

- [ ] **Step 6: Implement deterministic mock creation**

Add an instance `_created: dict[tuple[str, str], dict[str, Any]]`. Generate IDs from content type plus canonical JSON and a per-instance sequence so two identical creates still receive distinct IDs. Set server-managed publication dates, status, content type, and a mock contributor. Make fetch-by-ID consult `_created` before generating a synthetic result and prepend created records to normal listings.

- [ ] **Step 7: Run client and regression tests**

Run: `uv run pytest tests/test_create_client.py tests/test_client_mock.py tests/test_double_stringify.py tests/test_error_parsing.py tests/test_published_filter.py -v`

Expected: all pass; the existing double-stringify test proves create refactoring did not change reads.

- [ ] **Step 8: Commit the client unit**

```bash
git add src/dots_kms_mcp/settings.py src/dots_kms_mcp/getdata_client.py src/dots_kms_mcp/mock_client.py tests/test_create_client.py tests/test_client_mock.py
git commit -m "Add create and publish client operation"
```

---

### Task 4: Google Identity Retention And Safe Audit Events

**Files:**
- Modify: `src/dots_kms_mcp/auth.py`
- Create: `src/dots_kms_mcp/audit.py`
- Modify: `tests/test_auth.py`
- Create: `tests/test_create_audit.py`

**Interfaces:**
- Produces: `GoogleBridgeProvider.identity_for_subject(subject: str) -> GoogleIdentity | None`, `CreateActor`, and `emit_create_audit(*, actor: CreateActor, content_type: str, title: str, document: dict[str, Any], outcome: Literal["success", "failure"], content_id: str | None, error: str | None, status_code: int | None) -> None`.
- Consumes: `AccessToken.subject` via `mcp.server.auth.middleware.auth_context.get_access_token` in Task 6.

- [ ] **Step 1: Write failing identity-retention test**

Extend the existing `test_callback_allowed_identity_mints_code` flow, which uses `_provider()`, `_registered_client()`, and `_start_login()`, then assert:

```python
identity = p.identity_for_subject("g-sub")
assert identity is not None
assert identity.email == "alice@noorahealth.org"
assert identity.email_verified is True
```

Also assert denied-domain identities are never stored.

- [ ] **Step 2: Implement identity retention**

Add `_identities_by_subject: dict[str, GoogleIdentity]` to `GoogleBridgeProvider`. Store the allowed identity immediately before issuing the MCP authorization code. Add a read-only lookup method. Do not place email into the opaque token and do not persist Google upstream tokens.

- [ ] **Step 3: Write failing audit event tests**

```python
import json

from dots_kms_mcp.audit import CreateActor, emit_create_audit


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
    line = capsys.readouterr().err.strip()
    prefix = "[dots-kms-mcp.audit] "
    assert line.startswith(prefix)
    event = json.loads(line[len(prefix):])
    assert event["google_subject"] == "google-sub-123"
    assert event["google_email"] == "writer@noorahealth.org"
    assert event["content_id"] == "6ac288dd870810e6b6d21048"
    assert len(event["request_sha256"]) == 64
    assert "secretNotes" not in line
```

Add a failure test asserting status/error are present, content ID is null, and token-like strings passed in the document never appear.

- [ ] **Step 4: Implement canonical hashing and stderr JSON audit**

Create a frozen `CreateActor(subject, email)` dataclass. Hash `json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` with SHA-256. Emit one JSON object with UTC `Z` timestamp, event name, actor, content type, title, outcome, hash, content ID, status, and a maximum 300-character error summary.

- [ ] **Step 5: Run identity and audit tests**

Run: `uv run pytest tests/test_auth.py tests/test_create_audit.py -v`

Expected: all pass with no OAuth or KMS token in captured output.

- [ ] **Step 6: Commit the provenance unit**

```bash
git add src/dots_kms_mcp/auth.py src/dots_kms_mcp/audit.py tests/test_auth.py tests/test_create_audit.py
git commit -m "Track create actor provenance"
```

---

### Task 5: Create Settings And Safe Registration Gate

**Files:**
- Modify: `src/dots_kms_mcp/settings.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- Produces settings fields `create_enabled: bool`, `create_content_types: tuple[str, ...]`, and `create_schema_path: str | None`.
- Produces `Settings.validate_create_runtime(oauth_configured: bool) -> None`.

- [ ] **Step 1: Write failing environment-resolution tests**

Add `KMS_CREATE_ENABLED`, `KMS_CREATE_CONTENT_TYPES`, and `KMS_CREATE_SCHEMA_PATH` to the test environment cleanup. Add these tests:

```python
def test_creation_defaults_off(monkeypatch):
    _clear_env(monkeypatch)
    settings = Settings.from_env(load_env_file=False)
    assert settings.create_enabled is False
    assert settings.create_content_types == ()
    assert settings.create_schema_path is None


def test_creation_settings_parse_allowlist(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("KMS_CREATE_ENABLED", "1")
    monkeypatch.setenv("KMS_CREATE_CONTENT_TYPES", "routineVisits, reports")
    monkeypatch.setenv("KMS_CREATE_SCHEMA_PATH", "/run/secrets/create-schema.json")
    settings = Settings.from_env(load_env_file=False)
    assert settings.create_enabled is True
    assert settings.create_content_types == ("routineVisits", "reports")
    assert settings.create_schema_path == "/run/secrets/create-schema.json"
```

Add parameterized runtime validation tests proving enabled stdio, enabled HTTP without OAuth, and an empty content-type allowlist raise `ValueError`; enabled HTTP with OAuth and non-empty allowlist passes.

- [ ] **Step 2: Run settings tests and verify failure**

Run: `uv run pytest tests/test_settings.py -v`

Expected: new attribute assertions fail.

- [ ] **Step 3: Implement frozen settings fields and runtime validation**

Parse content types by comma, trim whitespace, preserve order, and remove duplicates. `validate_create_runtime()` returns immediately when disabled; otherwise require `transport == "streamable-http"`, OAuth configured, and at least one content type.

- [ ] **Step 4: Run settings tests**

Run: `uv run pytest tests/test_settings.py -v`

Expected: all settings tests pass without altering old defaults.

- [ ] **Step 5: Commit the settings unit**

```bash
git add src/dots_kms_mcp/settings.py tests/test_settings.py
git commit -m "Gate content creation behind safe settings"
```

---

### Task 6: Preview And Create MCP Tools

**Files:**
- Modify: `src/dots_kms_mcp/server.py`
- Modify: `tests/test_server_tools.py`
- Create: `tests/test_create_tools.py`

**Interfaces:**
- Consumes: registry loader, validator, client create method, `get_access_token()`, provider identity lookup, audit emitter, and citation helper.
- Produces: `preview_content_creation(content_type, document)` and `create_and_publish_content(content_type, document, confirm_publish=False)`.

- [ ] **Step 1: Write failing preview behavior tests**

In `tests/test_create_tools.py`, monkeypatch server helpers/client with a complete fixture registry and actor. Assert preview returns normalized data and never calls `create_and_publish`:

```python
async def test_preview_validates_without_creating(monkeypatch):
    called = False

    async def forbidden_create(content_type, document):
        nonlocal called
        called = True
        raise AssertionError("preview must not create")

    monkeypatch.setattr(server._client, "create_and_publish", forbidden_create)
    monkeypatch.setattr(
        server,
        "_require_create_actor",
        lambda: CreateActor("google-sub-123", "writer@noorahealth.org"),
    )
    result = await server.preview_content_creation(
        "routineVisits", {"main": {"title": "A visit"}}
    )
    assert result["valid"] is True
    assert result["publishes_immediately"] is True
    assert result["actor"]["email"] == "writer@noorahealth.org"
    assert called is False
```

Add tests for disallowed type, validation errors, incomplete schema warning, tag verification error, and absence of service-token details.

- [ ] **Step 2: Write failing commit behavior tests**

Add exact tests proving:

- `confirm_publish=False` fails before client invocation;
- incomplete registry fails before client invocation;
- invalid candidate fails before client invocation;
- commit calls validation even after a successful independent preview;
- successful create annotates `content.source_url`;
- result identifies initiating Google email and warns that DOTS contributor is the service account;
- success and client failure each emit one audit event;
- validation failure after actor resolution emits one failure audit event;
- caller-supplied top-level `meta` is rejected as an unknown path.

- [ ] **Step 3: Implement actor resolution**

Import `get_access_token` from MCP auth middleware. `_require_create_actor()` must require `_auth`, an access token subject, and a provider identity lookup. Raise a model-readable `ValueError` if any is absent. Never accept actor identity as a tool argument.

- [ ] **Step 4: Implement undecorated preview and create functions**

Both functions first check `content_type in _settings.create_content_types`. Preview calls validation with `require_commit_ready=False`. Commit requires confirmation and calls validation with `require_commit_ready=True`, then invokes `_client.create_and_publish`.

Load one module-level `_create_registry = load_create_schema(_settings.create_schema_path)` only when creation is enabled; disabled read-only startup must not load or validate a creation registry.

Convert issues to dictionaries with exact `path`, `code`, and `message`. Catch `KmsError` through `_raise_readable` after emitting the failure audit event. Annotate the returned content using `_annotate(content, content_type)`.

- [ ] **Step 5: Register tools only after startup policy validation**

After `_auth` and `mcp` construction:

```python
from mcp.types import ToolAnnotations


if _settings.create_enabled:
    _settings.validate_create_runtime(oauth_configured=_auth is not None)
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        )
    )(preview_content_creation)
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        )
    )(create_and_publish_content)
```

Because the functions are defined later in the module, place this registration immediately after both function definitions, not at the shown initialization location.

Use tool docstrings that state immediate publication, preview-first workflow, no metadata override, existing tags only, no media upload, actor attribution boundary, and no automatic retry.

- [ ] **Step 6: Test default and enabled tool registration**

Update `tests/test_server_tools.py` to assert the two tools are absent under the default disabled test environment. In `tests/test_create_tools.py`, run an isolated subprocess with these environment values:

```text
KMS_CREATE_ENABLED=1
KMS_CREATE_CONTENT_TYPES=routineVisits
KMS_TRANSPORT=streamable-http
GOOGLE_CLIENT_ID=test-client
GOOGLE_CLIENT_SECRET=test-secret
KMS_MOCK=1
```

The subprocess imports `dots_kms_mcp.server`, lists tools, and asserts both names are present. Add negative subprocess cases for enabled stdio and enabled HTTP without OAuth credentials.

- [ ] **Step 7: Run tool tests**

Run: `uv run pytest tests/test_create_tools.py tests/test_server_tools.py -v`

Expected: all preview, commit, actor, audit, and registration tests pass.

- [ ] **Step 8: Commit the MCP tool unit**

```bash
git add src/dots_kms_mcp/server.py tests/test_create_tools.py tests/test_server_tools.py
git commit -m "Expose previewed content creation tools"
```

---

### Task 7: Deployment Documentation And End-To-End Verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Documents every setting and operational boundary introduced by Tasks 1-6.
- Does not change runtime interfaces.

- [ ] **Step 1: Add disabled-by-default environment examples**

Add these commented values to `.env.example`:

```dotenv
# Content creation is remote-OAuth-only and publishes immediately.
KMS_CREATE_ENABLED=0
# Required when creation is enabled; expose only registry-ready content types.
KMS_CREATE_CONTENT_TYPES=routineVisits
# Optional authoritative registry override; packaged registry fails closed by default.
# KMS_CREATE_SCHEMA_PATH=/run/secrets/kms_create_schema.json
```

- [ ] **Step 2: Document setup and attribution without overstating capability**

Add a README "Create and publish content" section covering:

- preview then commit examples with exact tool names;
- immediate publication and 60/minute shared rate limit;
- authenticated HTTP-only activation;
- content allowlist and `commit_ready` requirement;
- validation registry ownership and missing DOTS metadata;
- existing tags and pre-uploaded files only;
- no update/delete/media upload/tag creation;
- no automatic create retry;
- `main.author` versus server-managed `meta.kp_contributed_by`;
- Google subject/email audit in stderr and Docker logs;
- web-app/DOTS-support correction path.

- [ ] **Step 3: Run formatting-sensitive and focused tests**

Run: `uv run pytest tests/test_create_schema.py tests/test_create_validation.py tests/test_create_client.py tests/test_create_audit.py tests/test_create_tools.py -v`

Expected: all create feature tests pass.

- [ ] **Step 4: Run the full regression suite**

Run: `uv run pytest`

Expected: zero failures.

- [ ] **Step 5: Smoke-test disabled tool inventory**

Run: `uv run python scripts/try_tool.py`

Expected: existing read tools are listed; create tools are absent under default settings.

- [ ] **Step 6: Smoke-test enabled mock tool inventory**

Run:

```bash
KMS_CREATE_ENABLED=1 \
KMS_CREATE_CONTENT_TYPES=routineVisits \
KMS_TRANSPORT=streamable-http \
GOOGLE_CLIENT_ID=test-client \
GOOGLE_CLIENT_SECRET=test-secret \
KMS_MOCK=1 \
uv run python scripts/try_tool.py
```

Expected: `preview_content_creation` and `create_and_publish_content` are listed. Do not call create against the live DOTS endpoint during verification.

- [ ] **Step 7: Inspect the complete change**

Run: `git status --short && git diff --check && git diff --stat`

Expected: only planned source, test, schema, and documentation files are changed; `git diff --check` reports no whitespace errors.

- [ ] **Step 8: Commit documentation and verification updates**

```bash
git add .env.example README.md
git commit -m "Document safe content creation setup"
```

---

## Activation Prerequisite

Implementation can merge with creation disabled and the packaged registry marked incomplete. Before enabling any production content type, obtain or explicitly approve all missing required flags, legal choice values, and conditional rules; place them in an external versioned creation registry; set that type's `commit_ready` to true; and point `KMS_CREATE_SCHEMA_PATH` at the reviewed file. This is the deliberate fail-closed behavior from the approved design, not deferred implementation work.

## Execution Branch

At execution time, invoke `using-git-worktrees` before editing application code and create the isolated branch `feature/create-and-publish-content`. Keep the plan/spec commits and every implementation commit on that branch.
