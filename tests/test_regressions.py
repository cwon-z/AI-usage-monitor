import asyncio
import base64
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.main import create_app
from ai_usage_monitor.models.database import create_database
from ai_usage_monitor.models.schemas import ProviderUsage, QuotaWindow
from ai_usage_monitor.providers.base import JSONLCommandClient
from ai_usage_monitor.providers.claude import ClaudeProvider
from ai_usage_monitor.providers.openai_codex import OpenAICodexProvider
from ai_usage_monitor.services.collector import CollectionScheduler
from ai_usage_monitor.services.history import HistoryRepository


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        widget_api_token="audit-fake-widget-token",
        database_url=f"sqlite:///{tmp_path / 'audit.db'}",
        claude_enabled=False,
        codex_enabled=False,
    )


def test_convenience_windows_do_not_mix_accounts_or_model_buckets(settings):
    usage = OpenAICodexProvider(settings).normalize_payload(
        {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 57, "windowDurationMins": 10080},
            },
            "rateLimitsByLimitId": {
                "spark": {"primary": {"usedPercent": 18, "windowDurationMins": 300}}
            },
        }
    )
    assert usage.five_hour is None, (
        "Canonical Codex has no five-hour quota; do not substitute Spark"
    )


def test_consumed_credits_are_not_remaining_balance(settings):
    usage = ClaudeProvider(settings).normalize_payload(
        {
            "rate_limits_available": True,
            "rate_limits": {"extra_usage": {"is_enabled": True, "used_credits": 4.25}},
        }
    )
    assert usage.credits is None or usage.credits.balance is None


@pytest.mark.asyncio
async def test_scheduler_recovers_from_transient_storage_failure():
    class BrokenOnce:
        calls = 0

        async def collect_all(self):
            self.calls += 1
            if self.calls == 1:
                raise OSError("synthetic database unavailable")
            return {}

    collector = BrokenOnce()
    scheduler = CollectionScheduler(collector, 0.01)
    scheduler.start()
    await asyncio.sleep(0.06)
    running, calls = scheduler.running, collector.calls
    try:
        await scheduler.stop()
    except OSError:
        pass
    assert running and calls >= 2, f"scheduler dead after {calls} cycle"


def test_health_failure_uses_error_status_for_docker(settings):
    app = create_app(settings, providers=[], start_scheduler=False)
    app.state.repository.ping = lambda: False
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 503, response.json()


def test_disabling_provider_does_not_make_service_permanently_stale(settings):
    app = create_app(settings, providers=[], start_scheduler=False)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/usage", headers={"Authorization": "Bearer audit-fake-widget-token"}
        )
        assert response.status_code == 200
        assert response.json()["stale"] is False


def test_expired_quota_is_stale_even_when_sample_is_recent(settings):
    now = datetime.now(UTC)
    engine, factory = create_database(settings.database_url)
    repo = HistoryRepository(factory)
    try:
        repo.save_success(
            "openai",
            ProviderUsage(
                collected_at=now - timedelta(minutes=5),
                five_hour=QuotaWindow.from_used(
                    99, reset_at=now - timedelta(minutes=1), window_minutes=300
                ),
            ),
        )
        result = repo.get_usage_response(["openai"], stale_after_seconds=1800, now=now)
        assert result.providers["openai"].stale is True
    finally:
        engine.dispose()


def test_failed_poll_does_not_change_data_timestamp(settings):
    now = datetime.now(UTC)
    engine, factory = create_database(settings.database_url)
    repo = HistoryRepository(factory)
    try:
        collected = now - timedelta(days=1)
        repo.save_success("openai", ProviderUsage(collected_at=collected))
        repo.save_failure("openai", "authentication_expired", attempted_at=now)
        result = repo.get_usage_response(["openai"], stale_after_seconds=1800, now=now)
        assert result.updated_at == collected
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_transport_accepts_line_below_declared_one_megabyte_limit():
    result = await JSONLCommandClient().request(
        [sys.executable, "-c", 'import json; print(json.dumps({"value":"x"*100000}))'],
        messages=[],
        matcher=lambda value: "value" in value,
        timeout=2,
    )
    assert len(result["value"]) == 100000


@pytest.mark.asyncio
async def test_transport_drains_stderr_continuously():
    result = await JSONLCommandClient().request(
        [
            sys.executable,
            "-c",
            'import sys,json; sys.stderr.write("x"*2000000); sys.stderr.flush(); print(json.dumps({"ok":True}))',
        ],
        messages=[],
        matcher=lambda value: value.get("ok"),
        timeout=0.5,
    )
    assert result["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_override", [True, False])
async def test_codex_child_does_not_receive_refresh_token_or_foreign_override(
    settings, tmp_path, monkeypatch, foreign_override
):
    path = tmp_path / "fake-auth.json"
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "header."
                    + base64.urlsafe_b64encode(
                        json.dumps({"exp": time.time() + 3600}).encode()
                    )
                    .decode()
                    .rstrip("=")
                    + ".signature",
                    "account_id": "fake-account",
                    "refresh_token": "FAKE_REFRESH",
                    "id_token": "FAKE_ID",
                },
                "last_refresh": "2020-01-01T00:00:00Z",
            }
        )
    )
    settings.codex_auth_file = path
    if foreign_override:
        monkeypatch.setenv("CODEX_ACCESS_TOKEN", "FAKE_OTHER_ACCOUNT")
    else:
        monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    observed = {}

    class Capture:
        async def request(self, command, **kwargs):
            observed["env"] = kwargs["env"]
            observed["auth"] = json.loads(
                (Path(kwargs["env"]["CODEX_HOME"]) / "auth.json").read_text()
            )
            return {"id": 2, "result": {"rateLimits": {}}}

    await OpenAICodexProvider(settings, Capture()).get_usage()
    assert "CODEX_ACCESS_TOKEN" not in observed["env"], (
        "A foreign auth override reaches the child"
    )
    assert not observed["auth"]["tokens"].get("refresh_token"), (
        "Temporary copies can still rotate live refresh tokens"
    )


def test_sqlite_memory_url_works_across_api_thread(settings):
    settings.database_url = "sqlite:///:memory:"
    app = create_app(settings, providers=[], start_scheduler=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/v1/usage", headers={"Authorization": "Bearer audit-fake-widget-token"}
        )
        assert response.status_code == 200


def test_compact_percentages_report_remaining_not_spent(settings, sample_usage):
    settings.claude_enabled = True
    app = create_app(settings, providers=[], start_scheduler=False)
    app.state.repository.save_success("claude", sample_usage)
    headers = {"Authorization": "Bearer audit-fake-widget-token"}
    with TestClient(app) as client:
        full = client.get("/api/v1/usage", headers=headers).json()
        compact = client.get("/api/v1/usage/compact", headers=headers).json()

    weekly = full["providers"]["claude"]["weekly"]
    assert compact["claude"]["weekly"] == round(weekly["remaining_percent"]), (
        "Compact percentages must be remaining quota, not spent"
    )
    assert compact["claude"]["weekly"] != round(weekly["used_percent"]), (
        "Compact weekly reverted to spent quota"
    )
