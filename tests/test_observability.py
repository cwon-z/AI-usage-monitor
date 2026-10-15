import logging

import pytest

from ai_usage_monitor.models.database import create_database
from ai_usage_monitor.providers.base import ProviderError
from ai_usage_monitor.services.collector import Collector
from ai_usage_monitor.services.history import HistoryRepository
from ai_usage_monitor.services.logging import JsonFormatter


@pytest.mark.asyncio
async def test_collection_logs_correlate_retry_and_recovery(
    settings, sample_usage, caplog
):
    class Provider:
        name = "claude"
        calls = 0

        async def get_usage(self):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("timeout", retryable=True)
            return sample_usage

    engine, factory = create_database(settings.database_url)
    collector = Collector([Provider()], HistoryRepository(factory), settings)
    try:
        with caplog.at_level(logging.INFO):
            assert await collector.collect_all() == {"claude": True}
        records = [r for r in caplog.records if hasattr(r, "event_fields")]
        assert len({r.event_fields["cycle_id"] for r in records}) == 1
        retry = next(r for r in records if r.msg == "provider_retry")
        assert retry.levelno == logging.WARNING
        success = next(r for r in records if r.msg == "provider_collection_success")
        assert success.event_fields["attempts"] == 2
        assert success.event_fields["duration_ms"] >= 0
        assert records[-1].event_fields["successful_providers"] == 1
    finally:
        await collector.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_unexpected_provider_error_never_enters_logs(settings, caplog):
    class Provider:
        name = "claude"

        async def get_usage(self):
            raise RuntimeError("private-token-DO-NOT-LOG")

    engine, factory = create_database(settings.database_url)
    collector = Collector([Provider()], HistoryRepository(factory), settings)
    try:
        with caplog.at_level(logging.INFO):
            assert await collector.collect_all() == {"claude": False}
        rendered = "\n".join(JsonFormatter().format(r) for r in caplog.records)
        assert "private-token" not in rendered
        assert '"level":"warning"' in rendered
        assert '"error_code":"provider_failed"' in rendered
    finally:
        await collector.close()
        engine.dispose()
