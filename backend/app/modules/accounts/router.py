"""Auth and /me endpoints."""

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import NotAuthenticated
from app.core.rate_limit import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.accounts.deps import CurrentUser
from app.modules.accounts.models import User
from app.modules.accounts.permissions import permissions_for
from app.modules.accounts.schemas import LoginInput, RefreshInput, TokenResponse, UserOut

router = APIRouter(tags=["auth"])

# A dummy hash used when the email doesn't exist, so response time doesn't
# reveal whether an account is registered (timing-safe login).
_DUMMY_HASH = hash_password("dummy-constant-value-for-timing-safety")


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        permissions=sorted(permissions_for(user)),
    )


@router.post("/auth/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, body: LoginInput, db: DbSession) -> TokenResponse:
    """Authenticate with email + password and return JWTs.

    Unknown email, wrong password, and inactive account all return the
    same 401 to avoid revealing which accounts exist.
    """
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()

    # Always run the password check to keep response time constant.
    candidate_hash = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(body.password, candidate_hash)

    if not password_ok or user is None or not user.is_active:
        raise NotAuthenticated("Invalid email or password")

    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        expires_in=settings.jwt_access_minutes * 60,
        user=_user_out(user),
    )


@router.post("/auth/refresh", response_model=dict)
def refresh(body: RefreshInput, db: DbSession) -> dict:
    """Exchange a valid refresh token for a new access token."""
    payload = decode_token(body.refresh_token, expected_type="refresh")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise NotAuthenticated("Inactive or unknown user")
    return {
        "access_token": create_access_token(user.id),
        "token_type": "bearer",
        "expires_in": get_settings().jwt_access_minutes * 60,
    }


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """Return the authenticated user's profile and permission list."""
    return _user_out(user)
