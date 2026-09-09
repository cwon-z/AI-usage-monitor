"""Deployment checks without contacting providers or modifying authentication."""

from __future__ import annotations

import sys

from ai_usage_monitor.config.settings import Settings
from ai_usage_monitor.models.database import create_database
from ai_usage_monitor.providers.base import ProviderError
from ai_usage_monitor.providers.claude import ClaudeProvider
from ai_usage_monitor.providers.credentials import (
    codex_credentials,
    read_secret,
    secret_object,
)
from ai_usage_monitor.services.history import HistoryRepository


def main() -> None:
    engine = None
    try:
        settings = Settings()
        engine, factory = create_database(settings.database_url)
        if not HistoryRepository(factory).ping():
            raise RuntimeError("database unavailable")
        # Exercise a write transaction without modifying any rows.
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "UPDATE provider_state SET provider=provider WHERE 0"
            )
            connection.rollback()
        if settings.claude_enabled:
            ClaudeProvider(settings)._read_credentials()
        if settings.codex_enabled:
            if settings.codex_auth_file is None:
                raise ProviderError("authentication_missing")
            codex_credentials(secret_object(read_secret(settings.codex_auth_file)))
    except Exception as error:  # noqa: BLE001 — never print secret input/driver errors
        code = (
            error.code
            if isinstance(error, ProviderError)
            else "configuration_or_storage_invalid"
        )
        print(f"Preflight failed: {code}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            engine.dispose()
    print(
        "Preflight passed: widget token, database, enabled provider credential files. No provider requests made."
    )


if __name__ == "__main__":
    main()
