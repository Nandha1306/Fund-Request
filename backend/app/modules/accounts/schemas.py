"""Pydantic schemas for authentication, users and invitations."""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

from app.core.enums import Role
from app.core.security import PASSWORD_MAX_BYTES, PASSWORD_MIN_BYTES

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


# ---------------------------------------------------------------------------
# Shared validators (used by LoginInput, InviteCreate and AcceptInvite)
# ---------------------------------------------------------------------------

def normalise_email(value: Any) -> str:
    """Strip, lower-case and shape-check an email address."""
    if not isinstance(value, str):
        raise ValueError("email must be a string")
    email = value.strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("invalid email format")
    return email


def validate_new_password(value: Any) -> str:
    """A password being created (accept-invite) must be 8-72 bytes: bcrypt limit is 72."""
    if not isinstance(value, str):
        raise ValueError("password must be a string")
    size = len(value.encode())
    if size < PASSWORD_MIN_BYTES or size > PASSWORD_MAX_BYTES:
        raise ValueError(f"password must be between {PASSWORD_MIN_BYTES} and {PASSWORD_MAX_BYTES} bytes")
    return value


def validate_login_password(value: Any) -> str:
    """On login the length rule is not enforced (legacy hashes), but bcrypt must not crash."""
    if not isinstance(value, str) or not value:
        raise ValueError("password is required")
    if len(value.encode()) > PASSWORD_MAX_BYTES:
        raise ValueError(f"password must be at most {PASSWORD_MAX_BYTES} bytes")
    return value


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginInput(BaseModel):
    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: Any) -> str:
        return normalise_email(v)

    @field_validator("password", mode="before")
    @classmethod
    def _password(cls, v: Any) -> str:
        return validate_login_password(v)


class RefreshInput(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    permissions: list[str]

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: UserOut


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

class InviteCreate(BaseModel):
    email: str
    role: Role

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: Any) -> str:
        return normalise_email(v)


class InviteOut(BaseModel):
    """Create response only — ``invite_link`` is shown once and never stored."""

    id: int
    email: str
    role: str
    expires_at: datetime
    invite_link: str


class InvitationOut(BaseModel):
    """List response — no link, because only the SHA-256 of the token is stored."""

    id: int
    email: str
    role: str
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class AcceptInvite(BaseModel):
    token: str
    full_name: str
    password: str

    @field_validator("token", mode="before")
    @classmethod
    def _token(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("token is required")
        return v.strip()

    @field_validator("full_name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("full_name must be a string")
        name = v.strip()
        if not 1 <= len(name) <= 255:
            raise ValueError("full_name must be 1-255 characters")
        return name

    @field_validator("password", mode="before")
    @classmethod
    def _password(cls, v: Any) -> str:
        return validate_new_password(v)


# ---------------------------------------------------------------------------
# User administration
# ---------------------------------------------------------------------------

class UserAdminOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    can_finance_ops: bool
    permissions: list[str]
    created_at: datetime


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    can_finance_ops: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_nulls(cls, data: Any) -> Any:
        """Omit a field to leave it unchanged; ``null`` is not a valid value here."""
        if isinstance(data, dict):
            for field in ("role", "is_active", "can_finance_ops"):
                if field in data and data[field] is None:
                    raise ValueError(f"{field} cannot be null; omit it to leave it unchanged")
        return data


class UserBrief(BaseModel):
    id: int
    full_name: str
    email: str

    model_config = {"from_attributes": True}


class UpdateUserResponse(BaseModel):
    user: UserAdminOut
    needs_reassignment: list[str] = []
