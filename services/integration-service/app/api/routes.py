from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.connectors.base import Context
from app.connectors.http import HTTPConnector
from app.connectors.registry import REGISTRY
from app.core.config import get_settings
from app.core.crypto import decrypt
from app.core.database import get_db
from app.core.security import current_user
from app.models.entities import Artifact, Connection, PollCursor
from app.schemas.actions import HttpRequest
from app.schemas.connections import (
    ActionInput,
    ConnectionCreate,
    ConnectionOutput,
    TelegramConnect,
)
from app.services import oauth
from app.services.connections import create, owned, perform
from app.services.google_auth import SCOPES
from app.services.polling import poll_connection
from app.services.redaction import redact

router = APIRouter(tags=["Connexions et intégrations"])


@router.get("/integrations/status")
def status(user: str = Depends(current_user)):
    settings = get_settings()
    return {
        "mock_integrations": settings.effective_mock,
        "google_configured": bool(
            settings.google_client_id and settings.google_client_secret
        ),
        "warning": (
            "MODE DÉMONSTRATION : aucune action externe n’est effectuée"
            if settings.effective_mock
            else None
        ),
    }


@router.get("/integrations/connectors")
def catalogue(user: str = Depends(current_user)):
    return [
        {
            "key": key,
            "actions": {
                action: model.model_json_schema()
                for action, model in cls.actions.items()
            },
            "triggers": cls.triggers,
            "credential_fields": sorted(cls.credential_fields),
            "required_credentials": sorted(cls.required_credentials),
            "always_required_credentials": sorted(cls.always_required_credentials),
        }
        for key, cls in sorted(REGISTRY.items())
    ]


