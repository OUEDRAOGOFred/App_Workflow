from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.core.config import get_settings


def access_token(user: str, service: bool = False, scope: str = "") -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": user,
        "type": "service" if service else "access",
        "service": "execution-service",
        "scope": scope,
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=10),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
