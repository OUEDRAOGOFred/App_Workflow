from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import current_user
from app.models.entities import Agent, AgentDraft, AgentType
from app.schemas.agents import (
    AgentCreate,
    AgentOutput,
    AgentTypeOutput,
    AgentUpdate,
    DraftBody,
    DraftOutput,
    Status,
)
from app.services.validation import (
    reject_secrets,
    validate_activation,
    validate_config,
    validate_connection,
)

router = APIRouter(tags=["Agents"])


def owned(db: Session, model, identifier: UUID, user: str):
    item = db.scalar(
        select(model).where(model.id == str(identifier), model.user_id == user)
    )
    if item is None:
        raise HTTPException(
            404, {"detail": "Ressource introuvable", "code": "not_found"}
        )
    return item


@router.get("/agent-types", response_model=list[AgentTypeOutput])
def types(user: str = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(AgentType).order_by(AgentType.key)).all()


@router.get("/agents", response_model=list[AgentOutput])
def agents(
    status: Status | None = None,
    type_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Agent).where(Agent.user_id == user)
    if status:
        query = query.where(Agent.status == status)
    if type_key:
        query = query.where(Agent.type_key == type_key)
    return db.scalars(
        query.order_by(Agent.created_at.desc(), Agent.id).offset(offset).limit(limit)
    ).all()


@router.post("/agents", response_model=AgentOutput, status_code=201)
def create(
    body: AgentCreate,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    validate_config(db, body.type_key, body.config)
    validate_connection(
        body.type_key, str(body.connection_id) if body.connection_id else None, request
    )
    agent = Agent(user_id=user, **body.model_dump(mode="json"))
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/agents/{identifier}", response_model=AgentOutput)
def detail(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    return owned(db, Agent, identifier, user)


@router.put("/agents/{identifier}", response_model=AgentOutput)
def update(
    identifier: UUID,
    body: AgentUpdate,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    agent = owned(db, Agent, identifier, user)
    values = body.model_dump(exclude_unset=True, mode="json")
    validate_config(
        db, values.get("type_key", agent.type_key), values.get("config", agent.config)
    )
    validate_connection(
        values.get("type_key", agent.type_key),
        values.get("connection_id", agent.connection_id),
        request,
    )
    for key, value in values.items():
        setattr(agent, key, value)
    # An edited agent must be explicitly reactivated with its new configuration.
    if values:
        agent.status = "draft"
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/agents/{identifier}", status_code=204)
def delete(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    db.delete(owned(db, Agent, identifier, user))
    db.commit()
    return Response(status_code=204)


@router.post("/agents/{identifier}/activate", response_model=AgentOutput)
def activate(
    identifier: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    agent = owned(db, Agent, identifier, user)
    validate_activation(db, agent)
    validate_connection(
        agent.type_key, agent.connection_id, request, require_active=True
    )
    agent.status = "active"
    db.commit()
    db.refresh(agent)
    return agent


@router.post("/agents/{identifier}/deactivate", response_model=AgentOutput)
def deactivate(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    agent = owned(db, Agent, identifier, user)
    agent.status = "disabled"
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/agent-drafts", response_model=list[DraftOutput], tags=["Brouillons"])
def drafts(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(AgentDraft)
        .where(AgentDraft.user_id == user)
        .order_by(AgentDraft.updated_at.desc(), AgentDraft.id)
        .offset(offset)
        .limit(limit)
    ).all()


def save_draft(db: Session, draft: AgentDraft, body: DraftBody) -> AgentDraft:
    reject_secrets(body.data.config)
    if body.data.type_key and db.get(AgentType, body.data.type_key) is None:
        raise HTTPException(
            422, {"detail": "Type d’agent inconnu", "code": "unknown_agent_type"}
        )
    draft.step = body.step
    draft.data = body.data.model_dump(mode="json")
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@router.post(
    "/agent-drafts", response_model=DraftOutput, status_code=201, tags=["Brouillons"]
)
def create_draft(
    body: DraftBody, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    return save_draft(db, AgentDraft(user_id=user), body)


@router.get(
    "/agent-drafts/{identifier}", response_model=DraftOutput, tags=["Brouillons"]
)
def draft_detail(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    return owned(db, AgentDraft, identifier, user)


@router.put(
    "/agent-drafts/{identifier}", response_model=DraftOutput, tags=["Brouillons"]
)
def update_draft(
    identifier: UUID,
    body: DraftBody,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return save_draft(db, owned(db, AgentDraft, identifier, user), body)


@router.delete("/agent-drafts/{identifier}", status_code=204, tags=["Brouillons"])
def delete_draft(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    db.delete(owned(db, AgentDraft, identifier, user))
    db.commit()
    return Response(status_code=204)
