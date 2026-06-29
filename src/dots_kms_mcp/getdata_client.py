"""Async HTTP client for the getData discovery API.

Encapsulates the two things that are easy to get wrong by hand:

1. The **double-stringify**: the request body is a JSON object whose ``configs``
   field is itself the config object run through ``json.dumps`` (a JSON *string*).
2. **Error parsing**: the API signals problems via ``{"errors": [{name, msg}]}``
   (often alongside a 400/401), so a 200 is not the only thing to check.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

import httpx

from .configs import validate_configs
from .errors import KmsApiError, KmsAuthError
from .settings import Settings


@runtime_checkable
class KmsClientProtocol(Protocol):
    """Either the real client or the mock satisfies this; tools depend on it."""

    async def get_data(self, configs: dict[str, Any]) -> dict[str, Any]: ...


def build_client(settings: Settings) -> KmsClientProtocol:
    """Return a mock or real client based on ``settings.mock``.

    Imported lazily to keep the mock out of the hot path for live deployments.
    """
    if settings.mock:
        from .mock_client import MockKmsClient

        return MockKmsClient()
    return KmsClient(settings)


class KmsClient:
    """Real client that POSTs to ``{base_url}/api/discovery/getData``."""

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

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        status = response.status_code

        # Parse JSON defensively — error responses may not always be JSON.
        payload: Any
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None

        # API-level errors arrive as {"errors": [{name, msg}]}, sometimes with 200.
        if isinstance(payload, dict) and payload.get("errors"):
            errors = payload["errors"]
            message = "getData returned errors"
            exc_cls = KmsAuthError if status == 401 else KmsApiError
            raise exc_cls(message, errors=errors, status_code=status)

        if status >= 400:
            text = (response.text or "").strip()
            snippet = text[:300] if text else "(no body)"
            message = f"getData failed with HTTP {status}: {snippet}"
            exc_cls = KmsAuthError if status == 401 else KmsApiError
            raise exc_cls(message, status_code=status)

        if not isinstance(payload, dict):
            raise KmsApiError(
                f"Unexpected getData response (HTTP {status}): expected a JSON object."
            )

        return payload
