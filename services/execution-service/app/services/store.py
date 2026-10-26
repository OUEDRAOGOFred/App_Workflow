from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import database
from app.models.entities import Execution, ExecutionEvent, ExecutionStep, now

TERMINAL = {"success", "failed", "cancelled"}


def session() -> Session:
    return Session(database.get_engine(), expire_on_commit=False)


def event(db: Session, execution_id: str, kind: str, data: dict) -> None:
    db.add(ExecutionEvent(execution_id=execution_id, kind=kind, data=data))


def finish(
    db: Session,
    execution: Execution,
    status: str,
    error: str | None = None,
    code: str | None = None,
) -> None:
    execution.status = status
    execution.finished_at = now()
    execution.heartbeat_at = now()
    execution.error = error
    execution.error_code = code
    unfinished = db.scalars(
        select(ExecutionStep).where(
            ExecutionStep.execution_id == execution.id,
            ExecutionStep.status.in_(["pending", "running"]),
        )
    ).all()
    for step in unfinished:
        step.status = (
            "failed"
            if status == "failed" and step.status == "running"
            else (
                "cancelled"
                if status == "cancelled" and step.status == "running"
                else "skipped"
            )
        )
        step.finished_at = now()
        step.error_code = code
        event(
            db,
            execution.id,
            "step",
            {"node_id": step.node_id, "status": step.status, "error_code": code},
        )
    event(
        db,
        execution.id,
        "execution",
        {"status": status, "error": error, "error_code": code},
    )
    db.commit()
