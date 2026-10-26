from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.entities import Workflow, WorkflowVersion, now

SNAPSHOT_FIELDS = ("name", "description", "graph", "trigger", "is_active")


def snapshot(db: Session, workflow: Workflow) -> None:
    db.add(
        WorkflowVersion(
            workflow_id=workflow.id,
            version=workflow.version,
            snapshot={key: getattr(workflow, key) for key in SNAPSHOT_FIELDS},
        )
    )


def revise(
    db: Session, workflow: Workflow, expected_version: int, changes: dict
) -> Workflow:
    result = db.execute(
        update(Workflow)
        .where(
            Workflow.id == workflow.id,
            Workflow.user_id == workflow.user_id,
            Workflow.version == expected_version,
        )
        .values(**changes, version=expected_version + 1, updated_at=now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            409,
            {
                "detail": "Le workflow a été modifié ; rechargez sa dernière version",
                "code": "version_conflict",
            },
        )
    db.refresh(workflow)
    snapshot(db, workflow)
    db.commit()
    db.refresh(workflow)
    return workflow
