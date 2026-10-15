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
    settings = SimpleNamespace(mcp_access_team='test.cloudflareaccess.com',
                               mcp_access_aud='expected-audience',
                               mcp_access_emails=['owner@example.com'])
    instance = AccessVerifier(settings)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    instance.keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key()))
    return instance, private


def signed(private, **changes):
    now = datetime.now(timezone.utc)
    claims = dict(iss='https://test.cloudflareaccess.com', aud='expected-audience',
                  sub='owner', email='owner@example.com', iat=now, exp=now + timedelta(minutes=5))
    claims.update(changes)
    return jwt.encode(claims, private, algorithm='RS256')


def test_access_verifies_signature_claims_and_identity(verifier):
    instance, private = verifier
    assert instance.verify(signed(private))
    for changes in ({'aud': 'wrong'}, {'iss': 'https://evil.example'},
                    {'email': 'other@example.com'}, {'email': None},
                    {'exp': datetime.now(timezone.utc) - timedelta(seconds=1)}):
        assert not instance.verify(signed(private, **changes))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert not instance.verify(signed(other))
    assert not instance.verify(jwt.encode({'email': 'owner@example.com'}, 'forged-secret-that-is-at-least-32-bytes', algorithm='HS256'))
    assert not instance.verify('junk')
    instance.audience = ''
    assert not instance.verify(signed(private))


def test_public_host_cannot_fall_back_to_bearer(settings, monkeypatch, verifier):
    from pydantic import SecretStr
    settings.mcp_enabled = True
    settings.mcp_api_token = SecretStr('dedicated-mcp-test-token-123456789')
    settings.mcp_public_host = 'usage.example.com'
    settings.mcp_allowed_hosts = ['usage.example.com', 'testserver']
    settings.mcp_access_team = 'test.cloudflareaccess.com'
    instance, private = verifier
    monkeypatch.setattr('ai_usage_monitor.api.access.AccessVerifier', lambda _: instance)
    app = create_app(settings, providers=[], start_scheduler=False)
    headers = {'Authorization': 'Bearer dedicated-mcp-test-token-123456789',
               'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'}
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}
    with TestClient(app) as client:
        assert client.post('/mcp', headers=headers, json=body).status_code == 200
        assert client.post('/mcp', headers={**headers, 'Host': 'usage.example.com'}, json=body).status_code == 401
        assert client.post('/mcp', headers={**headers, 'CF-Connecting-IP': '1.2.3.4'}, json=body).status_code == 401
        public = {**headers, 'Host': 'usage.example.com', 'Cf-Access-Jwt-Assertion': signed(private)}
        del public['Authorization']
        assert client.post('/mcp', headers=public, json=body).status_code == 200
        assert client.get('/api/v1/usage', headers=public).status_code == 401
