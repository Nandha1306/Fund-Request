"""Password hashing and JWT helpers.

Design decisions (from architecture doc §12):
- bcrypt 5 raises ValueError for passwords > 72 bytes; callers must validate length first.
  PASSWORD_MAX_BYTES is exported so schemas can enforce the limit.
- Access tokens expire in jwt_access_minutes (default 30); refresh tokens in jwt_refresh_days (default 7).
- Payload: {"sub": "<user_id>", "type": "access"|"refresh", "iat": <int>, "exp": <int>}.
- decode_token raises NotAuthenticated (our error class) — not HTTPException — so our error
  handler formats the response in the project's standard {"error": ...} envelope.
"""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import NotAuthenticated

PASSWORD_MAX_BYTES = 72  # bcrypt hard limit; enforced in request schemas
PASSWORD_MIN_BYTES = 8  # only enforced when a password is being created


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*.  Caller must ensure len(plain.encode()) <= 72."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff *plain* matches the stored *hashed* password."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _settings():
    return get_settings()


def create_access_token(user_id: int) -> str:
    settings = _settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_refresh_token(user_id: int) -> str:
    settings = _settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_refresh_days)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, expected_type: str) -> dict:
    """Decode and validate a JWT.

    Raises NotAuthenticated when the token is expired, has a bad signature,
    is malformed, or carries a different type than *expected_type*.
    """
    settings = _settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise NotAuthenticated("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise NotAuthenticated("Invalid token") from exc

    if payload.get("type") != expected_type:
        raise NotAuthenticated(f"Expected {expected_type!r} token, got {payload.get('type')!r}")

    return payload
