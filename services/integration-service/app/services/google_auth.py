from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.connectors.base import Context
from app.core.crypto import encrypt

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]


class TimedRequest(Request):
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 10
        return super().__call__(*args, **kwargs)


def save_credentials(ctx: Context) -> None:
    credentials = ctx.google_credentials
    if credentials is None:
        return
    data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "expiry": (
            credentials.expiry.replace(tzinfo=UTC).isoformat()
            if credentials.expiry
            else None
        ),
    }
    ctx.connection.encrypted_credentials = encrypt(data)


def prepare_credentials(ctx: Context) -> None:
    if ctx.connection.is_mock:
        return
    data = ctx.credentials
    expiry = (
        datetime.fromisoformat(data["expiry"]).replace(tzinfo=None)
        if data.get("expiry")
        else None
    )
    credentials = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=ctx.settings.google_client_id,
        client_secret=ctx.settings.google_client_secret,
        scopes=SCOPES,
        expiry=expiry,
    )
    if (
        not credentials.token
        or not expiry
        or expiry <= datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=60)
    ):
        try:
            credentials.refresh(TimedRequest())
        except Exception:
            ctx.connection.status = "expired"
            ctx.db.commit()
            raise HTTPException(
                409,
                {
                    "detail": "Connexion expirée, reconnecter",
                    "code": "connection_expired",
                    "help_url": "/api/v1/integrations/google/checklist",
                },
            ) from None
    ctx.google_credentials = credentials
    save_credentials(ctx)
