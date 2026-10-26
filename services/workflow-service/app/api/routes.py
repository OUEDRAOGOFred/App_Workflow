from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import current_user
from app.models.entities import Workflow, WorkflowVersion
from app.schemas.workflows import (
    ValidationReport,
    VersionOutput,
    WorkflowCreate,
    WorkflowOutput,
    WorkflowUpdate,
)
from app.services.validation import validate_workflow
from app.services.versions import revise, snapshot

router = APIRouter(prefix="/workflows", tags=["Workflows"])


def owned(db: Session, identifier: UUID, user: str) -> Workflow:
    workflow = db.scalar(
        select(Workflow).where(Workflow.id == str(identifier), Workflow.user_id == user)
    )
    if workflow is None:
        raise HTTPException(
            404, {"detail": "Workflow introuvable", "code": "not_found"}
        )
    return workflow


@router.get("", response_model=list[WorkflowOutput])
def workflows(
    is_active: bool | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Workflow).where(Workflow.user_id == user)
    if is_active is not None:
        query = query.where(Workflow.is_active == is_active)
    return db.scalars(
        query.order_by(Workflow.updated_at.desc(), Workflow.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("", response_model=WorkflowOutput, status_code=201)
def create(
    body: WorkflowCreate,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = Workflow(user_id=user, **body.model_dump(mode="json"))
    db.add(workflow)
    db.flush()
    snapshot(db, workflow)
    db.commit()
    db.refresh(workflow)
    return workflow


@router.get("/{identifier}", response_model=WorkflowOutput)
def detail(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    return owned(db, identifier, user)


@router.put("/{identifier}", response_model=WorkflowOutput)
def update(
    identifier: UUID,
    body: WorkflowUpdate,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = owned(db, identifier, user)
    changes = body.model_dump(mode="json", exclude_unset=True, exclude={"version"})
    # Preserve incomplete drafts, and require explicit revalidation after any edit.
    changes["is_active"] = False
    return revise(db, workflow, body.version, changes)


@router.delete("/{identifier}", status_code=204)
def remove(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    workflow = owned(db, identifier, user)
    db.execute(
        delete(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
    )
    db.delete(workflow)
    db.commit()
    return Response(status_code=204)


@router.get("/{identifier}/versions", response_model=list[VersionOutput])
def history(
    identifier: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = owned(db, identifier, user)
    return db.scalars(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow.id)
        .order_by(WorkflowVersion.version.desc())
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("/{identifier}/validate", response_model=ValidationReport)
def validate(
    identifier: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return validate_workflow(owned(db, identifier, user), request)


@router.post("/{identifier}/activate", response_model=WorkflowOutput)
def activate(
    identifier: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = owned(db, identifier, user)
    report = validate_workflow(workflow, request)
    if not report.valid:
        raise HTTPException(
            422,
            {
                "detail": "Le workflow ne peut pas être activé",
                "code": "invalid_workflow",
                "errors": report.model_dump()["errors"],
            },
        )
    if workflow.is_active:
        return workflow
    return revise(db, workflow, workflow.version, {"is_active": True})


@router.post("/{identifier}/deactivate", response_model=WorkflowOutput)
def deactivate(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    workflow = owned(db, identifier, user)
    if not workflow.is_active:
        return workflow
    return revise(db, workflow, workflow.version, {"is_active": False})
