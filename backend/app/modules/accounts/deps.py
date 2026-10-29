"""FastAPI dependencies for authentication and authorisation.

Design decisions:
- HTTPBearer(auto_error=False): we raise our own NotAuthenticated so the response
  uses the project's standard {"error": ...} envelope, not FastAPI's default 403.
- get_current_user reloads the user from the DB on every request so deactivation
  takes effect immediately without waiting for the token to expire.
- require() raises Forbidden (our app error) — not HTTPException — for the same reason.
"""

from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.db import DbSession
from app.core.errors import Forbidden, NotAuthenticated
from app.core.security import decode_token
from app.modules.accounts.models import User
from app.modules.accounts.permissions import permissions_for

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> User:
    """Decode the Bearer token and return the active user, or raise NotAuthenticated."""
    if credentials is None:
        raise NotAuthenticated("Missing bearer token")
    payload = decode_token(credentials.credentials, expected_type="access")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise NotAuthenticated("Inactive or unknown user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require(permission: str):
    """Dependency factory: inject the current user and raise 403 if they lack *permission*."""
    def _checker(user: CurrentUser) -> User:
        if permission not in permissions_for(user):
            raise Forbidden(f"Missing permission: {permission}")
        return user
    return Depends(_checker)
