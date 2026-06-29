"""Runtime configuration, loaded from environment variables.

Loads a local ``.env`` (via python-dotenv) if present, then reads:

- ``KMS_AUTH_TOKEN``  - API token (secret)
- ``KMS_TENANT``      - tenant identifier string
- ``KMS_BASE_URL``    - API base URL (default: prod host)
- ``KMS_MOCK``        - force mock on/off; auto-on when creds are missing
- ``KMS_SCHEMA_PATH`` - path to kms_schema.json (else the packaged default)
- ``KMS_TIMEOUT``     - request timeout in seconds (default 30)

IMPORTANT: under the stdio transport, stdout is the protocol channel. Any
diagnostics MUST go to stderr — never print() to stdout.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://okf-be-prod-dot-ok-framework.el.r.appspot.com"
GETDATA_PATH = "/api/discovery/getData"
DEFAULT_TIMEOUT = 30.0

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _env_bool(value: str | None) -> bool | None:
    """Parse a tri-state boolean env var. Returns None if unset/blank/unknown."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings."""

    token: str | None
    tenant: str | None
    base_url: str
    mock: bool
    schema_path: str | None
    timeout: float

    @property
    def getdata_url(self) -> str:
        return self.base_url.rstrip("/") + GETDATA_PATH

    @classmethod
    def from_env(cls, *, load_env_file: bool = True) -> "Settings":
        if load_env_file:
            # override=False so real environment vars win over a stale .env.
            load_dotenv(override=False)

        token = (os.environ.get("KMS_AUTH_TOKEN") or "").strip() or None
        tenant = (os.environ.get("KMS_TENANT") or "").strip() or None
        base_url = (os.environ.get("KMS_BASE_URL") or "").strip() or DEFAULT_BASE_URL
        schema_path = (os.environ.get("KMS_SCHEMA_PATH") or "").strip() or None

        timeout_raw = (os.environ.get("KMS_TIMEOUT") or "").strip()
        try:
            timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT
        except ValueError:
            timeout = DEFAULT_TIMEOUT

        has_creds = bool(token and tenant)
        explicit_mock = _env_bool(os.environ.get("KMS_MOCK"))
        if explicit_mock is None:
            # Auto-mock whenever credentials are absent.
            mock = not has_creds
        else:
            mock = explicit_mock

        settings = cls(
            token=token,
            tenant=tenant,
            base_url=base_url,
            mock=mock,
            schema_path=schema_path,
            timeout=timeout,
        )
        settings._warn_if_inconsistent()
        return settings

    def _warn_if_inconsistent(self) -> None:
        """Emit guidance to stderr about the chosen mode."""
        if self.mock:
            if not (self.token and self.tenant):
                print(
                    "[dots-kms-mcp] Running in MOCK mode (no KMS_AUTH_TOKEN/KMS_TENANT). "
                    "Tools return realistic sample data. Set both env vars and KMS_MOCK=0 "
                    "to query the live KMS.",
                    file=sys.stderr,
                )
            else:
                print(
                    "[dots-kms-mcp] Running in MOCK mode (KMS_MOCK is set). "
                    "Tools return sample data; the live KMS is not contacted.",
                    file=sys.stderr,
                )
        else:
            if not (self.token and self.tenant):
                print(
                    "[dots-kms-mcp] WARNING: live mode requested but KMS_AUTH_TOKEN/KMS_TENANT "
                    "are incomplete — API calls will fail with auth errors.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[dots-kms-mcp] Running in LIVE mode against {self.base_url}.",
                    file=sys.stderr,
                )
