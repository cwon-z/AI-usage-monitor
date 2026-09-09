from __future__ import annotations

from datetime import UTC

import pytest

from ai_usage_monitor.providers.base import ProviderError
from ai_usage_monitor.providers.claude import ClaudeProvider
from ai_usage_monitor.providers.openai_codex import OpenAICodexProvider


def test_claude_provider_normalization(settings):
    provider = ClaudeProvider(settings)
    usage = provider.normalize_payload(
        {
            "subscription_type": "max",
            "rate_limits_available": True,
            "rate_limits": {
                "five_hour": {
                    "utilization": 37.2,
                    "resets_at": "2026-09-10T02:43:00+09:00",
                },
                "seven_day": {
                    "utilization": 61,
                    "resets_at": "2026-09-14T10:00:00+09:00",
                },
                "seven_day_opus": {"utilization": 11, "resets_at": None},
                "model_scoped": [
                    {"display_name": "Fable", "utilization": 9, "resets_at": None}
                ],
                "extra_usage": {"is_enabled": True, "used_credits": 4.25},
            },
        }
    )
    assert usage.session and usage.session.used_percent == 37.2
    assert usage.session.remaining_percent == 62.8
    assert usage.weekly and usage.weekly.reset_at.utcoffset() == UTC.utcoffset(None)
    assert usage.additional_windows["seven_day_opus"].used_percent == 11
    assert usage.additional_windows["model:Fable"].used_percent == 9
    assert usage.credits is None  # Consumed extra usage is not a balance.
    assert usage.plan == "max"


def test_codex_provider_normalizes_multi_bucket_response(settings):
    provider = OpenAICodexProvider(settings)
    usage = provider.normalize_payload(
        {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 57,
                    "windowDurationMins": 10080,
                    "resetsAt": 1789493161,
                },
                "secondary": None,
                "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                "planType": "prolite",
            },
            "rateLimitsByLimitId": {
                "codex": {},
                "codex_bengalfox": {
                    "primary": {
                        "usedPercent": 18,
                        "windowDurationMins": 300,
                        "resetsAt": 1788987360,
                    },
                    "secondary": {
                        "usedPercent": 26,
                        "windowDurationMins": 10080,
                        "resetsAt": 1789574160,
                    },
                },
            },
        }
    )
    assert usage.five_hour is None  # Never substitute another model's quota.
    assert usage.weekly and usage.weekly.used_percent == 57
    assert usage.weekly.source == "codex:primary"
    assert usage.credits and usage.credits.balance == "0"
    assert usage.plan == "prolite"
    assert set(usage.additional_windows) == {
        "codex:primary",
        "codex_bengalfox:primary",
        "codex_bengalfox:secondary",
    }


def test_codex_does_not_invent_missing_five_hour_window(settings):
    usage = OpenAICodexProvider(settings).normalize_payload(
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 31,
                    "windowDurationMins": 10080,
                    "resetsAt": None,
                },
                "secondary": None,
            }
        }
    )
    assert usage.five_hour is None
    assert usage.weekly and usage.weekly.used_percent == 31
    assert usage.capabilities.five_hour is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"rateLimits": []},
        {"rateLimits": {"primary": {"usedPercent": "bad", "windowDurationMins": 300}}},
        {"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": -1}}},
    ],
)
def test_codex_malformed_response(settings, payload):
    with pytest.raises(ProviderError, match="malformed_response"):
        OpenAICodexProvider(settings).normalize_payload(payload)


def test_claude_missing_authentication(settings):
    with pytest.raises(ProviderError, match="authentication_scope_missing"):
        ClaudeProvider(settings).normalize_payload(
            {
                "subscription_type": None,
                "rate_limits_available": False,
                "rate_limits": None,
            }
        )
