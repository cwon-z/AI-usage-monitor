from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_usage_monitor.models.schemas import QuotaWindow
from ai_usage_monitor.services.normalizer import (
    calculate_pace,
    format_reset_in,
    parse_timestamp,
)


def test_pace_over_budget_and_projected_exhaustion():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    window = QuotaWindow.from_used(
        71,
        reset_at=now + timedelta(hours=2, minutes=36),
        window_minutes=300,
    )
    pace = calculate_pace(window, now=now)
    assert pace is not None
    assert pace.time_elapsed_percent == 48
    assert pace.pace_delta == 23
    assert pace.pace == "over_budget"
    assert pace.projected_exhaustion_before_reset is True


def test_pace_under_budget():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    window = QuotaWindow.from_used(
        10, reset_at=now + timedelta(hours=2), window_minutes=300
    )
    pace = calculate_pace(window, now=now)
    assert pace and pace.pace == "under_budget"
    assert pace.projected_exhaustion_before_reset is False


def test_pace_unavailable_without_duration():
    window = QuotaWindow.from_used(10, reset_at=datetime.now(UTC), window_minutes=None)
    assert calculate_pace(window) is None


def test_timestamp_handling():
    parsed = parse_timestamp("2026-09-10T02:43:00+09:00")
    assert parsed == datetime(2026, 9, 9, 17, 43, tzinfo=UTC)
    assert parse_timestamp(0) == datetime(1970, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone"):
        parse_timestamp("2026-09-10T02:43:00")
    with pytest.raises(ValueError, match="invalid timestamp"):
        parse_timestamp("not-a-time")


def test_reset_duration_format():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    assert format_reset_in(now + timedelta(days=3, hours=7), now=now) == "3d 7h"
    assert format_reset_in(now + timedelta(hours=2, minutes=11), now=now) == "2h 11m"
