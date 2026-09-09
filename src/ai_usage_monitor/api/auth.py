from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status


def require_api_token(request: Request) -> None:
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    expected = request.app.state.settings.widget_api_token.get_secret_value()
    valid = (
        scheme.lower() == "bearer"
        and bool(supplied)
        and secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
