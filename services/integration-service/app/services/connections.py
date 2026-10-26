from uuid import UUID

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import Context
from app.connectors.registry import REGISTRY
from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.models.entities import Connection
from app.schemas.connections import ConnectionCreate
from app.services.google_auth import prepare_credentials, save_credentials
from app.services.mock_google import seed
from app.services.redaction import redact

CHECKLIST = "/api/v1/integrations/google/checklist"


def owned(
    db: Session, identifier: UUID | str, user: str, lock: bool = False
) -> Connection:
    query = select(Connection).where(
        Connection.id == str(identifier), Connection.user_id == user
    )
    if lock:
        query = query.with_for_update()
    connection = db.scalar(query)
    if connection is None:
        raise HTTPException(
            404, {"detail": "Connexion introuvable", "code": "not_found"}
        )
    return connection


def context(db: Session, connection: Connection, http: httpx.Client) -> Context:
    if get_settings().effective_mock and not connection.is_mock:
        raise HTTPException(
            409,
            {
                "detail": (
                    "Le mode démonstration interdit les actions sur une "
                    "connexion réelle"
                ),
                "code": "mode_mismatch",
            },
        )
    if connection.status == "expired":
        raise HTTPException(
            409,
            {
                "detail": "Connexion expirée, reconnecter",
                "code": "connection_expired",
                "help_url": CHECKLIST,
            },
        )
    try:
        credentials = decrypt(connection.encrypted_credentials)
    except InvalidToken:
        raise HTTPException(
            503,
            {
                "detail": (
                    "Clé de chiffrement incompatible avec les connexions "
                    "enregistrées"
                ),
                "code": "credentials_unreadable",
            },
        ) from None
    ctx = Context(db, connection, credentials, http, get_settings())
    if connection.type == "google":
        prepare_credentials(ctx)
    return ctx


def perform(db: Session, connection: Connection, http: httpx.Client, operation) -> dict:
    ctx = context(db, connection, http)
    connector = REGISTRY[connection.type](ctx)
    try:
        result = operation(connector)
        if connection.type == "google":
            save_credentials(ctx)
        secrets = dict(ctx.credentials)
        if ctx.google_credentials is not None:
            secrets.update(
                token=ctx.google_credentials.token,
                refresh_token=ctx.google_credentials.refresh_token,
            )
        result = redact(result, secrets)
        connection.status = "error" if result.get("ok") is False else "active"
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except HttpError as error:
        db.rollback()
        status = int(error.resp.status)
        connection.status = "expired" if status == 401 else "error"
        db.commit()
        raise HTTPException(
            409 if status == 401 else 502,
            {
                "detail": (
                    "Connexion expirée, reconnecter"
                    if status == 401
                    else "Google a refusé l’action ; vérifier les API et autorisations"
                ),
                "code": "connection_expired" if status == 401 else "google_api_error",
                "help_url": CHECKLIST,
            },
        ) from None
    except Exception:
        db.rollback()
        connection.status = "error"
        db.commit()
        raise HTTPException(
            502,
            {
                "detail": "Le connecteur ne répond pas ou a refusé les identifiants",
                "code": "connector_error",
            },
        ) from None


def create(
    db: Session, user: str, body: ConnectionCreate, http: httpx.Client
) -> Connection:
    settings = get_settings()
    credentials = {
        key: value.get_secret_value() for key, value in body.credentials.items()
    }
    connector_class = REGISTRY[body.type]
    if set(credentials) - connector_class.credential_fields or any(
        len(value) > 4096 for value in credentials.values()
    ):
        raise HTTPException(
            422,
            {
                "detail": "Champs d’identifiants invalides",
                "code": "invalid_credentials_fields",
            },
        )
    mock = settings.effective_mock
    if body.type == "google" and not mock:
        raise HTTPException(
            409,
            {
                "detail": "Utiliser la connexion OAuth Google",
                "code": "oauth_required",
                "help_url": CHECKLIST,
            },
        )
    if (
        body.type == "telegram"
        and not credentials.get("token")
        and settings.telegram_token
    ):
        credentials["token"] = settings.telegram_token
    if (
        body.type == "gmail_smtp"
        and not credentials.get("app_password")
        and settings.gmail_app_password
    ):
        credentials["app_password"] = settings.gmail_app_password
    required = connector_class.always_required_credentials | (
        connector_class.required_credentials if not mock else set()
    )
    if any(not credentials.get(key) for key in required):
        raise HTTPException(
            422,
            {"detail": "Identifiants requis absents", "code": "missing_credentials"},
        )
    connection = Connection(
        user_id=user,
        type=body.type,
        name=body.name,
        encrypted_credentials=encrypt(credentials),
        is_mock=mock,
        poll_config=body.poll_config.model_dump(mode="json"),
    )
    db.add(connection)
    db.flush()
    if mock and body.type == "google":
        seed(REGISTRY["google"](Context(db, connection, credentials, http, settings)))
    if mock and body.type == "gmail_smtp":
        from app.models.entities import Artifact

        db.add(
            Artifact(
                connection_id=connection.id,
                kind="gmail_message",
                data={
                    "id": "smtp-demo-mail",
                    "subject": "Facture",
                    "text": "Facture de démonstration",
                    "from": "supplier@example.com",
                    "attachments": [],
                },
            )
        )
    db.commit()
    db.refresh(connection)
    return connection
