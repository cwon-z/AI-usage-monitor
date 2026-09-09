from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from ai_usage_monitor.api.auth import require_api_token
from ai_usage_monitor.models.schemas import (
    CompactProvider,
    CompactUsageResponse,
    HistoryResponse,
    ProvidersResponse,
    UsageResponse,
)
from ai_usage_monitor.services.logging import log_event
from ai_usage_monitor.services.normalizer import format_reset_in, utc_now

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_token)])
logger = logging.getLogger(__name__)


def _full_usage(request: Request) -> UsageResponse:
    return request.app.state.repository.get_usage_response(
        request.app.state.provider_names,
        stale_after_seconds=request.app.state.settings.stale_after_seconds,
        disabled_providers=request.app.state.disabled_providers,
    )


@router.get("/usage", response_model=UsageResponse)
def get_usage(request: Request) -> UsageResponse:
    return _full_usage(request)


def _compact_provider(result, *, claude: bool, now: datetime) -> CompactProvider:  # type: ignore[no-untyped-def]
    return CompactProvider(
        status=result.status,
        stale=result.stale,
        error=result.error,
        collected_at=result.collected_at,
        session=round(result.session.remaining_percent)
        if claude and result.session
        else None,
        five_hour=round(result.five_hour.remaining_percent)
        if not claude and result.five_hour
        else None,
        weekly=round(result.weekly.remaining_percent) if result.weekly else None,
        session_reset_in=format_reset_in(result.session.reset_at, now=now)
        if claude and result.session
        else None,
        five_hour_reset_in=format_reset_in(result.five_hour.reset_at, now=now)
        if not claude and result.five_hour
        else None,
        weekly_reset_in=format_reset_in(result.weekly.reset_at, now=now)
        if result.weekly
        else None,
    )


@router.get("/usage/compact", response_model=CompactUsageResponse)
def get_compact_usage(request: Request) -> CompactUsageResponse:
    full = _full_usage(request)
    current = utc_now()
    return CompactUsageResponse(
        claude=_compact_provider(full.providers["claude"], claude=True, now=current),
        openai=_compact_provider(full.providers["openai"], claude=False, now=current),
        updated_at=full.updated_at,
        stale=full.stale,
    )


@router.get("/usage/history", response_model=HistoryResponse)
def get_history(
    request: Request,
    provider: str | None = Query(default=None, max_length=40),
    quota_type: str | None = Query(default=None, max_length=120),
    since: datetime | None = None,
    limit: int = Query(default=500, ge=1, le=1_000),
    before_id: int | None = Query(default=None, ge=1),
) -> HistoryResponse:
    if since is not None and (since.tzinfo is None or since.utcoffset() is None):
        raise HTTPException(status_code=422, detail="since must include a timezone")
    items = request.app.state.repository.list_history(
        provider=provider,
        quota_type=quota_type,
        since=since,
        limit=limit + 1,
        before_id=before_id,
    )
    return HistoryResponse(
        items=items[:limit],
        next_before_id=items[limit - 1].id if len(items) > limit else None,
    )


@router.get("/providers", response_model=ProvidersResponse)
def get_providers(request: Request) -> ProvidersResponse:
    usage = _full_usage(request)
    descriptors = []
    for descriptor in request.app.state.provider_descriptors:
        result = usage.providers.get(descriptor.name)
        authenticated = result is not None and (
            result.status == "ok"
            or result.session is not None
            or result.five_hour is not None
            or result.weekly is not None
        )
        descriptors.append(
            descriptor.model_copy(
                update={"configured": descriptor.configured or authenticated}
            )
        )
    return ProvidersResponse(providers=descriptors)


@router.post("/usage/refresh", response_model=UsageResponse)
async def refresh_usage(request: Request, response: Response) -> UsageResponse:
    retry_after = await request.app.state.refresh_limiter.claim()
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="manual_refresh_rate_limited",
            headers={"Retry-After": str(retry_after)},
        )
    log_event(logger, "manual_refresh")
    try:
        outcomes = await request.app.state.collector.collect_all()
    except Exception:  # noqa: BLE001 — never expose storage/driver details
        log_event(logger, "collection_cycle_failed", error_code="storage_unavailable")
        raise HTTPException(status_code=503, detail="storage_unavailable") from None
    response.status_code = 200 if all(outcomes.values()) else 207
    return await asyncio.to_thread(_full_usage, request)
