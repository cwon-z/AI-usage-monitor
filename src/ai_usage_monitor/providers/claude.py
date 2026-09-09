from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.models.schemas import (
    ProviderCapabilities,
    ProviderUsage,
    QuotaWindow,
)
from ai_usage_monitor.services.normalizer import parse_timestamp

from .base import ProviderError, UsageProvider
from .credentials import (
    claude_credentials,
    read_secret,
    secret_object,
    token_string,
)


class ClaudeProvider(UsageProvider):
    """Isolated adapter for the undocumented OAuth endpoint used by Claude Code /usage."""

    name = "claude"

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.client = client

    def _read_credentials(self) -> dict[str, Any]:
        if self.settings.claude_oauth_token is not None:
            token = token_string(self.settings.claude_oauth_token.get_secret_value())
        elif self.settings.claude_oauth_token_file is not None:
            token = token_string(
                read_secret(self.settings.claude_oauth_token_file, max_bytes=65_536)
            )
        elif self.settings.claude_credentials_file is not None:
            return claude_credentials(
                secret_object(read_secret(self.settings.claude_credentials_file))
            )
        else:
            raise ProviderError("authentication_missing")
        return claude_credentials(
            {
                "claudeAiOauth": {
                    "accessToken": token,
                    "scopes": self.settings.claude_oauth_scopes.split(),
                }
            }
        )

    async def _request(self) -> dict[str, Any]:
        credentials = await asyncio.to_thread(self._read_credentials)
        try:
            if self.client is not None:
                return await self._fetch(self.client, credentials)
            async with httpx.AsyncClient(trust_env=False) as client:
                return await self._fetch(client, credentials)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", retryable=True) from exc
        except httpx.RequestError as exc:
            raise ProviderError("upstream_error", retryable=True) from exc

    async def _fetch(
        self, client: httpx.AsyncClient, credentials: dict[str, Any]
    ) -> dict[str, Any]:
        # Verified against Claude Code 2.1.266's /usage implementation.
        # Fixed origin, no redirects/proxies, no refresh endpoint, bounded body.
        async with client.stream(
            "GET",
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": "Bearer " + credentials["accessToken"],
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
            },
            timeout=self.settings.provider_timeout_seconds,
            follow_redirects=False,
        ) as response:
            if response.status_code == 401:
                raise ProviderError("authentication_expired")
            if response.status_code == 403:
                raise ProviderError("authentication_scope_missing")
            if response.status_code != 200:
                raise ProviderError(
                    "upstream_error",
                    retryable=response.status_code == 429
                    or response.status_code >= 500,
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 1_000_000:
                    raise ProviderError("malformed_response")
        try:
            import json

            limits = json.loads(body)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ProviderError("malformed_response") from exc
        if not isinstance(limits, dict):
            raise ProviderError("malformed_response")
        return {
            "rate_limits_available": True,
            "rate_limits": limits,
            "subscription_type": credentials.get("subscriptionType"),
        }

    @staticmethod
    def _window(
        raw: Any,
        *,
        minutes: int,
        source: str,
    ) -> QuotaWindow | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ProviderError("malformed_response")
        used = raw.get("utilization")
        if used is None:
            return None
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            raise ProviderError("malformed_response")
        try:
            reset_at = parse_timestamp(raw.get("resets_at"))
            return QuotaWindow.from_used(
                float(used),
                reset_at=reset_at,
                window_minutes=minutes,
                source=source,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError("malformed_response") from exc

    def normalize_payload(self, payload: dict[str, Any]) -> ProviderUsage:
        if payload.get("rate_limits_available") is False:
            raise ProviderError("authentication_scope_missing")
        if payload.get("rate_limits_available") is not True:
            raise ProviderError("malformed_response")
        limits = payload.get("rate_limits")
        if limits is None:
            raise ProviderError("upstream_error", retryable=True)
        if not isinstance(limits, dict):
            raise ProviderError("malformed_response")

        session = self._window(limits.get("five_hour"), minutes=300, source="five_hour")
        weekly = self._window(
            limits.get("seven_day"), minutes=10_080, source="seven_day"
        )
        additional: dict[str, QuotaWindow] = {}
        for key in ("seven_day_oauth_apps", "seven_day_opus", "seven_day_sonnet"):
            window = self._window(limits.get(key), minutes=10_080, source=key)
            if window is not None:
                additional[key] = window

        model_scoped = limits.get("model_scoped", [])
        if model_scoped is None:
            model_scoped = []
        if not isinstance(model_scoped, list) or len(model_scoped) > 100:
            raise ProviderError("malformed_response")
        for raw in model_scoped:
            if not isinstance(raw, dict):
                raise ProviderError("malformed_response")
            label = raw.get("display_name")
            if (
                not isinstance(label, str)
                or not label
                or len(label) > 140
                or f"model:{label}" in additional
            ):
                raise ProviderError("malformed_response")
            window = self._window(raw, minutes=10_080, source=label)
            if window is not None:
                additional[f"model:{label}"] = window

        credits = None
        extra = limits.get("extra_usage")
        if extra is not None and not isinstance(extra, dict):
            raise ProviderError("malformed_response")
        # used_credits is consumption, never a remaining balance. Do not invent
        # currency units or overloading has_credits from an enabled flag.

        try:
            return ProviderUsage(
                session=session,
                weekly=weekly,
                additional_windows=additional,
                credits=credits,
                plan=payload.get("subscription_type"),
                capabilities=ProviderCapabilities(
                    session=session is not None,
                    five_hour=session is not None,
                    weekly=weekly is not None,
                    credits=credits is not None,
                    plan=payload.get("subscription_type") is not None,
                    additional_windows=bool(additional),
                ),
                collected_at=datetime.now(UTC),
            )
        except ValidationError as exc:
            raise ProviderError("malformed_response") from exc

    async def get_usage(self) -> ProviderUsage:
        try:
            return self.normalize_payload(await self._request())
        except ValidationError as exc:
            raise ProviderError("malformed_response") from exc
