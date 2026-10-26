import hashlib
import secrets
from uuid import UUID

from fastapi import HTTPException
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import InvalidClientError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.models.entities import Connection
from app.schemas.connections import ConnectionCreate, ConnectionOutput
from app.services.connections import CHECKLIST, create, owned
from app.services.google_auth import SCOPES

CHECKLIST_ITEMS = [
    "Activer Gmail API, Google Sheets API et Google Drive API",
    "Configurer le client comme Application Web",
    "Ajouter exactement GOOGLE_REDIRECT_URI aux URI autorisées",
    "Ajouter les quatre scopes dans Accès aux données",
    "Ajouter GOOGLE_TEST_USER_EMAIL aux utilisateurs de test dans Audience",
]


def flow(state: str, verifier: str) -> Flow:
    settings = get_settings()
    config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        config,
        scopes=SCOPES,
        state=state,
        code_verifier=verifier,
        redirect_uri=settings.google_redirect_uri,
    )


def start(db, user: str, request, connection_id: UUID | None, response) -> dict:
    settings = get_settings()
    existing = owned(db, connection_id, user) if connection_id else None
    if existing and existing.type != "google":
        raise HTTPException(
            422,
            {"detail": "Connexion Google attendue", "code": "connection_type_mismatch"},
        )
    if settings.effective_mock:
        connection = existing or db.scalar(
            select(Connection).where(
                Connection.user_id == user,
                Connection.type == "google",
                Connection.is_mock.is_(True),
            )
        )
        if connection is not None and not connection.is_mock:
            raise HTTPException(
                409,
                {
                    "detail": "Créer une connexion de démonstration distincte",
                    "code": "mode_mismatch",
                },
            )
        if connection is None:
            connection = create(
                db,
                user,
                ConnectionCreate(type="google", name="Google de démonstration"),
                request.app.state.http,
            )
        connection.status = "active"
        db.commit()
        db.refresh(connection)
        return {
            "mock": True,
            "authorization_url": None,
            "connection": ConnectionOutput.model_validate(connection).model_dump(
                mode="json"
            ),
        }
    if existing and existing.is_mock:
        raise HTTPException(
            409,
            {
                "detail": (
                    "Créer une connexion OAuth distincte du compte de " "démonstration"
                ),
                "code": "mode_mismatch",
            },
        )
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    binding = secrets.token_urlsafe(32)
    authorization, _ = flow(state, verifier).authorization_url(
        access_type="offline", prompt="consent", state=state
    )
    payload = {
        "user_id": user,
        "verifier": verifier,
        "connection_id": str(connection_id) if connection_id else None,
        "binding": hashlib.sha256(binding.encode()).hexdigest(),
    }
    request.app.state.redis.setex(f"oauth:google:{state}", 600, encrypt(payload))
    response.set_cookie(
        "workflow_oauth_binding",
        binding,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.google_redirect_uri.startswith("https://"),
    )
    return {"authorization_url": authorization, "mock": False}


def callback(
    db, request, state: str | None, code: str | None, error: str | None, response
) -> Connection:
    if get_settings().effective_mock:
        raise HTTPException(
            409,
            {
                "detail": "Callback OAuth réel désactivé en démonstration",
                "code": "mode_mismatch",
            },
        )
    if not state:
        raise HTTPException(
            400,
            {
                "detail": "État OAuth manquant ou expiré",
                "code": "invalid_oauth_state",
                "help_url": CHECKLIST,
            },
        )
    stored = request.app.state.redis.getdel(f"oauth:google:{state}")
    if not stored:
        raise HTTPException(
            400,
            {
                "detail": "État OAuth absent, expiré ou déjà utilisé",
                "code": "invalid_oauth_state",
                "help_url": CHECKLIST,
            },
        )
    try:
        data = decrypt(stored.decode() if isinstance(stored, bytes) else stored)
    except Exception:
        raise HTTPException(
            400,
            {
                "detail": "État OAuth invalide",
                "code": "invalid_oauth_state",
                "help_url": CHECKLIST,
            },
        ) from None
    binding = request.cookies.get("workflow_oauth_binding", "")
    if not binding or not secrets.compare_digest(
        hashlib.sha256(binding.encode()).hexdigest(), data.get("binding", "")
    ):
        raise HTTPException(
            400,
            {
                "detail": "Reprendre OAuth dans le navigateur d’origine",
                "code": "invalid_oauth_browser",
                "help_url": CHECKLIST,
            },
        )
    if error:
        message = {
            "redirect_uri_mismatch": "URI différente de celle du client Google",
            "access_denied": (
                "Accès refusé : vérifier le consentement et " "l’utilisateur de test"
            ),
        }.get(error, "Google a refusé l’autorisation")
        raise HTTPException(
            400,
            {
                "detail": message,
                "code": (
                    error
                    if error in {"redirect_uri_mismatch", "access_denied"}
                    else "oauth_error"
                ),
                "help_url": CHECKLIST,
            },
        )
    if not code:
        raise HTTPException(
            400,
            {
                "detail": "Code OAuth manquant",
                "code": "oauth_error",
                "help_url": CHECKLIST,
            },
        )
    connection = (
        owned(db, data["connection_id"], data["user_id"], lock=True)
        if data.get("connection_id")
        else None
    )
    try:
        oauth = flow(state, data["verifier"])
        oauth.fetch_token(code=code, timeout=15)
        credentials = oauth.credentials
        if not credentials.refresh_token:
            raise ValueError("Refresh token absent")
        encrypted = encrypt(
            {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "expiry": (
                    credentials.expiry.isoformat() if credentials.expiry else None
                ),
            }
        )
    except InvalidClientError:
        raise HTTPException(
            400,
            {
                "detail": (
                    "Google refuse les identifiants OAuth ; vérifier l’ID client "
                    "et le secret du même client Web dans la configuration locale"
                ),
                "code": "oauth_invalid_client",
                "help_url": CHECKLIST,
            },
        ) from None
    except Exception:
        raise HTTPException(
            400,
            {
                "detail": (
                    "Échange OAuth impossible ; réessayer avec "
                    "consentement hors ligne et vérifier la checklist"
                ),
                "code": "oauth_exchange_failed",
                "help_url": CHECKLIST,
            },
        ) from None
    if connection is None:
        connection = Connection(
            user_id=data["user_id"],
            type="google",
            name="Compte Google",
            is_mock=False,
            poll_config={},
        )
        db.add(connection)
    connection.encrypted_credentials = encrypted
    connection.status = "active"
    db.commit()
    db.refresh(connection)
    response.delete_cookie(
        "workflow_oauth_binding",
        httponly=True,
        samesite="lax",
        secure=get_settings().google_redirect_uri.startswith("https://"),
    )
    return connection
