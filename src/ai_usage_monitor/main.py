from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from ai_usage_monitor.api.routes_health import router as health_router
from ai_usage_monitor.api.routes_usage import router as usage_router
from ai_usage_monitor.config.settings import Settings, get_settings
from ai_usage_monitor.models.database import create_database
from ai_usage_monitor.models.schemas import ProviderDescriptor
from ai_usage_monitor.providers import (
    ClaudeProvider,
    OpenAICodexProvider,
    UsageProvider,
)
from ai_usage_monitor.services.collector import (
    CollectionScheduler,
    Collector,
    ManualRefreshLimiter,
)
from ai_usage_monitor.services.history import HistoryRepository
from ai_usage_monitor.services.logging import configure_logging


def create_app(
    settings: Settings | None = None,
    *,
    providers: list[UsageProvider] | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    engine, session_factory = create_database(resolved.database_url)
    repository = HistoryRepository(session_factory)

    configured_providers = providers
    if configured_providers is None:
        configured_providers = []
        if resolved.claude_enabled:
            configured_providers.append(ClaudeProvider(resolved))
        if resolved.codex_enabled:
            configured_providers.append(OpenAICodexProvider(resolved))

    provider_names = [provider.name for provider in configured_providers]
    for expected in ("claude", "openai"):
        if expected not in provider_names:
            provider_names.append(expected)

    collector = Collector(configured_providers, repository, resolved)
    scheduler = (
        CollectionScheduler(collector, resolved.collection_interval_seconds)
        if start_scheduler
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                await scheduler.stop()
            await collector.close()
            engine.dispose()

    app = FastAPI(
        title="AI Usage Monitor",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.repository = repository
    app.state.collector = collector
    app.state.scheduler = scheduler
    app.state.refresh_limiter = ManualRefreshLimiter(
        resolved.manual_refresh_min_interval_seconds
    )
    app.state.provider_names = provider_names
    app.state.disabled_providers = {
        name
        for name, enabled in (
            ("claude", resolved.claude_enabled),
            ("openai", resolved.codex_enabled),
        )
        if not enabled
    }
    app.state.provider_descriptors = [
        ProviderDescriptor(
            name="claude",
            enabled=resolved.claude_enabled,
            integration="claude_oauth_usage",
            interface_status="undocumented",
            configured=bool(
                resolved.claude_oauth_token
                or resolved.claude_oauth_token_file
                or (
                    resolved.claude_credentials_file
                    and resolved.claude_credentials_file.is_file()
                )
            ),
            capabilities=["session", "weekly", "plan", "model_scoped_windows"],
        ),
        ProviderDescriptor(
            name="openai",
            enabled=resolved.codex_enabled,
            integration="codex_app_server",
            interface_status="official",
            configured=bool(
                resolved.codex_auth_file and resolved.codex_auth_file.is_file()
            ),
            capabilities=[
                "five_hour",
                "weekly",
                "credits",
                "plan",
                "named_quota_buckets",
            ],
        ),
    ]
    app.include_router(health_router)
    app.include_router(usage_router)

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def storage_unavailable(_request, _error):
        return JSONResponse(
            status_code=503,
            content={"detail": "storage_unavailable"},
            headers={"Cache-Control": "no-store"},
        )

    return app


def run() -> None:
    uvicorn.run(
        "ai_usage_monitor.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        access_log=False,
    )


if __name__ == "__main__":
    run()
