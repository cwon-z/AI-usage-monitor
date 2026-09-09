from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.providers.base import JSONLCommandClient, ProviderError
from ai_usage_monitor.providers.openai_codex import OpenAICodexProvider


@pytest.mark.asyncio
async def test_provider_timeout():
    client = JSONLCommandClient()
    with pytest.raises(ProviderError, match="timeout"):
        await client.request(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            messages=[{"hello": "world"}],
            matcher=lambda _value: True,
            timeout=0.05,
        )


@pytest.mark.asyncio
async def test_missing_codex_authentication(settings):
    provider = OpenAICodexProvider(settings)
    with pytest.raises(ProviderError, match="authentication_missing"):
        await provider.get_usage()


@pytest.mark.asyncio
async def test_cli_unavailable():
    client = JSONLCommandClient()
    with pytest.raises(ProviderError, match="cli_unavailable"):
        await client.request(
            ["/definitely/not/a/command"],
            messages=[],
            matcher=lambda _value: True,
            timeout=1,
        )


def test_example_api_token_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="replace the example"):
        Settings(
            widget_api_token="replace-with-at-least-32-random-characters",
            database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        )
