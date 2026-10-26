import hashlib
import time

import jwt
from fastapi import HTTPException, Request
from redis.exceptions import RedisError

from app.core.config import get_settings

# Increment and expiry are one atomic operation, including concurrent requests.
LIMIT_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], 61) end
return n
"""


async def rate_limit(request: Request, sensitive: bool = False):
    settings = get_settings()
    address = request.client.host if request.client else "unknown"
    # Trust the TCP peer, never arbitrary X-Forwarded-For headers.
    address_hash = hashlib.sha256(address.encode()).hexdigest()
    bucket = "auth" if sensitive else "api"
    key = f"limit:{bucket}:{address_hash}:{int(time.time()) // 60}"
    limit = (
        settings.auth_rate_limit_per_minute
        if sensitive
        else settings.rate_limit_per_minute
    )
    try:
        count = await request.app.state.redis.eval(LIMIT_SCRIPT, 1, key)
    except RedisError:
        raise HTTPException(
            503, {"detail": "Limiteur indisponible", "code": "rate_limit_unavailable"}
        ) from None
    if count > limit:
        raise HTTPException(
            429,
            {"detail": "Trop de requêtes", "code": "rate_limited"},
            headers={"Retry-After": "60"},
        )


def verify_access(request: Request):
    settings = get_settings()
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token:
            raise jwt.InvalidTokenError()
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "type", "iat", "exp", "jti"]},
        )
        if claims["type"] != "access":
            raise jwt.InvalidTokenError()
        return claims
    except jwt.InvalidTokenError:
        raise HTTPException(
            401,
            {"detail": "Session invalide ou expirée", "code": "invalid_token"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
