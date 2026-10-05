"""Tests for the Google-delegated OAuth bridge (src/dots_kms_mcp/auth.py).

Security-critical, so it gets focused unit tests:
- domain restriction (only @noorahealth.org may obtain a token),
- the token "swap" + **audience binding** (tokens are minted for THIS server and
  load_access_token rejects tokens whose audience/resource isn't this server),
- expiry, refresh rotation, and the Google-callback authorization-code flow.

The Google HTTP exchange is stubbed (we override `_exchange_google_code`) so no
network is touched.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import AnyUrl

from dots_kms_mcp.auth import (
    GoogleBridgeProvider,
    GoogleIdentity,
    build_auth,
    is_allowed_identity,
)
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams, AccessToken
from mcp.shared.auth import OAuthClientInformationFull

RES = "https://dots.mcp.noorahealth.org/mcp"
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"
ALLOWED = ("noorahealth.org",)


def _provider() -> GoogleBridgeProvider:
    return GoogleBridgeProvider(
        google_client_id="GID",
        google_client_secret="GSEC",
        callback_url="https://dots.mcp.noorahealth.org/auth/google/callback",
        resource_url=RES,
        allowed_email_domains=ALLOWED,
        access_token_ttl=3600,
    )


async def _registered_client(provider: GoogleBridgeProvider) -> OAuthClientInformationFull:
    client = OAuthClientInformationFull(client_id="c1", redirect_uris=[AnyUrl(CLAUDE_CB)])
    await provider.register_client(client)
    return client


def _params() -> AuthorizationParams:
    return AuthorizationParams(
        state="client-state",
        scopes=[],
        code_challenge="chal",
        redirect_uri=AnyUrl(CLAUDE_CB),
        redirect_uri_provided_explicitly=True,
        resource=RES,
    )


# --------------------------------------------------------------------------- #
# Domain restriction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "email,verified,expected",
    [
        ("alice@noorahealth.org", True, True),
        ("Alice@NOORAHEALTH.ORG", True, True),          # case-insensitive
        ("bob@gmail.com", True, False),                 # wrong domain
        ("eve@noorahealth.org", False, False),          # email not verified
        ("mal@noorahealth.org.evil.com", True, False),  # suffix spoof -> domain is evil.com
        ("mal@evil-noorahealth.org", True, False),      # lookalike domain
        ("", True, False),                              # no email
    ],
)
def test_is_allowed_identity(email, verified, expected):
    ident = GoogleIdentity(sub="s", email=email, email_verified=verified, hd=None)
    assert is_allowed_identity(ident, ALLOWED) is expected


# --------------------------------------------------------------------------- #
# Audience binding + token lifecycle (the "token swap")
# --------------------------------------------------------------------------- #
async def test_exchange_code_mints_audience_bound_token():
    p = _provider()
    client = await _registered_client(p)
    code = AuthorizationCode(
        code="ac", scopes=[], expires_at=time.time() + 300, client_id="c1",
        code_challenge="x", redirect_uri=AnyUrl(CLAUDE_CB),
        redirect_uri_provided_explicitly=True, resource=RES, subject="google-sub-123",
    )
    tok = await p.exchange_authorization_code(client, code)
    assert tok.access_token and tok.refresh_token

    at = await p.load_access_token(tok.access_token)
    assert at is not None
    assert at.resource == RES          # minted for THIS server
    assert at.subject == "google-sub-123"
    # the used authorization code is single-use
    assert await p.load_authorization_code(client, "ac") is None


async def test_load_access_token_rejects_wrong_audience():
    p = _provider()
    p._access["foreign"] = AccessToken(
        token="foreign", client_id="c1", scopes=[],
        expires_at=int(time.time()) + 999, resource="https://evil.example/mcp",
    )
    # The mandatory audience check (spec MUST) refuses a token minted for another resource.
    assert await p.load_access_token("foreign") is None


async def test_load_access_token_rejects_expired():
    p = _provider()
    p._access["stale"] = AccessToken(
        token="stale", client_id="c1", scopes=[],
        expires_at=int(time.time()) - 1, resource=RES,
    )
    assert await p.load_access_token("stale") is None
    assert "stale" not in p._access  # purged


async def test_refresh_token_rotation():
    p = _provider()
    client = await _registered_client(p)
    code = AuthorizationCode(
        code="ac", scopes=[], expires_at=time.time() + 300, client_id="c1",
        code_challenge="x", redirect_uri=AnyUrl(CLAUDE_CB),
        redirect_uri_provided_explicitly=True, resource=RES, subject="s",
    )
    tok = await p.exchange_authorization_code(client, code)
    rt = await p.load_refresh_token(client, tok.refresh_token)
    assert rt is not None

    new = await p.exchange_refresh_token(client, rt, scopes=[])
    assert new.access_token != tok.access_token
    assert new.refresh_token != tok.refresh_token
    # old refresh token is rotated out (no longer valid)
    assert await p.load_refresh_token(client, tok.refresh_token) is None
    # new access token validates and is audience-bound
    at = await p.load_access_token(new.access_token)
    assert at is not None and at.resource == RES


# --------------------------------------------------------------------------- #
# /authorize -> Google -> callback flow
# --------------------------------------------------------------------------- #
async def test_authorize_redirects_to_google_and_stores_pending():
    p = _provider()
    client = await _registered_client(p)
    url = await p.authorize(client, _params())
    assert url.startswith("https://accounts.google.com/")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["GID"]
    assert q["redirect_uri"] == ["https://dots.mcp.noorahealth.org/auth/google/callback"]
    assert q["response_type"] == ["code"]
    # a pending entry keyed by the google `state` we generated
    gstate = q["state"][0]
    assert gstate in p._pending


async def _start_login(p: GoogleBridgeProvider, client) -> str:
    url = await p.authorize(client, _params())
    return parse_qs(urlparse(url).query)["state"][0]


async def test_callback_allowed_identity_mints_code():
    p = _provider()
    client = await _registered_client(p)
    gstate = await _start_login(p, client)

    async def fake(code):  # stub Google exchange
        return GoogleIdentity(sub="g-sub", email="alice@noorahealth.org",
                              email_verified=True, hd="noorahealth.org")
    p._exchange_google_code = fake

    location = await p.complete_google_login(code="gcode", state=gstate)
    lq = parse_qs(urlparse(location).query)
    assert urlparse(location).netloc == "claude.ai"
    assert lq["state"] == ["client-state"]      # client's original state echoed back
    assert "error" not in lq
    our_code = lq["code"][0]

    ac = await p.load_authorization_code(client, our_code)
    assert ac is not None
    assert ac.code_challenge == "chal"          # PKCE challenge carried for the SDK to verify
    assert ac.resource == RES                   # resource indicator preserved
    assert ac.subject == "g-sub"
    assert gstate not in p._pending             # pending consumed
    identity = p.identity_for_subject("g-sub")
    assert identity is not None
    assert identity.email == "alice@noorahealth.org"
    assert identity.email_verified is True


async def test_callback_denied_for_wrong_domain():
    p = _provider()
    client = await _registered_client(p)
    gstate = await _start_login(p, client)

    async def fake(code):
        return GoogleIdentity(sub="x", email="mallory@gmail.com",
                              email_verified=True, hd=None)
    p._exchange_google_code = fake

    location = await p.complete_google_login(code="gcode", state=gstate)
    lq = parse_qs(urlparse(location).query)
    assert lq["error"] == ["access_denied"]     # redirected back with an error
    assert "code" not in lq                      # NO authorization code minted
    assert not p._codes                          # nothing stored
    assert p.identity_for_subject("x") is None


async def test_callback_unknown_state_is_rejected():
    p = _provider()
    from mcp.server.auth.provider import AuthorizeError
    with pytest.raises(AuthorizeError):
        await p.complete_google_login(code="gcode", state="never-issued")


# --------------------------------------------------------------------------- #
# build_auth gating
# --------------------------------------------------------------------------- #
def test_build_auth_disabled_without_google_creds(monkeypatch):
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "KMS_PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    from dots_kms_mcp.settings import Settings
    s = Settings.from_env(load_env_file=False)
    assert build_auth(s) is None


def test_build_auth_enabled_with_creds(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "GID")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "GSEC")
    monkeypatch.setenv("KMS_PUBLIC_URL", "https://dots.mcp.noorahealth.org")
    monkeypatch.setenv("KMS_TRANSPORT", "streamable-http")
    from dots_kms_mcp.settings import Settings
    s = Settings.from_env(load_env_file=False)
    comp = build_auth(s)
    assert comp is not None
    assert str(comp.auth_settings.issuer_url).rstrip("/") == "https://dots.mcp.noorahealth.org"
    assert str(comp.auth_settings.resource_server_url) == RES
    assert comp.callback_path == "/auth/google/callback"
    assert isinstance(comp.provider, GoogleBridgeProvider)
