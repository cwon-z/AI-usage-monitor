from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.models.schemas import (
    ProviderCapabilities,
    ProviderUsage,
    QuotaWindow,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        widget_api_token="test-widget-token-1234567890",
        database_url=f"sqlite:///{tmp_path / 'usage.db'}",
        collection_interval_seconds=600,
        stale_after_seconds=1_800,
        manual_refresh_min_interval_seconds=60,
        provider_timeout_seconds=2,
        provider_retry_attempts=2,
        provider_retry_base_seconds=0,
        claude_enabled=True,
        codex_enabled=True,
        codex_auth_file=tmp_path / "missing-auth.json",
    )


@pytest.fixture
def sample_usage():
    now = datetime.now(UTC)
    return ProviderUsage(
        session=QuotaWindow.from_used(
            37.2,
            reset_at=now + timedelta(hours=2, minutes=11),
            window_minutes=300,
            source="five_hour",
        ),
        weekly=QuotaWindow.from_used(
            61,
            reset_at=now + timedelta(days=3, hours=7),
            window_minutes=10_080,
            source="seven_day",
        ),
        capabilities=ProviderCapabilities(session=True, five_hour=True, weekly=True),
        plan="pro",
        collected_at=now,
    )
