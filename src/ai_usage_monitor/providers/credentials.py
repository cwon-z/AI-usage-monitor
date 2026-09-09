"""Read-only credential extraction. Refresh credentials never enter child processes."""

from __future__ import annotations

import base64
import json
import math
import os
import stat
import time
from pathlib import Path
from typing import Any

from .base import ProviderError


def read_secret(path: Path, *, max_bytes: int = 1_000_000) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ProviderError("authentication_invalid")
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ProviderError("authentication_invalid")
        return raw.decode("utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ProviderError("authentication_missing") from exc


def secret_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ProviderError("authentication_invalid") from exc
    if not isinstance(value, dict):
        raise ProviderError("authentication_invalid")
    return value


def token_string(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 65_536
        or any(not 33 <= ord(c) <= 126 for c in value)
    ):
        raise ProviderError("authentication_invalid")
    return value


def check_expiration(value: Any, *, milliseconds: bool = False) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ProviderError("authentication_invalid")
    if value / (1000 if milliseconds else 1) <= time.time():
        raise ProviderError("authentication_expired")


def codex_credentials(raw: dict[str, Any]) -> dict[str, Any]:
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        raise ProviderError("authentication_invalid")
    access = token_string(tokens.get("access_token"))
    # Decode only to reject expired credentials early; upstream verifies authenticity.
    try:
        encoded = access.split(".")[1]
        claims = secret_object(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        )
    except (IndexError, ValueError, UnicodeError) as exc:
        raise ProviderError("authentication_invalid") from exc
    if "exp" not in claims:
        raise ProviderError("authentication_invalid")
    check_expiration(claims["exp"])
    account_id = token_string(tokens.get("account_id"))
    return {
        "access_token": access,
        "id_token": token_string(tokens.get("id_token")),
        "account_id": account_id,
        "refresh_token": "",
    }


def claude_credentials(raw: dict[str, Any]) -> dict[str, Any]:
    oauth = raw.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise ProviderError("authentication_invalid")
    scopes = oauth.get("scopes")
    if not isinstance(scopes, list) or not all(
        isinstance(scope, str) for scope in scopes
    ):
        raise ProviderError("authentication_invalid")
    if "user:profile" not in scopes or "user:inference" not in scopes:
        raise ProviderError("authentication_scope_missing")
    check_expiration(oauth.get("expiresAt"), milliseconds=True)
    result = {
        "accessToken": token_string(oauth.get("accessToken")),
        "scopes": ["user:profile", "user:inference"],
        "expiresAt": oauth.get("expiresAt"),
    }
    for key in ("subscriptionType", "rateLimitTier"):
        value = oauth.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 100):
            raise ProviderError("authentication_invalid")
        result[key] = value
    return result


def child_environment() -> dict[str, str]:
    # No inherited provider URLs, tokens, API-key helpers, tracing, or config flags.
    allowed = {"PATH", "USER", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "WINDIR"}
    return {key: value for key, value in os.environ.items() if key in allowed}
