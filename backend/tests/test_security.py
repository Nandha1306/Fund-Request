"""Tests for core/security.py — no database required."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import NotAuthenticated
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_round_trip(self):
        hashed = hash_password("MySecret1!")
        assert verify_password("MySecret1!", hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_empty_password_fails(self):
        hashed = hash_password("something")
        assert verify_password("", hashed) is False

    def test_different_hashes_same_password(self):
        """bcrypt generates a fresh salt each time."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        assert verify_password("same", h1)
        assert verify_password("same", h2)


class TestJWT:
    def test_access_token_round_trip(self):
        token = create_access_token(42)
        payload = decode_token(token, expected_type="access")
        assert payload["sub"] == "42"
        assert payload["type"] == "access"

    def test_refresh_token_round_trip(self):
        token = create_refresh_token(7)
        payload = decode_token(token, expected_type="refresh")
        assert payload["sub"] == "7"
        assert payload["type"] == "refresh"

    def test_expired_access_token_raises(self):
        import jwt

        from app.core.config import get_settings
        settings = get_settings()
        now = datetime.now(UTC)
        payload = {
            "sub": "1",
            "type": "access",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),  # already expired
        }
        token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        with pytest.raises(NotAuthenticated, match="expired"):
            decode_token(token, expected_type="access")

    def test_refresh_token_rejected_as_access(self):
        token = create_refresh_token(1)
        with pytest.raises(NotAuthenticated, match="refresh"):
            decode_token(token, expected_type="access")

    def test_access_token_rejected_as_refresh(self):
        token = create_access_token(1)
        with pytest.raises(NotAuthenticated, match="access"):
            decode_token(token, expected_type="refresh")

    def test_tampered_token_raises(self):
        token = create_access_token(1)
        tampered = token[:-4] + "XXXX"
        with pytest.raises(NotAuthenticated):
            decode_token(tampered, expected_type="access")
