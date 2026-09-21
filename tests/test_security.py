import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    JWT_ALGORITHM,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_is_argon2id_and_salted() -> None:
    first = hash_password("correct-horse-battery")
    second = hash_password("correct-horse-battery")

    assert first.startswith("$argon2id$")
    assert first != second  # random salt


def test_verify_password() -> None:
    hashed = hash_password("correct-horse-battery")

    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("correct-horse-battery", "not-a-valid-hash")


def test_verify_password_is_false_when_the_user_does_not_exist() -> None:
    assert not verify_password("anything", None)


def test_token_round_trip() -> None:
    user_id = uuid.uuid4()

    assert decode_access_token(create_access_token(user_id)) == user_id


def test_expired_token_is_rejected() -> None:
    token = create_access_token(uuid.uuid4(), timedelta(seconds=-1))

    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_token_signed_with_another_key_is_rejected() -> None:
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "another-secret-key-that-is-also-long-enough-to-sign",
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(forged)


def test_unsigned_token_is_rejected() -> None:
    """The classic `alg: none` attack must not work."""
    unsigned = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        key=None,
        algorithm="none",
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(unsigned)


def test_token_without_a_valid_subject_is_rejected() -> None:
    secret = get_settings().secret_key
    exp = datetime.now(UTC) + timedelta(minutes=5)
    no_subject = jwt.encode({"exp": exp}, secret, algorithm=JWT_ALGORITHM)
    bad_subject = jwt.encode({"sub": "not-a-uuid", "exp": exp}, secret, algorithm=JWT_ALGORITHM)

    for token in (no_subject, bad_subject):
        with pytest.raises(InvalidTokenError):
            decode_access_token(token)
