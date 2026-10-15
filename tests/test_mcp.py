from datetime import timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.main import create_app

TOKEN = "dedicated-mcp-test-token-123456789"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}


@pytest.fixture
def app(settings):
    settings.mcp_enabled = True
    settings.mcp_api_token = Settings(
        _env_file=None, widget_api_token=TOKEN
    ).widget_api_token
    settings.mcp_allowed_hosts = ["testserver"]
    return create_app(settings, providers=[], start_scheduler=False)


def rpc(client, method, params=None, headers=None):
    return client.post(
        "/mcp",
        headers=HEADERS if headers is None else headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def call(client, name, arguments=None):
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_protocol_and_authentication(app, sample_usage):
    app.state.repository.save_success("claude", sample_usage)
    with TestClient(app) as client:
        for headers in (
            {},
            {**HEADERS, "Authorization": "Bearer test-widget-token-1234567890"},
        ):
            assert rpc(client, "tools/list", headers=headers).status_code == 401
        assert client.get("/api/v1/usage", headers=HEADERS).status_code == 401
        response = rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["serverInfo"]["name"] == "AI Usage Monitor"
        assert response.headers["cache-control"] == "no-store"
        tools = rpc(client, "tools/list").json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == {
            "get_usage_limits",
            "check_usage_budget",
            "refresh_usage",
        }
        data = call(client, "get_usage_limits", {"provider": "claude"})[
            "structuredContent"
        ]
        assert data["providers"]["claude"]["session"]["remaining_percent"] == 62.8
        assert data["providers"]["claude"]["usable_for_decisions"] is True
        assert "openai" not in data["providers"]


def test_budget_fails_closed_for_missing_failed_old_and_reset_data(app, sample_usage):
    repo = app.state.repository
    with TestClient(app) as client:
        args = {"provider": "claude"}
        assert (
            call(client, "check_usage_budget", args)["structuredContent"]["decision"]
            == "unknown"
        )
        repo.save_success("claude", sample_usage)
        assert (
            call(client, "check_usage_budget", args)["structuredContent"]["decision"]
            == "available"
        )
        assert (
            call(
                client, "check_usage_budget", {**args, "minimum_remaining_percent": 40}
            )["structuredContent"]["decision"]
            == "insufficient"
        )
        repo.save_failure("claude", "timeout")
        assert (
            call(client, "check_usage_budget", args)["structuredContent"]["decision"]
            == "unknown"
        )
        old = sample_usage.model_copy(deep=True)
        old.collected_at -= timedelta(minutes=6)
        repo.save_success("claude", old)
        assert (
            call(client, "check_usage_budget", args)["structuredContent"]["decision"]
            == "unknown"
        )
        expired = sample_usage.model_copy(deep=True)
        expired.session.reset_at = expired.collected_at - timedelta(seconds=1)
        repo.save_success("claude", expired)
        result = call(client, "check_usage_budget", args)["structuredContent"]
        assert result["decision"] == "unknown"
        assert result["providers"]["claude"]["decision_blockers"] == ["reset_elapsed"]


def test_mcp_rejects_bad_origin_host_large_body_and_arguments(app):
    with TestClient(app) as client:
        assert (
            rpc(
                client,
                "tools/list",
                headers={**HEADERS, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            rpc(
                client, "tools/list", headers={**HEADERS, "Host": "evil.example"}
            ).status_code
            == 421
        )
        response = client.post(
            "/mcp",
            headers={**HEADERS, "Content-Type": "application/json"},
            content=b" " * 65537,
        )
        assert response.status_code == 413
        for args in ({"provider": "other"}, {"max_age_seconds": 0}):
            assert call(client, "get_usage_limits", args)["isError"] is True


def test_refresh_throttle_shared_with_rest(app):
    with TestClient(app) as client:
        assert call(client, "refresh_usage")["structuredContent"]["all_providers_ok"]
        response = client.post(
            "/api/v1/usage/refresh",
            headers={"Authorization": "Bearer test-widget-token-1234567890"},
        )
        assert response.status_code == 429
        result = call(client, "refresh_usage")["structuredContent"]
        assert result["error"] == "manual_refresh_rate_limited"
        assert result["retry_after_seconds"] > 0


def test_mcp_storage_errors_sanitized(app):
    def broken(*args, **kwargs):
        raise OperationalError("private path", {}, Exception("DO-NOT-LEAK"))

    app.state.repository.get_usage_response = broken
    with TestClient(app) as client:
        result = call(client, "get_usage_limits")
        assert result["isError"]
        assert "storage_unavailable" in str(result)
        assert "DO-NOT-LEAK" not in str(result)
        assert "private path" not in str(result)


def test_mcp_requires_separate_credentials():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, widget_api_token=TOKEN, mcp_enabled=True)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, widget_api_token=TOKEN, mcp_api_token=TOKEN)


def test_mcp_disabled_by_default(settings):
    with TestClient(
        create_app(settings, providers=[], start_scheduler=False)
    ) as client:
        assert rpc(client, "tools/list").status_code == 404


@pytest.mark.asyncio
async def test_official_sdk_client_round_trip(app, sample_usage):
    app.state.repository.save_success("claude", sample_usage)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=http
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert len(listed.tools) == 3
                    result = await session.call_tool(
                        "get_usage_limits", {"provider": "claude"}
                    )
                    assert not result.is_error
                    assert (
                        result.structured_content["providers"]["claude"]["session"][
                            "remaining_percent"
                        ]
                        == 62.8
                    )


def test_readiness_distinguishes_fresh_collection_from_reset(app, sample_usage):
    sample_usage.session.reset_at = sample_usage.collected_at - timedelta(seconds=1)
    app.state.repository.save_success("claude", sample_usage)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test-widget-token-1234567890"}
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code == 401
        ready = client.get("/api/v1/ready", headers=headers)
        assert ready.status_code == 503
        assert ready.json()["providers"]["claude"]["stale_reasons"] == ["reset_elapsed"]
        compact = client.get("/api/v1/usage/compact", headers=headers).json()
        assert compact["claude"]["stale_reasons"] == ["reset_elapsed"]
        assert compact["claude"]["last_attempt_at"] is not None
