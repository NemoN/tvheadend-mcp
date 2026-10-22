import json
from urllib.parse import parse_qs

import httpx
import pytest

from tvheadend_mcp.client import TvheadendClient, TvheadendError
from tvheadend_mcp.config import Settings


async def test_real_form_encoding_prefix_and_false_values():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    client = TvheadendClient(
        Settings("http://tvh.test:9981/tvh/", auth="none"), httpx.MockTransport(handler)
    )
    await client.request(
        "idnode/save",
        {"node": {"uuid": "a" * 32, "enabled": False}, "enabled": False, "name": "", "omit": None},
        write=True,
    )
    await client.close()
    request = seen[0]
    assert request.url.path == "/tvh/api/idnode/save"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    fields = parse_qs(request.content.decode(), keep_blank_values=True)
    assert json.loads(fields["node"][0])["enabled"] is False
    assert fields["enabled"] == ["0"]
    assert fields["name"] == [""]
    assert "omit" not in fields


async def test_digest_challenge_authenticates_without_changing_payload():
    seen = []

    def handler(request):
        seen.append(request)
        if "authorization" not in request.headers:
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Digest realm="tvh", nonce="12345", algorithm=MD5, qop="auth"'
                    )
                },
            )
        return httpx.Response(200, json={"uuid": "a" * 32})

    client = TvheadendClient(
        Settings("http://tvh.test:9981", "user", "secret"), httpx.MockTransport(handler)
    )
    await client.request("channel/create", {"conf": {"name": "Öffentlich"}}, write=True)
    await client.close()
    assert len(seen) == 2
    assert seen[1].headers["authorization"].startswith("Digest ")
    assert "secret" not in seen[1].headers["authorization"]
    assert seen[0].content == seen[1].content


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 500])
async def test_http_errors_are_actionable_and_do_not_leak_credentials(status):
    client = TvheadendClient(
        Settings("http://tvh.test:9981", "user", "TOP_SECRET", auth="basic"),
        httpx.MockTransport(lambda r: httpx.Response(status, text="TOP_SECRET")),
    )
    with pytest.raises(TvheadendError) as exc:
        await client.request("serverinfo")
    await client.close()
    assert "TOP_SECRET" not in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        {"success": False},
        {"success": 0},
        {"error": "failed"},
        {"errors": ["failed"]},
        "not-an-object",
    ],
)
async def test_json_errors_are_not_reported_as_success(data):
    client = TvheadendClient(
        Settings("http://tvh.test:9981", auth="none"),
        httpx.MockTransport(lambda r: httpx.Response(200, json=data)),
    )
    with pytest.raises(TvheadendError):
        await client.request("channel/create", write=True)
    await client.close()


async def test_read_only_blocks_post_before_network_request():
    def unexpected(request):
        pytest.fail("A read-only write must never reach the network")

    client = TvheadendClient(
        Settings("http://tvh.test:9981", read_only=True), httpx.MockTransport(unexpected)
    )
    with pytest.raises(TvheadendError, match="TVH_READ_ONLY"):
        await client.request("idnode/save", write=True)
    await client.close()


async def test_write_timeout_is_never_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout")

    client = TvheadendClient(
        Settings("http://tvh.test:9981", auth="none"), httpx.MockTransport(handler)
    )
    with pytest.raises(TvheadendError, match="outcome is unknown"):
        await client.request("channel/create", {"conf": {"name": "TV"}}, write=True)
    await client.close()
    assert calls == 1


@pytest.mark.parametrize(
    "path",
    [
        "http://other.test/x",
        "../idnode/save",
        "/serverinfo",
        "serverinfo?x=1",
        "mpegts/%2e%2e/save",
    ],
)
async def test_api_path_cannot_override_base_url(path):
    client = TvheadendClient(Settings("http://tvh.test:9981"))
    with pytest.raises(ValueError):
        await client.request(path)
    await client.close()
