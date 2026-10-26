from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import service_claims
from app.models.entities import Workflow

router = APIRouter(prefix="/internal", include_in_schema=False)


@router.get("/workflows/active")
def active(
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    claims: dict = Depends(service_claims),
    db: Session = Depends(get_db),
):
    if claims.get("scope") != "scheduler":
        raise HTTPException(
            403, {"detail": "Portée ordonnanceur requise", "code": "service_forbidden"}
        )
    workflows = db.scalars(
        select(Workflow)
        .where(Workflow.is_active.is_(True))
        .order_by(Workflow.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            "id": workflow.id,
            "user_id": workflow.user_id,
            "trigger": workflow.trigger,
            "version": workflow.version,
        }
        for workflow in workflows
    ]
