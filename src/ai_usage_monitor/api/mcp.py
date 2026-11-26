"""Private Streamable HTTP MCP tools over the application's shared cache."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from ai_usage_monitor.api.auth import valid_bearer
from ai_usage_monitor.services.normalizer import utc_now
from ai_usage_monitor.services.usage import RefreshThrottled, StorageUnavailable

Provider = Literal["claude", "openai"]
MaxAge = Annotated[int, Field(ge=1, le=86400)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


# Access verification can block on a signing-key fetch (up to its timeout while
# the endpoint is unreachable) and runs before authentication succeeds. Capping
# it keeps unauthenticated requests from occupying the shared worker threads
# that collection and storage also use. Verification with cached keys is
# sub-millisecond, so a single-user deployment never reaches the cap.
MAX_CONCURRENT_ACCESS_CHECKS = 2


class MCPAuthentication:
    """Authenticate before the SDK reads a body or dispatches any MCP method."""

    def __init__(self, app, token: str, settings=None):
        self.app, self.token = app, token
        self.access = None
        self.public_host = settings.mcp_public_host if settings else ""
        self._access_checks = 0
        if settings and settings.mcp_access_team:
            from ai_usage_monitor.api.access import AccessVerifier

            self.access = AccessVerifier(settings)

    async def _verify_access(self, assertion: str) -> bool:
        if self.access is None or self._access_checks >= MAX_CONCURRENT_ACCESS_CHECKS:
            return False
        loop = asyncio.get_running_loop()

        def release() -> None:
            self._access_checks -= 1

        def verify() -> bool:
            # Released when the thread really finishes: a cancelled request
            # stops waiting, but its thread keeps running until then.
            try:
                return self.access.verify(assertion)
            finally:
                loop.call_soon_threadsafe(release)

        self._access_checks += 1
        return await asyncio.to_thread(verify)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # MCP is served over HTTP only. Refuse any other transport here
            # instead of relying on the wrapped application to reject it.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "").lower().split(":")[0].rstrip(".")
        public = (
            (bool(self.public_host) and host == self.public_host)
            or "cf-access-jwt-assertion" in headers
            or "cf-connecting-ip" in headers
        )
        if public:
            authorized = await self._verify_access(
                headers.get("cf-access-jwt-assertion", "")
            )
        else:
            authorized = valid_bearer(headers.get("authorization", ""), self.token)
        if not authorized:
            response = JSONResponse(
                {"detail": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def quota_report(usage, *, provider: Provider | None, max_age_seconds: int):
    now = utc_now()
    results = {}
    for name, result in usage.providers.items():
        if provider is not None and name != provider:
            continue
        age = (
            (now - result.collected_at).total_seconds() if result.collected_at else None
        )
        reasons = list(result.stale_reasons)
        if (
            age is not None
            and age > max_age_seconds
            and "sample_too_old" not in reasons
        ):
            reasons.append("sample_too_old")
        usable = (
            result.status == "ok"
            and not result.stale
            and age is not None
            and 0 <= age <= max_age_seconds
        )
        results[name] = {
            **result.model_dump(mode="json"),
            "sample_age_seconds": round(age, 2) if age is not None else None,
            "usable_for_decisions": usable,
            "decision_blockers": reasons
            or ([] if usable else [result.error or "unavailable"]),
        }
    return {
        "checked_at": now.isoformat(),
        "max_age_seconds": max_age_seconds,
        "providers": results,
        "guidance": "Percentages are remaining subscription quota, not token counts. Null means unknown. "
        "Use only usable_for_decisions samples. Model-specific additional_windows are separate constraints; "
        "never sum aliases. Cached quota is not a reservation or a guarantee that a request will succeed.",
    }


def create_mcp(service, settings):
    server = MCPServer(
        "AI Usage Monitor",
        version="0.2.0",
        instructions="Check get_usage_limits before choosing an AI provider. Respect freshness and "
        "all applicable windows; unknown or stale limits must not be treated as available quota.",
    )
    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    async def read():
        try:
            return await asyncio.to_thread(service.read)
        except Exception:
            raise ToolError("storage_unavailable") from None

    @server.tool(annotations=read_only, structured_output=True)
    async def get_usage_limits(
        provider: Provider | None = None, max_age_seconds: MaxAge = 300
    ) -> dict[str, Any]:
        """Read cached remaining quotas, reset times, pacing and freshness. No provider calls."""
        return quota_report(
            await read(), provider=provider, max_age_seconds=max_age_seconds
        )

    @server.tool(annotations=read_only, structured_output=True)
    async def check_usage_budget(
        provider: Provider,
        minimum_remaining_percent: Percent = 10,
        max_age_seconds: MaxAge = 300,
    ) -> dict[str, Any]:
        """Conservative check of ALL reported canonical and model windows against a reserve.

        Returns unknown for stale/missing data. This cannot predict a task's token cost.
        Model-specific limits may make this stricter than needed; inspect get_usage_limits
        to select a model. Never sum or average windows.
        """
        report = quota_report(
            await read(), provider=provider, max_age_seconds=max_age_seconds
        )
        result = report["providers"][provider]
        windows = {
            key: result[key]
            for key in ("session", "five_hour", "weekly")
            if result[key] is not None
        }
        windows.update(
            {
                f"additional:{key}": value
                for key, value in result["additional_windows"].items()
            }
        )
        blockers = [
            key
            for key, window in windows.items()
            if window["remaining_percent"] <= minimum_remaining_percent
        ]
        decision = (
            "unknown"
            if not result["usable_for_decisions"] or not windows
            else ("insufficient" if blockers else "available")
        )
        return {
            **report,
            "decision": decision,
            "minimum_remaining_percent": minimum_remaining_percent,
            "limiting_windows": blockers,
            "evaluated_windows": list(windows),
        }

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def refresh_usage() -> dict[str, Any]:
        """Poll enabled providers once through the shared throttled collector. No model requests.

        Usually use cached get_usage_limits. A rate-limited response includes retry_after_seconds.
        """
        try:
            usage, success = await service.refresh()
        except RefreshThrottled as exc:
            return {
                "error": "manual_refresh_rate_limited",
                "retry_after_seconds": exc.retry_after,
            }
        except StorageUnavailable:
            raise ToolError("storage_unavailable") from None
        return {
            "all_providers_ok": success,
            **quota_report(usage, provider=None, max_age_seconds=300),
        }

    transport = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=65536,
        transport_security=TransportSecuritySettings(
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )
    return server, MCPAuthentication(
        transport, settings.mcp_api_token.get_secret_value(), settings
    )
