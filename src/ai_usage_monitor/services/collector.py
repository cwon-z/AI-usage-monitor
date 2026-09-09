from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import UTC, datetime

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.models.schemas import ProviderUsage
from ai_usage_monitor.providers.base import ProviderError, UsageProvider
from ai_usage_monitor.services.history import HistoryRepository
from ai_usage_monitor.services.logging import log_event


class Collector:
    def __init__(
        self,
        providers: list[UsageProvider],
        repository: HistoryRepository,
        settings: Settings,
    ) -> None:
        self.providers = providers
        self.repository = repository
        self.settings = settings
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[dict[str, bool]] | None = None
        self.logger = logging.getLogger(__name__)

    async def _collect_provider(self, provider: UsageProvider) -> bool:
        last_error = ProviderError("provider_failed")
        for attempt in range(self.settings.provider_retry_attempts):
            try:
                usage = await asyncio.wait_for(
                    provider.get_usage(),
                    timeout=self.settings.provider_timeout_seconds + 3,
                )
                if not isinstance(usage, ProviderUsage):
                    raise ProviderError("malformed_response")
            except ProviderError as exc:
                last_error = exc
            except TimeoutError:
                last_error = ProviderError("timeout", retryable=True)
            # A third-party adapter must never terminate collection for its peers.
            except Exception:  # noqa: BLE001
                last_error = ProviderError("provider_failed", retryable=True)
            else:
                # Storage failure must never retry an otherwise successful provider.
                await asyncio.to_thread(
                    self.repository.save_success, provider.name, usage
                )
                log_event(
                    self.logger, "provider_collection_success", provider=provider.name
                )
                log_event(self.logger, "snapshot_saved", provider=provider.name)
                return True

            if (
                not last_error.retryable
                or attempt + 1 >= self.settings.provider_retry_attempts
            ):
                break
            delay = self.settings.provider_retry_base_seconds * (2**attempt)
            if delay:
                await asyncio.sleep(delay)

        await asyncio.to_thread(
            self.repository.save_failure, provider.name, last_error.code
        )
        event = (
            "provider_auth_expired"
            if last_error.code
            in {
                "authentication_expired",
                "authentication_missing",
            }
            else "provider_collection_failed"
        )
        log_event(
            self.logger, event, provider=provider.name, error_code=last_error.code
        )
        return False

    async def collect_all(self) -> dict[str, bool]:
        # Simultaneous scheduled/manual refreshes share a cycle, not a queued poll.
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._cycle())
            self._inflight.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
        return await asyncio.shield(self._inflight)

    async def close(self) -> None:
        if self._inflight is not None:
            self._inflight.cancel()
            await asyncio.gather(self._inflight, return_exceptions=True)

    async def _cycle(self) -> dict[str, bool]:
        async with self._lock:
            log_event(self.logger, "collector_started")
            pairs = await asyncio.gather(
                *(self._collect_provider(provider) for provider in self.providers),
                return_exceptions=True,
            )
            if any(isinstance(result, BaseException) for result in pairs):
                raise RuntimeError("storage_unavailable")
            await asyncio.to_thread(self.repository.prune, self.settings.retention_days)
            return {
                provider.name: ok
                for provider, ok in zip(self.providers, pairs, strict=True)
            }


class CollectionScheduler:
    def __init__(self, collector: Collector, interval_seconds: int) -> None:
        self.collector = collector
        self.interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_cycle_ok = True

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="usage-collector")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.collector.collect_all()
                self.last_cycle_ok = True
            except Exception:  # noqa: BLE001 — preserve the scheduling loop
                self.last_cycle_ok = False
                log_event(
                    logging.getLogger(__name__),
                    "collection_cycle_failed",
                    error_code="storage_unavailable",
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


class ManualRefreshLimiter:
    def __init__(self, min_interval_seconds: int) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_refresh: datetime | None = None
        self._last_monotonic: float | None = None

    async def claim(self, now: datetime | None = None) -> int | None:
        current = now or datetime.now(UTC)
        async with self._lock:
            clock = time.monotonic()
            if self._last_monotonic is not None:
                elapsed = (
                    (current - self._last_refresh).total_seconds()
                    if now is not None and self._last_refresh
                    else clock - self._last_monotonic
                )
                if elapsed < self.min_interval_seconds:
                    return max(1, math.ceil(self.min_interval_seconds - elapsed))
            self._last_refresh = current
            self._last_monotonic = clock
            return None
