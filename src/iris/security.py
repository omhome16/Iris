"""HTTP auth — shared-secret bearer token for iris-core and the dashboard proxy.

Design: IRIS_API_TOKEN from settings/env. When unset, auth is disabled with a
boot-time warning (zero friction for local dev). When set, every route except
/health requires `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request

from iris.config import settings

log = logging.getLogger("iris.security")

TOKEN_ENV = "IRIS_API_TOKEN"


def require_token(request: Request) -> None:
    """FastAPI dependency. No-op unless a token is configured."""
    if not settings.iris_api_token:
        return
    auth = request.headers.get("authorization", "")
    if not secrets.compare_digest(auth, f"Bearer {settings.iris_api_token}"):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def warn_if_unset() -> None:
    if not settings.iris_api_token:
        log.warning(
            "%s is not set — iris-core HTTP API is unauthenticated. "
            "Set it in .env to require a bearer token.",
            TOKEN_ENV,
        )


def auth_headers() -> dict[str, str]:
    """Headers the bridge/dashboard attach when calling iris-core."""
    if settings.iris_api_token:
        return {"Authorization": f"Bearer {settings.iris_api_token}"}
    return {}