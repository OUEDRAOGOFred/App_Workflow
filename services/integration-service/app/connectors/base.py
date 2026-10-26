from dataclasses import dataclass
from typing import ClassVar

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import Artifact, Connection


@dataclass
class Context:
    db: Session
    connection: Connection
    credentials: dict
    http: httpx.Client
    settings: Settings
    google_credentials: object | None = None


class BaseConnector:
    key: ClassVar[str]
    actions: ClassVar[dict[str, type[BaseModel]]] = {}
    triggers: ClassVar[list[str]] = []
    credential_fields: ClassVar[set[str]] = set()
    required_credentials: ClassVar[set[str]] = set()
    always_required_credentials: ClassVar[set[str]] = set()

    def __init__(self, context: Context):
        self.ctx = context

    def run(self, action: str, params: dict) -> dict:
        model = self.actions.get(action)
        if model is None:
            raise HTTPException(
                422,
                {
                    "detail": "Action inconnue pour ce connecteur",
                    "code": "unknown_action",
                },
            )
        try:
            values = model.model_validate(params).model_dump(
                mode="json", exclude_none=True
            )
        except ValidationError:
            raise HTTPException(
                422,
                {
                    "detail": "Paramètres de l’action invalides",
                    "code": "invalid_action_params",
                },
            ) from None
        return self.execute(action, values)

    def execute(self, action: str, params: dict) -> dict:
        raise NotImplementedError

    def test_connection(self) -> dict:
        raise NotImplementedError

    def poll(self, state: dict) -> tuple[list[dict], dict]:
        return [], state

    def records(self, kind: str) -> list[Artifact]:
        return list(
            self.ctx.db.scalars(
                select(Artifact)
                .where(
                    Artifact.connection_id == self.ctx.connection.id,
                    Artifact.kind == kind,
                )
                .order_by(Artifact.created_at, Artifact.id)
            )
        )

    def record(self, kind: str, data: dict) -> Artifact:
        artifact = Artifact(connection_id=self.ctx.connection.id, kind=kind, data=data)
        self.ctx.db.add(artifact)
        self.ctx.db.flush()
        return artifact
