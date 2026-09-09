from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    widget_api_token: SecretStr = Field(min_length=16)
    widget_api_token_file: Path | None = None
    database_url: str = "sqlite:///./data/usage.db"
    collection_interval_seconds: int = Field(default=600, ge=30, le=86_400)
    retention_days: int = Field(default=30, ge=1, le=3_650)
    stale_after_seconds: int = Field(default=1_800, ge=60, le=604_800)
    manual_refresh_min_interval_seconds: int = Field(default=60, ge=1, le=3_600)
    provider_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    provider_retry_attempts: int = Field(default=3, ge=1, le=5)
    provider_retry_base_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    log_level: str = "INFO"

    claude_enabled: bool = True
    claude_cli_path: str = "claude"
    claude_oauth_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CLAUDE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    )
    claude_oauth_token_file: Path | None = None
    claude_oauth_scopes: str = "user:profile user:inference"
    claude_credentials_file: Path | None = Field(
        default_factory=lambda: Path.home() / ".claude" / ".credentials.json"
    )

    codex_enabled: bool = True
    codex_cli_path: str = "codex"
    codex_auth_file: Path | None = Field(
        default_factory=lambda: Path.home() / ".codex" / "auth.json"
    )

    @model_validator(mode="before")
    @classmethod
    def read_widget_token(cls, values):
        path = values.get("widget_api_token_file")
        if path and not values.get("widget_api_token"):
            from ai_usage_monitor.providers.base import ProviderError
            from ai_usage_monitor.providers.credentials import read_secret

            try:
                values["widget_api_token"] = read_secret(
                    Path(path).expanduser(), max_bytes=4096
                )
            except ProviderError as exc:
                raise ValueError("widget token file is unavailable or invalid") from exc
        return values

    @field_validator(
        "claude_oauth_token_file",
        "claude_credentials_file",
        "codex_auth_file",
        "widget_api_token_file",
        mode="after",
    )
    @classmethod
    def expand_paths(cls, value: Path | None) -> Path | None:
        return value.expanduser() if value is not None else None

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("invalid log level")
        return normalized

    @field_validator("widget_api_token")
    @classmethod
    def reject_placeholder_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if any(c.isspace() for c in token) or len(token) > 4096:
            raise ValueError("widget token must be a single non-whitespace value")
        if token.lower().startswith("replace-with"):
            raise ValueError("replace the example widget API token")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value: str) -> str:
        try:
            url = make_url(value)
        except Exception as exc:
            raise ValueError("invalid SQLite URL") from exc
        if url.drivername != "sqlite" or url.query or url.host or not url.database:
            raise ValueError("use a local SQLite URL without query parameters")
        if url.database != ":memory:":
            value = str(url.set(database=str(Path(url.database).expanduser())))
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
