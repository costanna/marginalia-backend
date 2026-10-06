import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"

# argon2-cffi defaults to Argon2id with sensible cost parameters.
_hasher = PasswordHasher()


class InvalidTokenError(Exception):
    """The token is malformed, tampered with, expired or has no valid subject."""


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A hash to verify against when the email is unknown, so "unknown email" and "wrong password"
    take about the same time and cannot be told apart by timing.

    Built on first use rather than at import time: an Argon2id hash costs real CPU, and importing
    this module must not tax everything that only wants `create_access_token` (the test suite, the
    migrations, ...). `lru_cache` keeps it a single hash per process, which is also what makes the
    timing comparison meaningful.
    """
    return _hasher.hash("marginalia-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password. Pass None when the user does not exist to keep timing uniform."""
    try:
        stored = password_hash or _dummy_hash()
        return _hasher.verify(stored, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: uuid.UUID, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    expires = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "exp": datetime.now(UTC) + expires}
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    """Return the user id in the token or raise InvalidTokenError."""
    try:
        payload = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=[JWT_ALGORITHM],  # pin the algorithm: never trust the token header
            options={"require": ["exp", "sub"]},
        )
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError from exc
