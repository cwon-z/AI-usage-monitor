from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status


def valid_bearer(authorization: str, expected: str) -> bool:
    scheme, _, supplied = authorization.partition(" ")
    return (
        scheme.lower() == "bearer"
        and bool(supplied)
        and len(supplied) <= 4096
        and secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    )


def require_api_token(request: Request) -> None:
    authorization = request.headers.get("authorization", "")
    expected = request.app.state.settings.widget_api_token.get_secret_value()
    valid = valid_bearer(authorization, expected)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
