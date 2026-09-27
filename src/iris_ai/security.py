"""HTTP auth — shared-secret bearer token for iris-core and its clients.

Design: IRIS_API_TOKEN from settings/env. When unset, auth is disabled with a
boot-time warning (zero friction for local dev). When set, every route except
/health requires `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request

from iris_ai.config import settings

log = logging.getLogger("iris_ai.security")

TOKEN_ENV = "IRIS_API_TOKEN"


def bearer(token: str) -> str:
    """The one place the `Authorization` value is written.

    The server check and every client that calls iris-core go through this, so a
    change to the scheme (a prefix, a signature) cannot be applied on one side
    and forgotten on the other. That drift is silent: the server rejects every
    request and each side looks correct on its own.
    """
    return f"Bearer {token}"


def auth_headers(token: str | None = None) -> dict[str, str]:
    """Headers a client attaches when calling iris-core (bridge, CLI, tools).

    `token` is for a client that resolved its own credential — the bridge reads
    `IRIS_API_TOKEN` from its own environment because it runs as a separate
    process. `None` falls back to settings; pass `""` to assert "no auth".
    """
    resolved = settings.iris_api_token if token is None else token
    return {"Authorization": bearer(resolved)} if resolved else {}


def require_token(request: Request) -> None:
    """FastAPI dependency. No-op unless a token is configured."""
    if not settings.iris_api_token:
        return
    auth = request.headers.get("authorization", "")
    if not secrets.compare_digest(auth, bearer(settings.iris_api_token)):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


def warn_if_unset() -> None:
    if not settings.iris_api_token:
        log.warning(
            "%s is not set — iris-core HTTP API is unauthenticated. "
            "Set it in .env to require a bearer token.",
            TOKEN_ENV,
        )


