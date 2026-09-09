from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.models.schemas import (
    Credits,
    ProviderCapabilities,
    ProviderUsage,
    QuotaWindow,
)
from ai_usage_monitor.services.normalizer import parse_timestamp

from .base import JSONLCommandClient, ProviderError, UsageProvider
from .credentials import (
    child_environment,
    codex_credentials,
    read_secret,
    secret_object,
)


class OpenAICodexProvider(UsageProvider):
    """OpenAI Codex's documented app-server rate-limit adapter."""

    name = "openai"

    def __init__(
        self, settings: Settings, client: JSONLCommandClient | None = None
    ) -> None:
        self.settings = settings
        self.client = client or JSONLCommandClient()

    def _validate_auth_file(self) -> Path:
        path = self.settings.codex_auth_file
        try:
            if path is None or not path.is_file() or path.stat().st_size > 1_000_000:
                raise ProviderError("authentication_missing")
        except OSError as exc:
            raise ProviderError("authentication_missing") from exc
        return path

    async def _request(self) -> dict[str, Any]:
        auth_file = self._validate_auth_file()
        tokens = codex_credentials(
            secret_object(await asyncio.to_thread(read_secret, auth_file))
        )
        with tempfile.TemporaryDirectory(prefix="ai-usage-codex-") as runtime_dir:
            runtime_path = Path(runtime_dir)
            runtime_path.chmod(0o700)
            copied_auth = runtime_path / "auth.json"
            try:
                # No usable refresh token reaches the managed-auth CLI. Fresh metadata
                # avoids proactive age-based refresh; JWT expiry was checked above.
                with copied_auth.open("x", encoding="utf-8") as stream:
                    copied_auth.chmod(0o600)
                    json.dump(
                        {
                            "OPENAI_API_KEY": None,
                            "tokens": tokens,
                            "last_refresh": datetime.now(UTC).isoformat(),
                        },
                        stream,
                    )
            except OSError as exc:
                raise ProviderError("authentication_missing") from exc

            environment = child_environment()
            environment["CODEX_HOME"] = runtime_dir
            response = await self.client.request(
                [
                    self.settings.codex_cli_path,
                    "app-server",
                    "--disable",
                    "plugins",
                    "--stdio",
                ],
                messages=[
                    {
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {
                                "name": "ai-usage-monitor",
                                "version": "0.1.0",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    },
                    {"method": "initialized"},
                    {"id": 2, "method": "account/rateLimits/read", "params": None},
                ],
                matcher=lambda value: value.get("id") == 2,
                timeout=self.settings.provider_timeout_seconds,
                env=environment,
                cwd=runtime_dir,
            )

        if "error" in response:
            error = response.get("error")
            message = error.get("message", "") if isinstance(error, dict) else ""
            if any(
                word in str(message).lower()
                for word in (
                    "authentication",
                    "login",
                    "401",
                    "unauthorized",
                    "refresh token",
                )
            ):
                raise ProviderError("authentication_expired")
            raise ProviderError("upstream_error", retryable=True)
        payload = response.get("result")
        if not isinstance(payload, dict):
            raise ProviderError("malformed_response")
        return payload

    @staticmethod
    def _window(raw: Any, *, scope: str) -> QuotaWindow | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ProviderError("malformed_response")
        used = raw.get("usedPercent")
        duration = raw.get("windowDurationMins")
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            raise ProviderError("malformed_response")
        if duration is not None and (
            not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0
        ):
            raise ProviderError("malformed_response")
        try:
            reset_at = parse_timestamp(raw.get("resetsAt"))
            return QuotaWindow.from_used(
                float(used),
                reset_at=reset_at,
                window_minutes=duration,
                source=scope,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError("malformed_response") from exc

    def normalize_payload(self, payload: dict[str, Any]) -> ProviderUsage:
        canonical = payload.get("rateLimits")
        if not isinstance(canonical, dict):
            raise ProviderError("malformed_response")
        buckets = payload.get("rateLimitsByLimitId")
        if buckets is None:
            buckets = {}
        if not isinstance(buckets, dict):
            raise ProviderError("malformed_response")

        canonical_id = canonical.get("limitId") or "codex"
        if (
            not isinstance(canonical_id, str)
            or len(canonical_id) > 140
            or len(buckets) > 100
        ):
            raise ProviderError("malformed_response")
        ordered: list[tuple[str, dict[str, Any]]] = [(canonical_id, canonical)]
        for key, value in buckets.items():
            if (
                not isinstance(key, str)
                or len(key) > 140
                or not isinstance(value, dict)
            ):
                raise ProviderError("malformed_response")
            if key != canonical_id:
                ordered.append((key, value))
        additional: dict[str, QuotaWindow] = {}
        five_hour: QuotaWindow | None = None
        weekly: QuotaWindow | None = None

        for bucket_name, bucket in ordered:
            for position in ("primary", "secondary"):
                window = self._window(
                    bucket.get(position), scope=f"{bucket_name}:{position}"
                )
                if window is None:
                    continue
                additional[f"{bucket_name}:{position}"] = window
                if (
                    bucket_name == canonical_id
                    and window.window_minutes == 300
                    and five_hour is None
                ):
                    five_hour = window
                if (
                    bucket_name == canonical_id
                    and window.window_minutes == 10_080
                    and weekly is None
                ):
                    weekly = window

        raw_credits = canonical.get("credits")
        credits = None
        if raw_credits is not None:
            if not isinstance(raw_credits, dict):
                raise ProviderError("malformed_response")
            balance = raw_credits.get("balance")
            try:
                credits = Credits(
                    balance=balance,
                    has_credits=raw_credits.get("hasCredits"),
                    unlimited=raw_credits.get("unlimited"),
                )
            except ValidationError as exc:
                raise ProviderError("malformed_response") from exc

        plan = canonical.get("planType")
        if plan is not None and not isinstance(plan, str):
            raise ProviderError("malformed_response")
        try:
            return ProviderUsage(
                five_hour=five_hour,
                weekly=weekly,
                additional_windows=additional,
                credits=credits,
                plan=plan,
                capabilities=ProviderCapabilities(
                    five_hour=five_hour is not None,
                    weekly=weekly is not None,
                    credits=credits is not None,
                    plan=plan is not None,
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
