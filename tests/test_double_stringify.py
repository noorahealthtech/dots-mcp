"""The critical test: the wire payload must double-encode `configs`.

The body must be a JSON object whose `configs` value is a JSON *string* (the
config object run through json.dumps), and the auth headers must be present.
"""

from __future__ import annotations

import json

import httpx
import respx

from dots_kms_mcp.getdata_client import KmsClient


@respx.mock
async def test_configs_is_double_encoded_on_the_wire(live_settings):
    route = respx.post("https://api.example.test/api/discovery/getData").mock(
        return_value=httpx.Response(200, json={"data": [], "count": 0})
    )

    client = KmsClient(live_settings)
    configs = {"contentTypes": ["articles"], "limit": 10, "skip": 0, "countData": True}
    await client.get_data(configs)

    assert route.called
    request = route.calls.last.request

    # Headers carry token + tenant.
    assert request.headers["x-auth-token"] == "TEST_TOKEN"
    assert request.headers["tenant"] == "TEST_TENANT"

    # Body is {"configs": "<json string>"} — note configs is a STRING.
    body = json.loads(request.content)
    assert set(body) == {"configs"}
    assert isinstance(body["configs"], str)

    # And that string decodes back to the original config object.
    assert json.loads(body["configs"]) == configs


@respx.mock
async def test_returns_parsed_payload(live_settings):
    respx.post("https://api.example.test/api/discovery/getData").mock(
        return_value=httpx.Response(200, json={"data": [{"_id": "1"}], "count": 1, "skip": 10})
    )
    client = KmsClient(live_settings)
    result = await client.get_data({"contentTypes": ["articles"]})
    assert result == {"data": [{"_id": "1"}], "count": 1, "skip": 10}
