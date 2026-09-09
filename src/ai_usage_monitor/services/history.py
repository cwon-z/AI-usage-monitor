from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import wraps
from threading import RLock

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from ai_usage_monitor.models.database import ProviderStateRecord, SnapshotRecord
from ai_usage_monitor.models.schemas import (
    HistoryItem,
    ProviderCapabilities,
    ProviderResult,
    ProviderUsage,
    UsageResponse,
)
from ai_usage_monitor.services.normalizer import add_pacing, ensure_aware, utc_now


def _db_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._db_lock:
            return method(self, *args, **kwargs)

    return call


class HistoryRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self._db_lock = RLock()

    @serialized
    def ping(self) -> bool:
        try:
            with self.session_factory() as session:
                session.execute(text("SELECT 1"))
            return True
        # Health checks deliberately collapse all driver failures to a boolean.
        except Exception:  # noqa: BLE001
            return False

    @serialized
    def save_success(self, provider: str, usage: ProviderUsage) -> None:
        collected_at = ensure_aware(usage.collected_at)
        with self.session_factory.begin() as session:
            record = session.get(ProviderStateRecord, provider)
            if record is None:
                record = ProviderStateRecord(
                    provider=provider,
                    payload=None,
                    successful_at=None,
                    last_attempt_at=collected_at,
                    last_error=None,
                )
                session.add(record)
            record.payload = usage.model_dump_json()
            record.successful_at = collected_at
            record.last_attempt_at = collected_at
            record.last_error = None

            windows = {
                "session": usage.session,
                "five_hour": usage.five_hour,
                "weekly": usage.weekly,
                **{
                    f"additional:{key}": value
                    for key, value in usage.additional_windows.items()
                },
            }
            for quota_type, window in windows.items():
                if window is None:
                    continue
                session.add(
                    SnapshotRecord(
                        provider=provider,
                        quota_type=quota_type,
                        scope=window.source,
                        used_percent=window.used_percent,
                        reset_at=window.reset_at,
                        collected_at=collected_at,
                        window_minutes=window.window_minutes,
                    )
                )

    @serialized
    def save_failure(
        self, provider: str, error_code: str, *, attempted_at: datetime | None = None
    ) -> None:
        attempted = ensure_aware(attempted_at or utc_now())
        with self.session_factory.begin() as session:
            record = session.get(ProviderStateRecord, provider)
            if record is None:
                record = ProviderStateRecord(
                    provider=provider,
                    payload=None,
                    successful_at=None,
                    last_attempt_at=attempted,
                    last_error=error_code,
                )
                session.add(record)
            else:
                record.last_attempt_at = attempted
                record.last_error = error_code

    @serialized
    def get_usage_response(
        self,
        provider_names: list[str],
        *,
        stale_after_seconds: int,
        now: datetime | None = None,
        disabled_providers: set[str] | None = None,
    ) -> UsageResponse:
        current = ensure_aware(now or utc_now())
        providers: dict[str, ProviderResult] = {}
        update_times: list[datetime] = []
        with self.session_factory() as session:
            records = {
                record.provider: record
                for record in session.scalars(
                    select(ProviderStateRecord).where(
                        ProviderStateRecord.provider.in_(provider_names)
                    )
                )
            }

        for provider in provider_names:
            if provider in (disabled_providers or set()):
                providers[provider] = ProviderResult(
                    status="unavailable", stale=False, error="disabled"
                )
                continue
            record = records.get(provider)
            if record is None:
                providers[provider] = ProviderResult(
                    status="unavailable",
                    stale=True,
                    error="not_collected",
                    capabilities=ProviderCapabilities(),
                    collected_at=None,
                )
                continue

            attempted_at = _db_datetime(record.last_attempt_at) or current
            if record.payload is None:
                providers[provider] = ProviderResult(
                    status="error" if record.last_error else "unavailable",
                    stale=True,
                    error=record.last_error or "not_collected",
                    capabilities=ProviderCapabilities(),
                    collected_at=None,
                    last_attempt_at=attempted_at,
                )
                continue

            try:
                usage = ProviderUsage.model_validate_json(record.payload)
            except ValueError:
                providers[provider] = ProviderResult(
                    status="error",
                    stale=True,
                    error="cached_data_invalid",
                    capabilities=ProviderCapabilities(),
                    collected_at=None,
                    last_attempt_at=attempted_at,
                )
                continue
            successful_at = _db_datetime(record.successful_at) or ensure_aware(
                usage.collected_at
            )
            age = (current - successful_at).total_seconds()
            update_times.append(successful_at)
            windows = [
                usage.session,
                usage.five_hour,
                usage.weekly,
                *usage.additional_windows.values(),
            ]
            expired = any(
                window is not None
                and window.reset_at is not None
                and window.reset_at <= current
                for window in windows
            )
            stale = bool(record.last_error) or age >= stale_after_seconds or expired
            paced = add_pacing(usage, now=usage.collected_at)
            if stale:
                for window in [
                    paced.session,
                    paced.five_hour,
                    paced.weekly,
                    *paced.additional_windows.values(),
                ]:
                    if window is not None:
                        window.pace = None
            providers[provider] = ProviderResult(
                **paced.model_dump(),
                status="error" if record.last_error else "ok",
                stale=stale,
                error=record.last_error,
                last_attempt_at=attempted_at,
            )

        updated_at = max(update_times) if update_times else None
        return UsageResponse(
            providers=providers,
            updated_at=updated_at,
            stale=any(provider.stale for provider in providers.values()),
        )

    @serialized
    def list_history(
        self,
        *,
        provider: str | None = None,
        quota_type: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
        before_id: int | None = None,
    ) -> list[HistoryItem]:
        statement = select(SnapshotRecord)
        if provider:
            statement = statement.where(SnapshotRecord.provider == provider)
        if quota_type:
            statement = statement.where(SnapshotRecord.quota_type == quota_type)
        if since:
            statement = statement.where(
                SnapshotRecord.collected_at >= ensure_aware(since)
            )
        if before_id is not None:
            statement = statement.where(SnapshotRecord.id < before_id)
        statement = statement.order_by(SnapshotRecord.id.desc()).limit(limit)
        with self.session_factory() as session:
            rows = list(session.scalars(statement))
        return [
            HistoryItem(
                id=row.id,
                provider=row.provider,
                quota_type=row.quota_type,
                scope=row.scope,
                used_percent=row.used_percent,
                reset_at=_db_datetime(row.reset_at),
                collected_at=_db_datetime(row.collected_at) or utc_now(),
                window_minutes=row.window_minutes,
            )
            for row in rows
        ]

    @serialized
    def prune(self, retention_days: int, *, now: datetime | None = None) -> int:
        cutoff = ensure_aware(now or utc_now()) - timedelta(days=retention_days)
        with self.session_factory.begin() as session:
            result = session.execute(
                delete(SnapshotRecord).where(SnapshotRecord.collected_at < cutoff)
            )
            return int(result.rowcount or 0)
