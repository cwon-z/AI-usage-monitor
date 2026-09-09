from __future__ import annotations

from fastapi.testclient import TestClient

from ai_usage_monitor.main import create_app
from ai_usage_monitor.providers.base import ProviderError, UsageProvider
from ai_usage_monitor.services.normalizer import utc_now

AUTH = {"Authorization": "Bearer test-widget-token-1234567890"}


class FakeProvider(UsageProvider):
    def __init__(self, name, usage=None, error=None):
        self.name = name
        self.usage = usage
        self.error = error
        self.calls = 0

    async def get_usage(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.usage.model_copy(update={"collected_at": utc_now()})


def test_api_authentication(settings):
    app = create_app(settings, providers=[], start_scheduler=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/usage").status_code == 401
        assert (
            client.get(
                "/api/v1/usage", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/usage", headers=AUTH).status_code == 200


def test_compact_response(settings, sample_usage):
    claude = FakeProvider("claude", sample_usage)
    openai_usage = sample_usage.model_copy(
        update={
            "session": None,
            "five_hour": sample_usage.session,
            "collected_at": utc_now(),
        }
    )
    openai = FakeProvider("openai", openai_usage)
    app = create_app(settings, providers=[claude, openai], start_scheduler=False)
    app.state.repository.save_success("claude", sample_usage)
    app.state.repository.save_success("openai", openai_usage)
    with TestClient(app) as client:
        response = client.get("/api/v1/usage/compact", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["claude"]["session"] == 63
    assert body["claude"]["weekly"] == 39
    assert body["openai"]["five_hour"] == 63
    assert body["openai"]["weekly"] == 39
    assert body["claude"]["session_reset_in"] is not None


def test_history_endpoint(settings, sample_usage):
    app = create_app(settings, providers=[], start_scheduler=False)
    app.state.repository.save_success("claude", sample_usage)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/usage/history?provider=claude&limit=10", headers=AUTH
        )
        naive = client.get(
            "/api/v1/usage/history?since=2026-01-01T00:00:00", headers=AUTH
        )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert naive.status_code == 422


def test_manual_refresh_and_rate_limit(settings, sample_usage):
    provider = FakeProvider("claude", sample_usage)
    app = create_app(settings, providers=[provider], start_scheduler=False)
    with TestClient(app) as client:
        first = client.post("/api/v1/usage/refresh", headers=AUTH)
        second = client.post("/api/v1/usage/refresh", headers=AUTH)
    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second.headers
    assert provider.calls == 1


def test_failed_provider_does_not_take_down_refresh(settings, sample_usage):
    claude = FakeProvider("claude", sample_usage)
    openai = FakeProvider("openai", error=ProviderError("timeout"))
    app = create_app(settings, providers=[claude, openai], start_scheduler=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/usage/refresh", headers=AUTH)
    assert response.status_code == 207
    body = response.json()
    assert body["providers"]["claude"]["status"] == "ok"
    assert body["providers"]["openai"]["status"] == "error"


def test_providers_endpoint_does_not_expose_paths(settings):
    app = create_app(settings, providers=[], start_scheduler=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/providers", headers=AUTH)
    serialized = response.text
    assert response.status_code == 200
    assert str(settings.codex_auth_file) not in serialized
    assert "test-widget-token" not in serialized


def test_providers_reflect_successful_implicit_cli_auth(settings, sample_usage):
    app = create_app(settings, providers=[], start_scheduler=False)
    app.state.repository.save_success("claude", sample_usage)
    with TestClient(app) as client:
        body = client.get("/api/v1/providers", headers=AUTH).json()
    claude = next(item for item in body["providers"] if item["name"] == "claude")
    assert claude["configured"] is True
