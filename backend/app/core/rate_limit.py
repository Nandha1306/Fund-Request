"""Centralised rate-limiter instance.

Kept in its own module to avoid circular imports between main.py and the
accounts router (both need the limiter, and the router is imported by main).

The limiter reads `rate_limit_enabled` from settings, so tests can flip it off
via the `app` fixture without touching production config.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import get_settings


def _make_limiter() -> Limiter:
    settings = get_settings()
    return Limiter(
        key_func=get_remote_address,
        enabled=settings.rate_limit_enabled,
    )


limiter = _make_limiter()


def rate_limit_exceeded(_: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return 429 in the project's standard error envelope."""
    return JSONResponse(
        status_code=429,
        content={"error": {"code": "rate_limited", "message": str(exc.detail), "details": None}},
    )
