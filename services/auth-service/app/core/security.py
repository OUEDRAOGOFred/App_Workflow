import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from fastapi import HTTPException

from app.core.config import get_settings

hasher = PasswordHasher()


def unauthorized():
    return HTTPException(
        401,
        {"detail": "Identifiants ou session invalides", "code": "invalid_credentials"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def encode_token(user_id: str, kind: str, duration: timedelta):
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": user_id,
        "type": kind,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + duration,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256"), claims


def decode_token(token: str, kind: str):
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "type", "exp", "iat", "jti"]},
        )
        if payload["type"] != kind:
            raise unauthorized()
        return payload
    except jwt.InvalidTokenError:
        raise unauthorized() from None
