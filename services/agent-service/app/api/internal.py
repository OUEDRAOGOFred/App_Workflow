from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import service_claims
from app.models.entities import Agent, now

router = APIRouter(prefix="/internal", include_in_schema=False)


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "failed"]
    execution_id: UUID


@router.post("/agents/{identifier}/run-result")
def result(
    identifier: UUID,
    body: RunResult,
    claims: dict = Depends(service_claims),
    db: Session = Depends(get_db),
):
    agent = db.scalar(
        select(Agent).where(Agent.id == str(identifier), Agent.user_id == claims["sub"])
    )
    if agent is None:
        raise HTTPException(404, {"detail": "Agent introuvable", "code": "not_found"})
    agent.last_run_at = now()
    if agent.status != "disabled":
        if body.status == "failed":
            agent.status = "error"
        elif agent.status == "error":
            agent.status = "draft"
    db.commit()
    return {"updated": True}
