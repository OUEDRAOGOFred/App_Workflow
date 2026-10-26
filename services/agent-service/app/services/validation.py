import httpx
from fastapi import HTTPException, Request
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from app.models.entities import Agent, AgentType

SECRET_FIELDS = {
    "token",
    "telegram_token",
    "password",
    "client_secret",
    "api_key",
    "access_token",
    "refresh_token",
    "authorization",
    "encrypted_credentials",
}


def reject_secrets(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SECRET_FIELDS:
                raise HTTPException(
                    422,
                    {
                        "detail": "Stocker les secrets dans une connexion",
                        "code": "secret_in_config",
                    },
                )
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)


def validate_config(db: Session, type_key: str, config: dict) -> AgentType:
    kind = db.get(AgentType, type_key)
    if kind is None:
        raise HTTPException(
            422, {"detail": "Type d’agent inconnu", "code": "unknown_agent_type"}
        )
    reject_secrets(config)
    errors = list(Draft202012Validator(kind.config_schema).iter_errors(config))
    if errors:
        fields = sorted(
            {".".join(map(str, e.absolute_path)) or "config" for e in errors}
        )
        raise HTTPException(
            422,
            {
                "detail": "Configuration incompatible avec le schéma du type",
                "code": "invalid_agent_config",
                "fields": fields,
            },
        )
    return kind


def validate_activation(db: Session, agent: Agent) -> None:
    kind = validate_config(db, agent.type_key, agent.config)
    if kind.requires_connection and not agent.connection_id:
        raise HTTPException(
            422,
            {
                "detail": "Une connexion est requise pour activer cet agent",
                "code": "connection_required",
            },
        )


def validate_connection(
    type_key: str,
    connection_id: str | None,
    request: Request,
    require_active: bool = False,
) -> None:
    if not connection_id:
        return
    try:
        response = request.app.state.integrations.get(
            f"/connections/{connection_id}",
            headers={"Authorization": request.headers["authorization"]},
        )
        if response.status_code == 404:
            raise HTTPException(
                422,
                {
                    "detail": "Connexion absente ou inaccessible",
                    "code": "invalid_connection",
                },
            )
        response.raise_for_status()
        connection = response.json()
        if not isinstance(connection, dict) or not {"type", "status"}.issubset(
            connection
        ):
            raise ValueError("Invalid connection response")
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            503,
            {
                "detail": "Impossible de vérifier la connexion",
                "code": "integration_service_unavailable",
            },
        ) from None
    expected = {
        "telegram_trigger": {"telegram"},
        "telegram_send": {"telegram"},
        "gmail_read": {"google", "gmail_smtp"},
        "gmail_send": {"google", "gmail_smtp"},
        "sheets_append_row": {"google"},
        "sheets_read": {"google"},
        "http_request": {"http"},
    }
    if type_key in expected and connection["type"] not in expected[type_key]:
        raise HTTPException(
            422,
            {
                "detail": "Type de connexion incompatible avec l’agent",
                "code": "connection_type_mismatch",
            },
        )
    if require_active and connection["status"] != "active":
        raise HTTPException(
            409,
            {
                "detail": "Connexion indisponible ou expirée ; tester ou reconnecter",
                "code": "connection_unavailable",
            },
        )
