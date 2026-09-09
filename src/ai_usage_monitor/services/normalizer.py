from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_usage_monitor.models.schemas import PaceInfo, ProviderUsage, QuotaWindow


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("invalid timestamp")
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("invalid timestamp") from exc
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    return ensure_aware(parsed)


def calculate_pace(
    window: QuotaWindow,
    *,
    now: datetime | None = None,
    tolerance_percent: float = 5.0,
) -> PaceInfo | None:
    if window.reset_at is None or window.window_minutes is None:
        return None
    current = ensure_aware(now or utc_now())
    reset = ensure_aware(window.reset_at)
    duration_seconds = window.window_minutes * 60
    start = reset.timestamp() - duration_seconds
    elapsed_seconds = current.timestamp() - start
    if elapsed_seconds < 0 or current >= reset:
        return None
    elapsed_fraction = min(1.0, elapsed_seconds / duration_seconds)
    elapsed_percent = elapsed_fraction * 100
    delta = window.used_percent - elapsed_percent
    if delta > tolerance_percent:
        pace = "over_budget"
    elif delta < -tolerance_percent:
        pace = "under_budget"
    else:
        pace = "on_pace"

    if window.used_percent >= 100:
        projected = True
    elif elapsed_fraction <= 0 or window.used_percent <= 0:
        projected = False
    else:
        projected = (elapsed_fraction / (window.used_percent / 100)) < 1

    return PaceInfo(
        time_elapsed_percent=round(elapsed_percent, 2),
        pace_delta=round(delta, 2),
        pace=pace,
        projected_exhaustion_before_reset=projected,
    )


def add_pacing(usage: ProviderUsage, *, now: datetime | None = None) -> ProviderUsage:
    def enriched(window: QuotaWindow | None) -> QuotaWindow | None:
        if window is None:
            return None
        return window.model_copy(update={"pace": calculate_pace(window, now=now)})

    return usage.model_copy(
        update={
            "session": enriched(usage.session),
            "five_hour": enriched(usage.five_hour),
            "weekly": enriched(usage.weekly),
            "additional_windows": {
                key: enriched(window)
                for key, window in usage.additional_windows.items()
            },
        }
    )


def format_reset_in(
    reset_at: datetime | None, *, now: datetime | None = None
) -> str | None:
    if reset_at is None:
        return None
    seconds = max(
        0,
        int((ensure_aware(reset_at) - ensure_aware(now or utc_now())).total_seconds()),
    )
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3_600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
