from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.main import create_app
from ai_usage_monitor.models.database import create_database
from ai_usage_monitor.providers.base import JSONLCommandClient, ProviderError
from ai_usage_monitor.providers.claude import ClaudeProvider
from ai_usage_monitor.providers.credentials import (
    claude_credentials,
    codex_credentials,
    read_secret,
)
from ai_usage_monitor.providers.openai_codex import OpenAICodexProvider
from ai_usage_monitor.provision import atomic_secret
from ai_usage_monitor.provision import main as provision_main
from ai_usage_monitor.services.collector import Collector
from ai_usage_monitor.services.history import HistoryRepository


def fake_codex(exp=None):
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"exp": exp if exp is not None else time.time() + 3600}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return {
        "tokens": {
            "access_token": f"header.{payload}.signature",
            "id_token": "fake-id",
            "refresh_token": "DO-NOT-PASS-REFRESH",
            "account_id": "fake-account",
        }
    }


def fake_claude():
    return {
        "claudeAiOauth": {
            "accessToken": "fake-access",
            "refreshToken": "DO-NOT-PASS-REFRESH",
            "expiresAt": (time.time() + 3600) * 1000,
            "scopes": ["user:profile", "user:inference"],
            "subscriptionType": "max",
        }
    }


def test_export_filters_refresh_tokens_and_rejects_expiration():
    assert codex_credentials(fake_codex())["refresh_token"] == ""
    assert "refreshToken" not in claude_credentials(fake_claude())
    with pytest.raises(ProviderError, match="authentication_expired"):
        codex_credentials(fake_codex(time.time() - 1))
    source = fake_claude()
    source["claudeAiOauth"]["expiresAt"] = 0
    with pytest.raises(ProviderError, match="authentication_expired"):
        claude_credentials(source)


def test_setup_token_scope_rejected():
    source = fake_claude()
    source["claudeAiOauth"]["scopes"] = ["user:inference"]
    with pytest.raises(ProviderError, match="authentication_scope_missing"):
        claude_credentials(source)


def test_secret_files_bounded_and_regular(tmp_path):
    file = tmp_path / "big"
    file.write_text("x" * 100)
    with pytest.raises(ProviderError):
        read_secret(file, max_bytes=20)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ProviderError):
        read_secret(fifo)


def test_widget_secret_file_and_config_validation(tmp_path):
    file = tmp_path / "widget"
    file.write_text("a-long-fake-widget-token\n")
    config = Settings(_env_file=None, widget_api_token_file=file)
    assert config.widget_api_token.get_secret_value() == "a-long-fake-widget-token"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, widget_api_token="a-long token-with-spaces")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            widget_api_token="a-long-fake-token",
            database_url="sqlite:///db?mode=ro",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication_expired", False),
        (403, "authentication_scope_missing", False),
        (429, "upstream_error", True),
        (500, "upstream_error", True),
        (302, "upstream_error", False),
    ],
)
async def test_claude_http_errors_are_sanitized(
    settings, tmp_path, status, code, retryable
):
    source = tmp_path / "claude"
    source.write_text(json.dumps(fake_claude()))
    settings.claude_credentials_file = source
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.anthropic.com/api/oauth/usage"
        assert request.headers["authorization"] == "Bearer fake-access"
        assert "DO-NOT-PASS-REFRESH" not in str(request.headers)
        return httpx.Response(
            status,
            json={"error": "SECRET-ERROR-BODY"},
            headers={"location": "https://example.com"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError) as caught:
            await ClaudeProvider(settings, client).get_usage()
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert "SECRET" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        "bad",
        {"five_hour": {"utilization": True}},
        {"seven_day": {"utilization": float("inf")}},
        {"model_scoped": [{"utilization": 1}]},
    ],
)
async def test_claude_malformed_response(settings, tmp_path, payload):
    source = tmp_path / "claude"
    source.write_text(json.dumps(fake_claude()))
    settings.claude_credentials_file = source
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload))
        )
    ) as client:
        with pytest.raises(ProviderError, match="malformed_response"):
            await ClaudeProvider(settings, client).get_usage()


@pytest.mark.asyncio
async def test_claude_success_uses_no_cli_and_does_not_write_source(settings, tmp_path):
    source = tmp_path / "claude"
    original = json.dumps(fake_claude())
    source.write_text(original)
    settings.claude_credentials_file = source
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"five_hour": {"utilization": 25, "resets_at": None}}
            )
        )
    ) as client:
        result = await ClaudeProvider(settings, client).get_usage()
    assert result.session.used_percent == 25
    assert result.plan == "max"
    assert source.read_text() == original


@pytest.mark.parametrize(
    "credits",
    [
        {"balance": {"secret": "body"}},
        {"balance": 2},
        {"hasCredits": "false"},
        {"unlimited": 1},
        {"balance": "NaN"},
    ],
)
def test_codex_rejects_malformed_credits(settings, credits):
    with pytest.raises(ProviderError, match="malformed_response"):
        OpenAICodexProvider(settings).normalize_payload(
            {"rateLimits": {"credits": credits}}
        )


@pytest.mark.asyncio
async def test_transport_rejects_oversized_line():
    with pytest.raises(ProviderError, match="malformed_response"):
        await JSONLCommandClient(max_line_bytes=100).request(
            [sys.executable, "-c", 'print("x"*500)'],
            messages=[],
            matcher=lambda value: True,
            timeout=1,
        )


