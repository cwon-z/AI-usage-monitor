"""Shared application operations for HTTP and MCP transports."""

from __future__ import annotations

import asyncio
import logging

from ai_usage_monitor.services.logging import log_event


class RefreshThrottled(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class StorageUnavailable(Exception):
    pass


class UsageService:
    def __init__(self, state):
        self.state = state

    def read(self):
        return self.state.repository.get_usage_response(
            self.state.provider_names,
            stale_after_seconds=self.state.settings.stale_after_seconds,
            disabled_providers=self.state.disabled_providers,
        )

    async def refresh(self):
        retry_after = await self.state.refresh_limiter.claim()
        if retry_after is not None:
            raise RefreshThrottled(retry_after)
        log_event(logging.getLogger(__name__), "manual_refresh")
        try:
            outcomes = await self.state.collector.collect_all()
            usage = await asyncio.to_thread(self.read)
        except Exception:
            raise StorageUnavailable("storage_unavailable") from None
        return usage, all(outcomes.values())
