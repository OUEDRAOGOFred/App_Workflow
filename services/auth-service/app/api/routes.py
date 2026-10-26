from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token, hasher, unauthorized
from app.models.user import User
from app.schemas.auth import Credentials, RefreshInput, TokenPair, UserOutput
from app.services.auth import issue_pair, rotate_pair

router = APIRouter(prefix="/auth", tags=["Authentification"])
bearer = HTTPBearer(auto_error=False)
# Comparable work for nonexistent users reduces account enumeration by timing.
_dummy_hash = hasher.hash("dummy-password-never-used-for-login")


@router.post("/register", response_model=UserOutput, status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    user = User(
        email=str(body.email),
        password_hash=hasher.hash(body.password.get_secret_value()),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, {"detail": "Cette adresse est déjà inscrite", "code": "email_exists"}
        ) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(body.email)))
    try:
        hasher.verify(
            user.password_hash if user else _dummy_hash,
            body.password.get_secret_value(),
        )
    except VerificationError:
        raise unauthorized() from None
    if not user:
        raise unauthorized()
    return issue_pair(db, user.id)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshInput, db: Session = Depends(get_db)):
    return rotate_pair(db, body.refresh_token.get_secret_value())


@router.get("/me", response_model=UserOutput)
def me(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise unauthorized()
    claims = decode_token(credentials.credentials, "access")
    user = db.get(User, claims["sub"])
    if not user:
        raise unauthorized()
    return user
