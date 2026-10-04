"""Tests for the create-and-publish HTTP client contract."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from dots_kms_mcp.errors import KmsApiError, KmsAuthError
from dots_kms_mcp.getdata_client import KmsClient, PublishedOnlyClient


CREATE_URL = (
    "https://api.example.test/api/content/createAndPublishContent/routineVisits"
)


@respx.mock
async def test_create_posts_plain_json_to_content_type_path(live_settings):
    document = {"main": {"title": "A routine visit"}}
    route = respx.post(CREATE_URL).mock(
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
    route = respx.post(CREATE_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

    with pytest.raises(KmsApiError, match="outcome may be unknown"):
        await KmsClient(live_settings).create_and_publish(
            "routineVisits", {"main": {"title": "Visit"}}
        )

    assert route.call_count == 1


@respx.mock
async def test_create_preserves_structured_errors(live_settings):
    route = respx.post(CREATE_URL).mock(
        return_value=httpx.Response(
            422,
            json={"errors": [{"name": "Validation", "msg": "title is required"}]},
        )
    )

    with pytest.raises(KmsApiError) as exc_info:
        await KmsClient(live_settings).create_and_publish("routineVisits", {})

    assert exc_info.value.status_code == 422
    assert exc_info.value.errors == [
        {"name": "Validation", "msg": "title is required"}
    ]
    assert "Validation: title is required" in exc_info.value.detail
    assert route.call_count == 1


@respx.mock
async def test_create_reports_json_error_message(live_settings):
    respx.post(CREATE_URL).mock(
        return_value=httpx.Response(400, json={"error": "Invalid content body"})
    )

    with pytest.raises(KmsApiError, match="Invalid content body") as exc_info:
        await KmsClient(live_settings).create_and_publish("routineVisits", {})

    assert exc_info.value.status_code == 400


@respx.mock
async def test_create_reports_json_error_even_with_200(live_settings):
    respx.post(CREATE_URL).mock(
        return_value=httpx.Response(200, json={"error": "Creation failed"})
    )

    with pytest.raises(KmsApiError, match="Creation failed") as exc_info:
        await KmsClient(live_settings).create_and_publish("routineVisits", {})

    assert exc_info.value.status_code == 200


@respx.mock
async def test_create_reports_html_server_error(live_settings):
    respx.post(CREATE_URL).mock(
        return_value=httpx.Response(500, text="<html>Internal Server Error</html>")
    )

    with pytest.raises(KmsApiError, match="Internal Server Error") as exc_info:
        await KmsClient(live_settings).create_and_publish("routineVisits", {})

    assert exc_info.value.status_code == 500


@respx.mock
async def test_create_401_raises_auth_error(live_settings):
    respx.post(CREATE_URL).mock(
        return_value=httpx.Response(401, json={"error": "Invalid token"})
    )

    with pytest.raises(KmsAuthError, match="Invalid token") as exc_info:
        await KmsClient(live_settings).create_and_publish("routineVisits", {})

    assert exc_info.value.status_code == 401


@respx.mock
async def test_create_404_identifies_tenant_configuration(live_settings):
    respx.post(CREATE_URL).mock(
        return_value=httpx.Response(404, json={"error": "Not found"})
    )

    with pytest.raises(KmsApiError, match="tenant configuration") as exc_info:
        await KmsClient(live_settings).create_and_publish("routineVisits", {})

    assert exc_info.value.status_code == 404


@respx.mock
async def test_create_429_reports_rate_limit_reset(live_settings):
    respx.post(CREATE_URL).mock(
        return_value=httpx.Response(
            429,
            headers={"RateLimit-Reset": "45"},
            json={"error": "Too many requests"},
        )
    )

    with pytest.raises(KmsApiError, match="RateLimit-Reset: 45") as exc_info:
        await KmsClient(live_settings).create_and_publish("routineVisits", {})

    assert exc_info.value.status_code == 429


@respx.mock
async def test_create_rejects_malformed_success_json(live_settings):
    respx.post(CREATE_URL).mock(
        return_value=httpx.Response(200, text="this is not JSON")
    )

    with pytest.raises(KmsApiError, match="expected a JSON object"):
        await KmsClient(live_settings).create_and_publish("routineVisits", {})


@respx.mock
async def test_create_rejects_success_without_content_object(live_settings):
    respx.post(CREATE_URL).mock(return_value=httpx.Response(200, json={"content": None}))

    with pytest.raises(KmsApiError, match="content object"):
        await KmsClient(live_settings).create_and_publish("routineVisits", {})


class _RecordingCreateClient:
    def __init__(self) -> None:
        self.content_type: str | None = None
        self.document: dict[str, Any] | None = None

    async def get_data(self, configs: dict[str, Any]) -> dict[str, Any]:
        return {"data": []}

    async def create_and_publish(
        self, content_type: str, document: dict[str, Any]
    ) -> dict[str, Any]:
        self.content_type = content_type
        self.document = document
        return {"content": {"_id": "created", **document}}


async def test_published_only_client_delegates_create_unchanged():
    inner = _RecordingCreateClient()
    client = PublishedOnlyClient(inner)
    document = {"main": {"title": "Visit"}}

    result = await client.create_and_publish("routineVisits", document)

    assert inner.content_type == "routineVisits"
    assert inner.document is document
    assert inner.document == {"main": {"title": "Visit"}}
    assert result["content"]["_id"] == "created"
