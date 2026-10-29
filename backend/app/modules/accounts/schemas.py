"""Pydantic schemas for authentication endpoints."""

import re
from typing import Any

from pydantic import BaseModel, field_validator

from app.core.security import PASSWORD_MAX_BYTES

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


class LoginInput(BaseModel):
    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("email must be a string")
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email format")
        return v

    @field_validator("password", mode="before")
    @classmethod
    def validate_password(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("password must be a string")
        if not v:
            raise ValueError("password is required")
        if len(v.encode()) > PASSWORD_MAX_BYTES:
            raise ValueError(f"password must be at most {PASSWORD_MAX_BYTES} bytes")
        return v


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
