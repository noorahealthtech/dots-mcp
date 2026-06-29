"""Exception types for the KMS client.

These mirror how the getData API signals problems:

- Local validation failures (e.g. neither/both of contentTypes/profileTypes) ->
  ``KmsConfigError`` (analogous to the API's 400 "config" responses, but caught
  before we make a network call).
- The API returning ``{"errors": [{"name", "msg"}]}`` or a non-2xx status ->
  ``KmsApiError`` (or ``KmsAuthError`` for 401).

The server layer catches these and re-raises as ``ValueError`` with a readable
message so the LLM sees a recoverable tool error it can act on.
"""

from __future__ import annotations

from typing import Any


class KmsError(Exception):
    """Base class for all KMS client errors."""


class KmsConfigError(KmsError):
    """The request config is invalid (caught locally before calling the API)."""


class KmsApiError(KmsError):
    """The getData API returned an error response.

    Attributes:
        errors: The parsed ``errors`` array from the response body, each item a
            dict like ``{"name": "...", "msg": "..."}``. May be empty if the API
            returned a non-2xx status without a structured body.
        status_code: The HTTP status code, if available.
    """

    def __init__(
        self,
        message: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.errors: list[dict[str, Any]] = errors or []
        self.status_code = status_code

    @property
    def detail(self) -> str:
        """A human-readable summary combining status and per-error name/msg."""
        parts: list[str] = []
        if self.status_code is not None:
            parts.append(f"HTTP {self.status_code}")
        for err in self.errors:
            name = err.get("name")
            msg = err.get("msg")
            if name and msg:
                parts.append(f"{name}: {msg}")
            elif msg:
                parts.append(str(msg))
            elif name:
                parts.append(str(name))
        summary = " | ".join(parts)
        base = str(self)
        if summary and summary not in base:
            return f"{base} ({summary})"
        return base or summary


class KmsAuthError(KmsApiError):
    """Authentication/authorization failure (HTTP 401)."""