@router.get("/connections", response_model=list[ConnectionOutput])
def connections(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(Connection)
        .where(Connection.user_id == user)
        .order_by(Connection.created_at.desc(), Connection.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("/connections", response_model=ConnectionOutput, status_code=201)
def new(
    body: ConnectionCreate,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return create(db, user, body, request.app.state.http)


@router.get("/connections/{identifier}", response_model=ConnectionOutput)
def detail(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    return owned(db, identifier, user)


@router.post("/connections/{identifier}/test")
def test(
    identifier: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    connection = owned(db, identifier, user, lock=True)
    return perform(
        db,
        connection,
        request.app.state.http,
        lambda connector: connector.test_connection(),
    )


@router.post("/connections/{identifier}/actions/{action}")
def action(
    identifier: UUID,
    action: str,
    body: ActionInput,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    connection = owned(db, identifier, user, lock=True)
    return perform(
        db,
        connection,
        request.app.state.http,
        lambda connector: connector.run(action, body.params),
    )


@router.get("/connections/{identifier}/outbox")
def outbox(
    identifier: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    connection = owned(db, identifier, user)
    if not connection.is_mock:
        raise HTTPException(
            409,
            {
                "detail": "Historique simulé disponible uniquement en démonstration",
                "code": "mock_only",
            },
        )
    records = db.scalars(
        select(Artifact)
        .where(Artifact.connection_id == connection.id, Artifact.kind == "outbox")
        .order_by(Artifact.created_at.desc(), Artifact.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            "id": record.id,
            "action": redact(record.data, decrypt(connection.encrypted_credentials)),
            "created_at": record.created_at,
        }
        for record in records
    ]


@router.post("/connections/{identifier}/poll")
def poll(
    identifier: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    connection = owned(db, identifier, user, lock=True)
    if not REGISTRY[connection.type].triggers:
        raise HTTPException(
            422,
            {
                "detail": "Ce connecteur ne reçoit pas d’événements",
                "code": "no_trigger",
            },
        )
    return poll_connection(
        db, connection, request.app.state.http, request.app.state.redis
    )


@router.delete("/connections/{identifier}", status_code=204)
def revoke(
    identifier: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    connection = owned(db, identifier, user, lock=True)
    if connection.type == "google" and not connection.is_mock:
        if get_settings().effective_mock:
            raise HTTPException(
                409,
                {
                    "detail": (
                        "Réactiver le mode réel avant de révoquer une "
                        "connexion Google réelle"
                    ),
                    "code": "mode_mismatch",
                },
            )
        credentials = decrypt(connection.encrypted_credentials)
        try:
            response = request.app.state.http.post(
                "https://oauth2.googleapis.com/revoke",
                data={
                    "token": credentials.get("refresh_token")
                    or credentials.get("token")
                },
                timeout=10,
            )
            if response.status_code not in {200, 400}:
                response.raise_for_status()
        except Exception:
            raise HTTPException(
                502,
                {
                    "detail": "Révocation Google indisponible ; réessayer",
                    "code": "revoke_failed",
                },
            ) from None
    db.execute(delete(Artifact).where(Artifact.connection_id == connection.id))
    db.execute(delete(PollCursor).where(PollCursor.connection_id == connection.id))
    db.delete(connection)
    db.commit()
    return Response(status_code=204)


@router.post(
    "/integrations/telegram/connect", response_model=ConnectionOutput, status_code=201
)
def telegram(
    body: TelegramConnect,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    values = {"token": body.token} if body.token else {}
    connection = create(
        db,
        user,
        ConnectionCreate(type="telegram", name=body.name, credentials=values),
        request.app.state.http,
    )
    perform(
        db,
        connection,
        request.app.state.http,
        lambda connector: connector.test_connection(),
    )
    db.refresh(connection)
    return connection


@router.get("/integrations/google/oauth/start")
def google_start(
    request: Request,
    response: Response,
    connection_id: UUID | None = None,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return oauth.start(db, user, request, connection_id, response)


@router.get("/integrations/google/oauth/callback", response_model=ConnectionOutput)
def google_callback(
    request: Request,
    response: Response,
    state: str | None = Query(default=None, max_length=512),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
):
    return oauth.callback(db, request, state, code, error, response)


@router.get("/integrations/google/checklist")
def checklist():
    return {
        "items": oauth.CHECKLIST_ITEMS,
        "redirect_uri": get_settings().google_redirect_uri,
        "scopes": SCOPES,
        "label_limitation": (
            "Ajouter un libellé Gmail exige gmail.modify ; ce scope "
            "n’est pas demandé"
        ),
    }


# Convenience routes use the same owner checks and connector dispatch as actions.
def google_action(identifier, user, db, request, action, params):
    connection = owned(db, identifier, user, lock=True)
    if connection.type != "google":
        raise HTTPException(
            422,
            {"detail": "Connexion Google requise", "code": "connection_type_mismatch"},
        )
    return perform(
        db,
        connection,
        request.app.state.http,
        lambda connector: connector.run(action, params),
    )


@router.get("/integrations/sheets/{connection_id}/spreadsheets")
def spreadsheets(
    connection_id: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return google_action(connection_id, user, db, request, "sheets_list", {})


@router.get("/integrations/sheets/{connection_id}/spreadsheets/{sid}/sheets")
def tabs(
    connection_id: UUID,
    sid: str,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return google_action(
        connection_id, user, db, request, "sheets_tabs", {"spreadsheet_id": sid}
    )


@router.get("/integrations/gmail/{connection_id}/labels")
def labels(
    connection_id: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return google_action(connection_id, user, db, request, "gmail_labels", {})


@router.post("/integrations/http/request")
def public_http(
    body: HttpRequest,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not body.url:
        raise HTTPException(422, {"detail": "URL requise", "code": "invalid_url"})
    connection = Connection(
        id="",
        user_id=user,
        type="http",
        name="HTTP public",
        is_mock=get_settings().effective_mock,
    )
    ctx = Context(
        db, connection, {"base_url": body.url}, request.app.state.http, get_settings()
    )
    try:
        return HTTPConnector(ctx).run(
            "request", body.model_dump(mode="json", exclude_none=True)
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            502, {"detail": "Serveur HTTP inaccessible", "code": "http_unavailable"}
        ) from None
