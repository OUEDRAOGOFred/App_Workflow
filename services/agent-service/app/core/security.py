from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    settings = get_settings()
    try:
        if credentials is None:
            raise jwt.InvalidTokenError()
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "type", "exp", "iat", "jti"]},
        )
        if claims["type"] != "access":
            raise jwt.InvalidTokenError()
        return str(UUID(claims["sub"]))
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(
            401,
            {"detail": "Session invalide ou expirée", "code": "invalid_token"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def service_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    settings = get_settings()
    try:
        if credentials is None:
            raise jwt.InvalidTokenError()
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "type", "service", "exp", "iat", "jti"]},
        )
        if claims["type"] != "service" or claims["service"] != "execution-service":
            raise jwt.InvalidTokenError()
        UUID(claims["sub"])
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(
            403, {"detail": "Accès interservice refusé", "code": "service_forbidden"}
        ) from None
