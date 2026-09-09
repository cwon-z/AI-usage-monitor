from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
)

ProviderStatus = Literal["ok", "error", "unavailable"]
PaceStatus = Literal["under_budget", "on_pace", "over_budget"]


class PaceInfo(BaseModel):
    time_elapsed_percent: float = Field(ge=0, le=100)
    pace_delta: float
    pace: PaceStatus
    projected_exhaustion_before_reset: bool


class QuotaWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    used_percent: float = Field(ge=0, le=10_000)
    remaining_percent: float = Field(ge=0, le=100)
    reset_at: AwareDatetime | None = None
    window_minutes: int | None = Field(default=None, gt=0)
    source: str | None = Field(default=None, max_length=160)
    pace: PaceInfo | None = None

    @field_validator("used_percent", "remaining_percent")
    @classmethod
    def finite_percent(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("percentage must be finite")
        return round(value, 4)

    @classmethod
    def from_used(
        cls,
        used_percent: float,
        *,
        reset_at: datetime | None,
        window_minutes: int | None,
        source: str | None = None,
    ) -> QuotaWindow:
        return cls(
            used_percent=used_percent,
            remaining_percent=max(0.0, min(100.0, 100.0 - used_percent)),
            reset_at=reset_at,
            window_minutes=window_minutes,
            source=source,
        )


class Credits(BaseModel):
    balance: str | None = Field(
        default=None, max_length=100, pattern=r"^-?\d+(\.\d+)?$"
    )
    has_credits: StrictBool | None = None
    unlimited: StrictBool | None = None


class ProviderCapabilities(BaseModel):
    session: bool = False
    five_hour: bool = False
    weekly: bool = False
    credits: bool = False
    plan: bool = False
    additional_windows: bool = False


class ProviderUsage(BaseModel):
    session: QuotaWindow | None = None
    five_hour: QuotaWindow | None = None
    weekly: QuotaWindow | None = None
    additional_windows: dict[str, QuotaWindow] = Field(default_factory=dict)
    credits: Credits | None = None
    plan: str | None = Field(default=None, max_length=100)
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)
    collected_at: AwareDatetime


class ProviderResult(ProviderUsage):
    collected_at: AwareDatetime | None = None
    last_attempt_at: AwareDatetime | None = None
    status: ProviderStatus
    stale: bool = False
    error: str | None = Field(default=None, max_length=100)


class UsageResponse(BaseModel):
    providers: dict[str, ProviderResult]
    updated_at: AwareDatetime | None
    stale: bool


class CompactProvider(BaseModel):
    status: ProviderStatus
    stale: bool
    error: str | None = None
    collected_at: AwareDatetime | None = None
    session: int | None = None
    weekly: int | None = None
    five_hour: int | None = None
    session_reset_in: str | None = None
    weekly_reset_in: str | None = None
    five_hour_reset_in: str | None = None


class CompactUsageResponse(BaseModel):
    claude: CompactProvider
    openai: CompactProvider
    updated_at: AwareDatetime | None
    stale: bool


class HistoryItem(BaseModel):
    id: int
    provider: str
    quota_type: str
    scope: str | None
    used_percent: float
    reset_at: AwareDatetime | None
    collected_at: AwareDatetime
    window_minutes: int | None


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    next_before_id: int | None = None


class ProviderDescriptor(BaseModel):
    name: str
    enabled: bool
    integration: str
    interface_status: Literal[
        "official", "supported_cli", "experimental_cli", "undocumented"
    ]
    configured: bool
    capabilities: list[str]


class ProvidersResponse(BaseModel):
    providers: list[ProviderDescriptor]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    scheduler: Literal["ok", "stopped", "error"]
