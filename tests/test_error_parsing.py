"""Tests for error parsing in the real client."""

from __future__ import annotations

import httpx
import pytest
import respx

from dots_kms_mcp.errors import KmsApiError, KmsAuthError
from dots_kms_mcp.getdata_client import KmsClient


@respx.mock
async def test_errors_array_with_400(live_settings):
    respx.post("https://api.example.test/api/discovery/getData").mock(
        return_value=httpx.Response(
            400,
            json={"errors": [{"name": "Config Not Present", "msg": "At least a content type..."}]},
        )
    )
    client = KmsClient(live_settings)
    with pytest.raises(KmsApiError) as ei:
        await client.get_data({"contentTypes": ["articles"]})
    err = ei.value
    assert err.status_code == 400
    assert err.errors[0]["name"] == "Config Not Present"
    assert "Config Not Present" in err.detail


@respx.mock
async def test_401_raises_auth_error(live_settings):
    respx.post("https://api.example.test/api/discovery/getData").mock(
        return_value=httpx.Response(
            401, json={"errors": [{"name": "Auth", "msg": "Invalid Authentication Token"}]}
        )
    )
    client = KmsClient(live_settings)
    with pytest.raises(KmsAuthError) as ei:
        await client.get_data({"contentTypes": ["articles"]})
    assert ei.value.status_code == 401


@respx.mock
async def test_errors_array_even_with_200(live_settings):
    # The API can return errors alongside a 200; we must still raise.
    respx.post("https://api.example.test/api/discovery/getData").mock(
        return_value=httpx.Response(200, json={"errors": [{"name": "X", "msg": "bad"}]})
    )
    client = KmsClient(live_settings)
    with pytest.raises(KmsApiError):
        await client.get_data({"contentTypes": ["articles"]})


@respx.mock
async def test_non_json_500_raises_cleanly(live_settings):
    respx.post("https://api.example.test/api/discovery/getData").mock(
        return_value=httpx.Response(500, text="<html>Internal Server Error</html>")
    )
    client = KmsClient(live_settings)
    with pytest.raises(KmsApiError) as ei:
        await client.get_data({"contentTypes": ["articles"]})
    assert ei.value.status_code == 500


@respx.mock
async def test_timeout_raises_api_error(live_settings):
    respx.post("https://api.example.test/api/discovery/getData").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    client = KmsClient(live_settings)
    with pytest.raises(KmsApiError):
        await client.get_data({"contentTypes": ["articles"]})
