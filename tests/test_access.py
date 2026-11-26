from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from ai_usage_monitor.api.access import AccessVerifier
from ai_usage_monitor.main import create_app


@pytest.fixture
def verifier():
    settings = SimpleNamespace(
        mcp_access_team="test.cloudflareaccess.com",
        mcp_access_aud="expected-audience",
        mcp_access_emails=["owner@example.com"],
    )
    instance = AccessVerifier(settings)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    instance.keys = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())
    )
    return instance, private


def signed(private, **changes):
    now = datetime.now(timezone.utc)
    claims = dict(
        iss="https://test.cloudflareaccess.com",
        aud="expected-audience",
        sub="owner",
        email="owner@example.com",
        iat=now,
        exp=now + timedelta(minutes=5),
    )
    claims.update(changes)
    return jwt.encode(claims, private, algorithm="RS256")


def test_access_verifies_signature_claims_and_identity(verifier):
    instance, private = verifier
    assert instance.verify(signed(private))
    for changes in (
        {"aud": "wrong"},
        {"iss": "https://evil.example"},
        {"email": "other@example.com"},
        {"email": None},
        {"exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
    ):
        assert not instance.verify(signed(private, **changes))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert not instance.verify(signed(other))
    assert not instance.verify(
        jwt.encode(
            {"email": "owner@example.com"},
            "forged-secret-that-is-at-least-32-bytes",
            algorithm="HS256",
        )
    )
    assert not instance.verify("junk")
    instance.audience = ""
    assert not instance.verify(signed(private))


def test_public_host_cannot_fall_back_to_bearer(settings, monkeypatch, verifier):
    from pydantic import SecretStr

    settings.mcp_enabled = True
    settings.mcp_api_token = SecretStr("dedicated-mcp-test-token-123456789")
    settings.mcp_public_host = "usage.example.com"
    settings.mcp_allowed_hosts = ["usage.example.com", "testserver"]
    settings.mcp_access_team = "test.cloudflareaccess.com"
    instance, private = verifier
    monkeypatch.setattr(
        "ai_usage_monitor.api.access.AccessVerifier", lambda _: instance
    )
    app = create_app(settings, providers=[], start_scheduler=False)
    headers = {
        "Authorization": "Bearer dedicated-mcp-test-token-123456789",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with TestClient(app) as client:
        assert client.post("/mcp", headers=headers, json=body).status_code == 200
        assert (
            client.post(
                "/mcp", headers={**headers, "Host": "usage.example.com"}, json=body
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/mcp", headers={**headers, "CF-Connecting-IP": "1.2.3.4"}, json=body
            ).status_code
            == 401
        )
        public = {
            **headers,
            "Host": "usage.example.com",
            "Cf-Access-Jwt-Assertion": signed(private),
        }
        del public["Authorization"]
        assert client.post("/mcp", headers=public, json=body).status_code == 200
        assert client.get("/api/v1/usage", headers=public).status_code == 401


def mcp_settings(settings):
    from pydantic import SecretStr

    settings.mcp_enabled = True
    settings.mcp_api_token = SecretStr("dedicated-mcp-test-token-123456789")
    settings.mcp_public_host = "usage.example.com"
    settings.mcp_allowed_hosts = ["usage.example.com", "testserver"]
    settings.mcp_access_team = "test.cloudflareaccess.com"
    return settings


def test_public_host_setting_is_normalized_or_rejected():
    from pydantic import ValidationError

    from ai_usage_monitor.config.settings import Settings

    def host(value):
        return Settings(
            _env_file=None,
            widget_api_token="test-widget-token-1234567890",
            mcp_public_host=value,
        ).mcp_public_host

    assert host(" Usage.Example.COM. ") == "usage.example.com"
    assert host("") == ""
    for never_matches in ("https://usage.example.com", "usage.example.com:443", "a/b"):
        with pytest.raises(ValidationError):
            host(never_matches)


def test_public_host_match_ignores_case_port_and_trailing_dot(
    settings, monkeypatch, verifier
):
    instance, _private = verifier
    monkeypatch.setattr(
        "ai_usage_monitor.api.access.AccessVerifier", lambda _: instance
    )
    app = create_app(mcp_settings(settings), providers=[], start_scheduler=False)
    bearer = {"Authorization": "Bearer dedicated-mcp-test-token-123456789"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with TestClient(app) as client:
        for spelling in (
            "USAGE.example.com",
            "usage.example.com.",
            "usage.example.com:8443",
        ):
            response = client.post(
                "/mcp", headers={**bearer, "Host": spelling}, json=body
            )
            assert response.status_code == 401, spelling


@pytest.mark.asyncio
async def test_non_http_transports_are_refused_before_the_mcp_app(settings):
    from ai_usage_monitor.api.mcp import MCPAuthentication

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    guard = MCPAuthentication(inner, "dedicated-mcp-test-token-123456789")
    sent = []

    async def send(message):
        sent.append(message)

    await guard({"type": "websocket", "headers": []}, None, send)
    assert reached == []
    assert sent == [{"type": "websocket.close", "code": 1008}]
    await guard({"type": "lifespan"}, None, send)
    assert reached == ["lifespan"]


@pytest.mark.asyncio
async def test_access_checks_cannot_exhaust_worker_threads():
    import asyncio
    import threading

    from ai_usage_monitor.api import mcp

    release = threading.Event()
    started = []

    class SlowVerifier:
        def verify(self, token):
            started.append(token)
            release.wait(5)
            return True

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = mcp.MCPAuthentication(inner, "dedicated-mcp-test-token-123456789")
    guard.access = SlowVerifier()

    async def request():
        statuses = []

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        scope = {
            "type": "http",
            "headers": [(b"cf-access-jwt-assertion", b"token")],
        }
        await guard(scope, None, send)
        return statuses[0]

    extra = 3
    tasks = [
        asyncio.create_task(request())
        for _ in range(mcp.MAX_CONCURRENT_ACCESS_CHECKS + extra)
    ]
    try:
        # The requests over the cap are refused while the others still block.
        done, pending = await asyncio.wait(tasks, timeout=0.5)
        assert len(started) == mcp.MAX_CONCURRENT_ACCESS_CHECKS
        assert sorted(task.result() for task in done) == [401] * extra
        assert len(pending) == mcp.MAX_CONCURRENT_ACCESS_CHECKS
    finally:
        release.set()
    assert sorted(await asyncio.gather(*tasks)) == [200, 200, 401, 401, 401]
    assert await request() == 200
