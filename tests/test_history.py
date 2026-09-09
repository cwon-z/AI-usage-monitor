from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_usage_monitor.models.database import create_database
from ai_usage_monitor.services.history import HistoryRepository


def make_repository(settings):
    _engine, factory = create_database(settings.database_url)
    return HistoryRepository(factory)


def test_history_storage_and_filters(settings, sample_usage):
    repository = make_repository(settings)
    repository.save_success("claude", sample_usage)
    items = repository.list_history(provider="claude", quota_type="session")
    assert len(items) == 1
    assert items[0].used_percent == 37.2
    assert items[0].scope == "five_hour"


def test_failed_provider_keeps_last_success_and_marks_stale(settings, sample_usage):
    repository = make_repository(settings)
    repository.save_success("claude", sample_usage)
    repository.save_failure(
        "claude",
        "timeout",
        attempted_at=sample_usage.collected_at + timedelta(minutes=1),
    )
    response = repository.get_usage_response(
        ["claude"],
        stale_after_seconds=1_800,
        now=sample_usage.collected_at + timedelta(minutes=2),
    )
    result = response.providers["claude"]
    assert result.status == "error"
    assert result.error == "timeout"
    assert result.stale is True
    assert result.session and result.session.used_percent == 37.2


def test_age_marks_data_stale(settings, sample_usage):
    repository = make_repository(settings)
    repository.save_success("claude", sample_usage)
    response = repository.get_usage_response(
        ["claude"],
        stale_after_seconds=1_800,
        now=sample_usage.collected_at + timedelta(hours=1),
    )
    assert response.providers["claude"].status == "ok"
    assert response.providers["claude"].stale is True


def test_pruning(settings, sample_usage):
    repository = make_repository(settings)
    old = sample_usage.model_copy(
        update={"collected_at": datetime.now(UTC) - timedelta(days=40)}
    )
    repository.save_success("claude", old)
    assert repository.prune(30) == 2
    assert repository.list_history() == []
