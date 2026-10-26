import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.config import get_settings
from app.core.security import decode_token, encode_token, unauthorized
from app.models.user import RefreshSession
from app.schemas.auth import TokenPair


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def issue_pair(db, user_id: str):
    settings = get_settings()
    access, _ = encode_token(
        user_id, "access", timedelta(minutes=settings.access_token_minutes)
    )
    refresh, claims = encode_token(
        user_id, "refresh", timedelta(days=settings.refresh_token_days)
    )
    db.add(
        RefreshSession(
            token_hash=digest(claims["jti"]), user_id=user_id, expires_at=claims["exp"]
        )
    )
    # Expired sessions are garbage-collected during token issuance.
    db.execute(
        delete(RefreshSession).where(RefreshSession.expires_at < datetime.now(UTC))
    )
    db.commit()
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
    )


def rotate_pair(db, token: str):
    claims = decode_token(token, "refresh")
    # Atomic DELETE prevents two requests from consuming the same refresh token.
    removed = db.execute(
        delete(RefreshSession).where(
            RefreshSession.token_hash == digest(claims["jti"]),
            RefreshSession.user_id == claims["sub"],
            RefreshSession.expires_at > datetime.now(UTC),
        )
    )
    if removed.rowcount != 1:
        db.rollback()
        raise unauthorized()
    return issue_pair(db, claims["sub"])