@pytest.mark.asyncio
async def test_transport_deadline_includes_blocked_stdin():
    started = time.monotonic()
    with pytest.raises(ProviderError, match="timeout"):
        await JSONLCommandClient().request(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            messages=[{"large": "x" * 2_000_000}],
            matcher=lambda value: True,
            timeout=0.05,
        )
    assert time.monotonic() - started < 3


@pytest.mark.asyncio
async def test_storage_failure_does_not_retry_provider_and_peer_is_saved(
    settings, sample_usage
):
    engine, factory = create_database(settings.database_url)
    repo = HistoryRepository(factory)
    save = repo.save_success

    def broken(name, usage):
        if name == "claude":
            raise OSError("private database path")
        return save(name, usage)

    repo.save_success = broken

    class Provider:
        def __init__(self, name):
            self.name, self.calls = name, 0

        async def get_usage(self):
            self.calls += 1
            return sample_usage

    first, second = Provider("claude"), Provider("openai")
    collector = Collector([first, second], repo, settings)
    try:
        with pytest.raises(RuntimeError, match="storage_unavailable"):
            await collector.collect_all()
        assert first.calls == second.calls == 1
        assert (
            repo.get_usage_response(["openai"], stale_after_seconds=1800)
            .providers["openai"]
            .status
            == "ok"
        )
    finally:
        await collector.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_simultaneous_refreshes_coalesce(settings, sample_usage):
    engine, factory = create_database(settings.database_url)

    class Provider:
        name = "claude"
        calls = 0

        async def get_usage(self):
            self.calls += 1
            await asyncio.sleep(0.02)
            return sample_usage

    provider = Provider()
    collector = Collector([provider], HistoryRepository(factory), settings)
    try:
        await asyncio.gather(*(collector.collect_all() for _ in range(5)))
        assert provider.calls == 1
    finally:
        await collector.close()
        engine.dispose()


def test_history_cursor_pages_without_duplicates(settings, sample_usage):
    app = create_app(settings, providers=[], start_scheduler=False)
    for _ in range(5):
        app.state.repository.save_success("claude", sample_usage)
    ids = []
    cursor = None
    with TestClient(app) as client:
        while True:
            params = {"limit": 3}
            if cursor is not None:
                params["before_id"] = cursor
            response = client.get(
                "/api/v1/usage/history",
                params=params,
                headers={"Authorization": "Bearer test-widget-token-1234567890"},
            )
            body = response.json()
            ids.extend(item["id"] for item in body["items"])
            cursor = body["next_before_id"]
            if cursor is None:
                break
    assert len(ids) == len(set(ids)) == 10


def test_database_error_response_is_sanitized(settings):
    app = create_app(settings, providers=[], start_scheduler=False)

    def broken(*args, **kwargs):
        raise OperationalError(
            "secret path or query", {}, Exception("secret driver error")
        )

    app.state.repository.get_usage_response = broken
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/usage",
            headers={"Authorization": "Bearer test-widget-token-1234567890"},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "storage_unavailable"}
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/openapi.json").status_code == 404


def test_pace_is_sampled_not_recomputed_as_data_ages(settings, sample_usage):
    engine, factory = create_database(settings.database_url)
    repo = HistoryRepository(factory)
    try:
        repo.save_success("claude", sample_usage)
        first = repo.get_usage_response(
            ["claude"], stale_after_seconds=1800, now=sample_usage.collected_at
        )
        later = repo.get_usage_response(
            ["claude"],
            stale_after_seconds=1800,
            now=sample_usage.collected_at + timedelta(minutes=10),
        )
        assert (
            first.providers["claude"].session.pace
            == later.providers["claude"].session.pace
        )
    finally:
        engine.dispose()


def test_export_command_preserves_source_and_widget(tmp_path, monkeypatch):
    claude, codex = tmp_path / "claude-source", tmp_path / "codex-source"
    claude.write_text(json.dumps(fake_claude()))
    codex.write_text(json.dumps(fake_codex()))
    before = (claude.read_bytes(), codex.read_bytes())
    directory = tmp_path / "exports"
    args = [
        "export",
        "--output-dir",
        str(directory),
        "--claude-credentials",
        str(claude),
        "--codex-auth",
        str(codex),
    ]
    monkeypatch.setattr(sys, "argv", args)
    provision_main()
    widget = (directory / "widget-api-token").read_bytes()
    assert len(widget) >= 32
    assert (directory.stat().st_mode & 0o777) == 0o700
    for file in directory.iterdir():
        assert file.stat().st_mode & 0o777 == 0o600
        assert b"DO-NOT-PASS-REFRESH" not in file.read_bytes()
    monkeypatch.setattr(sys, "argv", args + ["--replace-provider-snapshots"])
    provision_main()
    assert (directory / "widget-api-token").read_bytes() == widget
    assert before == (claude.read_bytes(), codex.read_bytes())


def test_export_does_not_follow_symlinks(tmp_path):
    source = tmp_path / "source"
    source.write_text("preserve")
    target = tmp_path / "output"
    target.symlink_to(source)
    with pytest.raises(ValueError):
        atomic_secret(target, "new", replace=True)
    assert source.read_text() == "preserve"
