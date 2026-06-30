"""Google-delegated OAuth 2.1 bridge for the remote MCP connector.

Why this exists (see the MCP authorization spec, 2025-06-18): a remote MCP server
is an OAuth *resource server* and Claude is the *client*. The spec REQUIRES that
tokens are **audience-bound** to this server (RFC 8707) and that the server reject
tokens not minted for it. Google can authenticate the human, but it can't mint a
token addressed to this server — so we run a thin authorization server *here* that:

1. speaks MCP-OAuth to Claude (the SDK mounts /authorize, /token, /register and the
   discovery metadata around this provider), and
2. delegates the actual login to Google using the org's existing OAuth client,
   enforcing the @noorahealth.org email domain.

The "token swap": the Google token is consumed once (in `_exchange_google_code`) only
to learn the verified identity; we then mint our OWN short-lived token bound to this
server's canonical URI and hand THAT to Claude. The upstream Google token is never
passed through to the MCP layer (this is what avoids the confused-deputy vuln the
spec forbids).

Tokens are opaque random strings held in memory. On a single VM this is fine — a
restart just forces users to re-auth. (Multi-VM would need a shared store; out of
scope.) No JWT/signing-key dependency.

IMPORTANT: under stdio there is no auth (the spec says stdio takes creds from the
environment). This module is only wired in for the HTTP transport.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from pydantic import AnyHttpUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .settings import Settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "openid email profile"

DEFAULT_ALLOWED_DOMAINS = ("noorahealth.org",)
CALLBACK_PATH = "/auth/google/callback"
DEFAULT_TOKEN_TTL = 3600
CODE_TTL = 300


@dataclass(frozen=True)
class GoogleIdentity:
    """The bits of a Google login we care about (from the id_token)."""

    sub: str
    email: str
    email_verified: bool
    hd: str | None = None


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def is_allowed_identity(
    identity: GoogleIdentity, allowed_domains: tuple[str, ...] | list[str]
) -> bool:
    """True only for a verified email whose EXACT domain is in the allowlist.

    Exact-match (not suffix) on purpose: ``user@noorahealth.org.evil.com`` and
    ``user@evil-noorahealth.org`` must both fail.
    """
    if not identity.email or not identity.email_verified:
        return False
    allowed = {d.strip().lower().lstrip("@") for d in allowed_domains}
    return _email_domain(identity.email) in allowed


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _decode_jwt_payload(token: str) -> dict[str, object]:
    """Decode (NOT verify) a JWT payload.

    Safe here because the id_token is received directly from Google's token endpoint
    over TLS, so per OIDC §3.1.3.7 the signature need not be re-verified. Keeps us off
    a JWT dependency.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthorizeError(error="server_error", error_description="malformed id_token")
    payload_b64 = parts[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64)
        data = json.loads(raw)
    except (binascii.Error, ValueError) as exc:
        raise AuthorizeError(
            error="server_error", error_description="could not decode id_token"
        ) from exc
    if not isinstance(data, dict):
        raise AuthorizeError(error="server_error", error_description="bad id_token payload")
    return data


def _new_secret() -> str:
    # 256 bits of entropy (spec requires >= 128 for auth codes).
    return secrets.token_urlsafe(32)


@dataclass
class _Pending:
    """A login in flight: what we need to rebuild the client redirect after Google."""

    client_id: str
    params: AuthorizationParams


