"""Async HTTP client for KMS discovery and create-and-publish APIs.

Encapsulates the three things that are easy to get wrong by hand:

1. The **double-stringify**: the request body is a JSON object whose ``configs``
   field is itself the config object run through ``json.dumps`` (a JSON *string*).
2. **Error parsing**: the API signals problems via ``{"errors": [{name, msg}]}``
   (often alongside a 400/401), so a 200 is not the only thing to check.
3. **Create encoding**: create-and-publish sends the document as ordinary JSON and
   never retries a timeout because the first request may have succeeded.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

import httpx

from .configs import merge_find_query, validate_configs
from .errors import KmsApiError, KmsAuthError
from .settings import Settings

# The document status field + the value that means "visible". Confirmed against the
# live tenant (e.g. reports: 81/300 published; every content type has published docs).
PUBLISHED_FIELD = "kp_published_status"
PUBLISHED_VALUE = "published"


@runtime_checkable
class KmsClientProtocol(Protocol):
    """Either the real client or the mock satisfies this; tools depend on it."""

    async def get_data(self, configs: dict[str, Any]) -> dict[str, Any]: ...

    async def create_and_publish(
        self, content_type: str, document: dict[str, Any]
    ) -> dict[str, Any]: ...


def build_client(settings: Settings) -> KmsClientProtocol:
    """Return a mock or real client based on ``settings.mock``.

    Wrapped in ``PublishedOnlyClient`` when ``settings.published_only`` (default under
    the HTTP transport) so the public connector never surfaces unpublished drafts.
    The mock is imported lazily to keep it out of the hot path for live deployments.
    """
    client: KmsClientProtocol
    if settings.mock:
        from .mock_client import MockKmsClient

        client = MockKmsClient()
    else:
        client = KmsClient(settings)
    if settings.published_only:
        client = PublishedOnlyClient(client)
    return client


class PublishedOnlyClient:
    """Client decorator that restricts CONTENT queries to published documents.

    Merges ``{kp_published_status: "published"}`` into the findQuery of every
    ``contentTypes`` query and leaves ``profileTypes`` queries untouched. The merge is
    a hard AND (via ``merge_find_query``), so it can't be overridden to reveal drafts —
    a query that also asked for drafts collides under ``$and`` and returns nothing.
    Used on the public remote connector, which runs on one shared service token.
    """

    def __init__(
        self,
        inner: KmsClientProtocol,
        field: str = PUBLISHED_FIELD,
        value: str = PUBLISHED_VALUE,
    ) -> None:
        self._inner = inner
        self._field = field
        self._value = value

    async def get_data(self, configs: dict[str, Any]) -> dict[str, Any]:
        if configs.get("contentTypes"):
            merged = merge_find_query(configs.get("findQuery"), {self._field: self._value})
            configs = {**configs, "findQuery": merged}  # copy; never mutate the caller's dict
        return await self._inner.get_data(configs)

    async def create_and_publish(
        self, content_type: str, document: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._inner.create_and_publish(content_type, document)


class KmsClient:
    """Real client for getData and create-and-publish operations."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "x-auth-token": self._settings.token or "",
            "tenant": self._settings.tenant or "",
            "content-type": "application/json",
        }

    async def get_data(self, configs: dict[str, Any]) -> dict[str, Any]:
        # Fail fast on the content/profile invariant before hitting the network.
        validate_configs(configs)

        # The critical double-stringify: configs is a JSON STRING inside the body.
        body = {"configs": json.dumps(configs, separators=(",", ":"), ensure_ascii=False)}

        try:
            async with httpx.AsyncClient(timeout=self._settings.timeout) as client:
                response = await client.post(
                    self._settings.getdata_url,
                    headers=self._headers,
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise KmsApiError(f"Request to getData timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise KmsApiError(f"HTTP error calling getData: {exc}") from exc

        return self._parse_response(response, "getData")

    async def create_and_publish(
        self, content_type: str, document: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._settings.timeout) as client:
                response = await client.post(
                    self._settings.create_url(content_type),
                    headers=self._headers,
                    json=document,
                )
        except httpx.TimeoutException as exc:
            raise KmsApiError(
                "Request to createAndPublishContent timed out; outcome may be unknown. "
                f"Do not retry automatically: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise KmsApiError(
                "HTTP error calling createAndPublishContent; outcome may be unknown. "
                f"Do not retry automatically: {exc}"
            ) from exc

        payload = self._parse_response(response, "createAndPublishContent")
        if not isinstance(payload.get("content"), dict):
            raise KmsApiError(
                "Unexpected createAndPublishContent response "
                f"(HTTP {response.status_code}): expected a content object."
            )
        return payload

    @staticmethod
    def _parse_response(
        response: httpx.Response, operation: str
    ) -> dict[str, Any]:
        status = response.status_code

        # Parse JSON defensively — error responses may not always be JSON.
        payload: Any
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None

        context = ""
        if status == 404:
            context += "; check the tenant configuration and content type"
        if status == 429 and response.headers.get("RateLimit-Reset"):
            context += f"; RateLimit-Reset: {response.headers['RateLimit-Reset']}"

        # API-level errors arrive as {"errors": [{name, msg}]}, sometimes with 200.
        if isinstance(payload, dict) and payload.get("errors"):
            errors = payload["errors"]
            message = f"{operation} returned errors{context}"
            exc_cls = KmsAuthError if status == 401 else KmsApiError
            raise exc_cls(message, errors=errors, status_code=status)

        api_error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(api_error, str) and api_error.strip():
            message = f"{operation} returned error: {api_error.strip()[:300]}{context}"
            exc_cls = KmsAuthError if status == 401 else KmsApiError
            raise exc_cls(message, status_code=status)

        if status >= 400:
            text = (response.text or "").strip()
            snippet = text[:300] if text else "(no body)"
            message = f"{operation} failed with HTTP {status}: {snippet}{context}"
            exc_cls = KmsAuthError if status == 401 else KmsApiError
            raise exc_cls(message, status_code=status)

        if not isinstance(payload, dict):
            raise KmsApiError(
                f"Unexpected {operation} response (HTTP {status}): "
                "expected a JSON object."
            )

        return payload