class GoogleBridgeProvider:
    """An ``OAuthAuthorizationServerProvider`` that delegates login to Google.

    Structurally implements the SDK provider Protocol; the SDK handles PKCE, redirect
    matching and client auth at /token, so this focuses on the Google round-trip,
    domain enforcement, and audience-bound token issuance.
    """

    def __init__(
        self,
        *,
        google_client_id: str,
        google_client_secret: str,
        callback_url: str,
        resource_url: str,
        allowed_email_domains: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_DOMAINS,
        access_token_ttl: int = DEFAULT_TOKEN_TTL,
        code_ttl: int = CODE_TTL,
        http_timeout: float = 15.0,
    ) -> None:
        self._google_client_id = google_client_id
        self._google_client_secret = google_client_secret
        self._callback_url = callback_url
        self._resource_url = resource_url.rstrip("/")
        self._allowed_email_domains = tuple(allowed_email_domains)
        self._access_ttl = access_token_ttl
        self._code_ttl = code_ttl
        self._http_timeout = http_timeout

        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}
        self._pending: dict[str, _Pending] = {}

    # --- DCR ---------------------------------------------------------------- #
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id:
            self._clients[client_info.client_id] = client_info

    # --- /authorize -> Google ---------------------------------------------- #
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Return the Google authorization URL to redirect the user to.

        We stash the in-flight request under a freshly minted ``state`` so the Google
        callback can correlate it back to this client + PKCE challenge + resource.
        """
        state = _new_secret()
        self._pending[state] = _Pending(client_id=client.client_id or "", params=params)
        query = {
            "client_id": self._google_client_id,
            "redirect_uri": self._callback_url,
            "response_type": "code",
            "scope": GOOGLE_SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        # Workspace hint (still enforced server-side; a hint is not a security control).
        if len(self._allowed_email_domains) == 1:
            query["hd"] = self._allowed_email_domains[0]
        return f"{GOOGLE_AUTH_URL}?{urlencode(query)}"

    async def _exchange_google_code(self, code: str) -> GoogleIdentity:
        """Swap a Google auth code for the user's verified identity (id_token claims)."""
        data = {
            "code": code,
            "client_id": self._google_client_id,
            "client_secret": self._google_client_secret,
            "redirect_uri": self._callback_url,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data=data)
        if resp.status_code >= 400:
            raise AuthorizeError(
                error="access_denied",
                error_description=f"Google token exchange failed (HTTP {resp.status_code})",
            )
        payload = resp.json()
        id_token = payload.get("id_token")
        if not id_token:
            raise AuthorizeError(
                error="server_error", error_description="Google returned no id_token"
            )
        claims = _decode_jwt_payload(id_token)
        hd = claims.get("hd")
        return GoogleIdentity(
            sub=str(claims.get("sub", "")),
            email=str(claims.get("email", "")),
            email_verified=_as_bool(claims.get("email_verified")),
            hd=str(hd) if hd is not None else None,
        )

    async def complete_google_login(self, code: str | None, state: str | None) -> str:
        """Handle Google's callback; return the URL to redirect the user-agent to.

        On success this mints our own (single-use) authorization code and redirects
        back to the MCP client's redirect_uri. On a disallowed identity it redirects
        back with ``error=access_denied`` (and mints nothing).
        """
        pending = self._pending.pop(state or "", None)
        if pending is None or not code:
            raise AuthorizeError(
                error="invalid_request",
                error_description="unknown or expired login state",
            )
        identity = await self._exchange_google_code(code)
        redirect = str(pending.params.redirect_uri)
        if not is_allowed_identity(identity, self._allowed_email_domains):
            return construct_redirect_uri(
                redirect,
                error="access_denied",
                error_description=(
                    f"{identity.email or 'this account'} is not authorized; "
                    "sign in with your Noora Health account."
                ),
                state=pending.params.state,
            )
        our_code = _new_secret()
        self._codes[our_code] = AuthorizationCode(
            code=our_code,
            scopes=list(pending.params.scopes or []),
            expires_at=time.time() + self._code_ttl,
            client_id=pending.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
            subject=identity.sub,
        )
        return construct_redirect_uri(redirect, code=our_code, state=pending.params.state)

    # --- code/token exchange (SDK calls these) ----------------------------- #
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        ac = self._codes.get(authorization_code)
        if ac is None:
            return None
        if client.client_id and ac.client_id and ac.client_id != client.client_id:
            return None
        if ac.expires_at < time.time():
            self._codes.pop(authorization_code, None)
            return None
        return ac

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)  # single-use
        return self._issue(
            client_id=authorization_code.client_id,
            scopes=list(authorization_code.scopes),
            subject=authorization_code.subject,
            resource=authorization_code.resource,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        rt = self._refresh.get(refresh_token)
        if rt is None:
            return None
        if rt.expires_at is not None and rt.expires_at < time.time():
            self._refresh.pop(refresh_token, None)
            return None
        return rt

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate: the presented refresh token is consumed.
        self._refresh.pop(refresh_token.token, None)
        return self._issue(
            client_id=refresh_token.client_id,
            scopes=list(scopes or refresh_token.scopes),
            subject=refresh_token.subject,
            resource=self._resource_url,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token (this is the resource-server verification path).

        Returns None — i.e. 401 — for unknown, expired, or wrong-audience tokens. The
        audience check is the spec-mandated MUST: only tokens minted for THIS server
        are accepted.
        """
        at = self._access.get(token)
        if at is None:
            return None
        if at.expires_at is not None and at.expires_at < time.time():
            self._access.pop(token, None)
            return None
        if at.resource is not None and at.resource.rstrip("/") != self._resource_url:
            return None
        return at

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._access.pop(token.token, None)
        self._refresh.pop(token.token, None)

    # --- helpers ------------------------------------------------------------ #
    def _issue(
        self,
        *,
        client_id: str,
        scopes: list[str],
        subject: str | None,
        resource: str | None,
    ) -> OAuthToken:
        access = _new_secret()
        refresh = _new_secret()
        now = int(time.time())
        self._access[access] = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + self._access_ttl,
            resource=(resource or self._resource_url),
            subject=subject,
        )
        self._refresh[refresh] = RefreshToken(
            token=refresh, client_id=client_id, scopes=scopes, subject=subject
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self._access_ttl,
            scope=(" ".join(scopes) or None),
            refresh_token=refresh,
        )


# --------------------------------------------------------------------------- #
# Wiring helper
# --------------------------------------------------------------------------- #
@dataclass
class AuthComponents:
    provider: GoogleBridgeProvider
    auth_settings: AuthSettings
    callback_path: str


def _public_url(settings: Settings) -> str:
    raw = (os.environ.get("KMS_PUBLIC_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    # Fall back to the local bind address (fine for local HTTP smoke tests).
    return f"http://{settings.host}:{settings.port}"


def build_auth(settings: Settings) -> AuthComponents | None:
    """Assemble the OAuth bridge from env, or return None if it isn't configured.

    Enabled only when GOOGLE_CLIENT_ID/SECRET are present. The caller (server.py)
    additionally only wires it for the HTTP transport.
    """
    client_id = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not (client_id and client_secret):
        return None

    public_url = _public_url(settings)
    resource_url = f"{public_url}/mcp"
    callback_url = f"{public_url}{CALLBACK_PATH}"

    domains_raw = (os.environ.get("KMS_ALLOWED_EMAIL_DOMAINS") or "").strip()
    allowed = (
        tuple(d.strip() for d in domains_raw.split(",") if d.strip())
        or DEFAULT_ALLOWED_DOMAINS
    )
    ttl_raw = (os.environ.get("KMS_TOKEN_TTL") or "").strip()
    try:
        ttl = int(ttl_raw) if ttl_raw else DEFAULT_TOKEN_TTL
    except ValueError:
        ttl = DEFAULT_TOKEN_TTL

    provider = GoogleBridgeProvider(
        google_client_id=client_id,
        google_client_secret=client_secret,
        callback_url=callback_url,
        resource_url=resource_url,
        allowed_email_domains=allowed,
        access_token_ttl=ttl,
    )
    auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl(public_url),
        resource_server_url=AnyHttpUrl(resource_url),
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[],
    )
    return AuthComponents(provider=provider, auth_settings=auth_settings, callback_path=CALLBACK_PATH)
