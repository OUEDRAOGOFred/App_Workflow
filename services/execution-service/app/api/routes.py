import asyncio
import hashlib
import hmac
import json
import secrets
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import current_user
from app.models.entities import (
    Execution,
    ExecutionEvent,
    ExecutionStep,
    WebhookKey,
    now,
)
from app.schemas.executions import (
    AgentTestInput,
    ExecutionDetail,
    ExecutionOutput,
    RunInput,
    Status,
    StepOutput,
)
from app.services.dispatch import create_execution
from app.services.store import TERMINAL, event, finish, session

router = APIRouter(tags=["Exécutions"])


def owned(db: Session, identifier: UUID, user: str) -> Execution:
    execution = db.scalar(
        select(Execution).where(
            Execution.id == str(identifier), Execution.user_id == user
        )
    )
    if execution is None:
        raise HTTPException(
            404, {"detail": "Exécution introuvable", "code": "not_found"}
        )
    return execution


@router.post(
    "/workflows/{identifier}/run", response_model=ExecutionOutput, status_code=202
)
def run(
    identifier: UUID,
    body: RunInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, max_length=200),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    snapshot = request.app.state.services.snapshot(str(identifier), user, "manual")
    return create_execution(
        db, user, snapshot, body.payload, "manual", idempotency_key or body.event_id
    )


@router.post(
    "/agents/{identifier}/test", response_model=ExecutionOutput, status_code=202
)
def test_agent(
    identifier: UUID,
    body: AgentTestInput,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    snapshot = request.app.state.services.test_snapshot(str(identifier), user)
    return create_execution(db, user, snapshot, body.input, "agent_test")


@router.post(
    "/agent-drafts/{identifier}/test", response_model=ExecutionOutput, status_code=202
)
def test_draft(
    identifier: UUID,
    body: AgentTestInput,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    snapshot = request.app.state.services.test_snapshot(
        str(identifier), user, draft=True
    )
    return create_execution(db, user, snapshot, body.input, "agent_test")


@router.get("/executions", response_model=list[ExecutionOutput])
def history(
    workflow_id: UUID | None = None,
    status: Status | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Execution).where(Execution.user_id == user)
    if workflow_id:
        query = query.where(Execution.workflow_id == str(workflow_id))
    if status:
        query = query.where(Execution.status == status)
    return db.scalars(
        query.order_by(Execution.created_at.desc(), Execution.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/executions/{identifier}", response_model=ExecutionDetail)
def detail(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    execution = owned(db, identifier, user)
    result = ExecutionOutput.model_validate(execution).model_dump(mode="json")
    result["workflow_name"] = execution.snapshot["name"]
    result["graph"] = execution.snapshot["graph"]
    result["steps"] = [
        StepOutput.model_validate(step)
        for step in db.scalars(
            select(ExecutionStep)
            .where(ExecutionStep.execution_id == execution.id)
            .order_by(ExecutionStep.position)
        )
    ]
    return result


@router.post("/executions/{identifier}/cancel", response_model=ExecutionOutput)
def cancel(
    identifier: UUID, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    execution = owned(db, identifier, user)
    if execution.status in TERMINAL:
        return execution
    execution.cancel_requested = True
    if execution.status == "queued":
        finish(db, execution, "cancelled", code="cancelled")
    else:
        event(db, execution.id, "cancel_requested", {"status": "running"})
        db.commit()
    db.refresh(execution)
    return execution


@router.get("/executions/{identifier}/stream")
def stream(
    identifier: UUID,
    request: Request,
    after_id: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, max_length=30),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    owned(db, identifier, user)
    if last_event_id:
        if not last_event_id.isdigit():
            raise HTTPException(
                422, {"detail": "Last-Event-ID invalide", "code": "invalid_event_id"}
            )
        after_id = max(after_id, int(last_event_id))

    async def events():
        cursor = after_id
        started = time.monotonic()
        while not await request.is_disconnected():
            with session() as current:
                execution = current.get(Execution, str(identifier))
                rows = list(
                    current.scalars(
                        select(ExecutionEvent)
                        .where(
                            ExecutionEvent.execution_id == str(identifier),
                            ExecutionEvent.id > cursor,
                        )
                        .order_by(ExecutionEvent.id)
                        .limit(200)
                    )
                )
                state = execution.status
                messages = [(row.id, row.kind, row.data) for row in rows]
            for event_id, kind, data in messages:
                cursor = event_id
                encoded = json.dumps(data, ensure_ascii=False)
                yield f"id: {event_id}\nevent: {kind}\ndata: {encoded}\n\n"
            if state in TERMINAL and len(messages) < 200:
                yield f"event: complete\ndata: {json.dumps({'status':state})}\n\n"
                return
            if time.monotonic() - started >= 600:
                yield "event: reconnect\ndata: {}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/workflows/{identifier}/webhook-key")
def webhook_key(
    identifier: UUID,
    request: Request,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    workflow = request.app.state.services.request(
        "workflow", "GET", f"/workflows/{identifier}", user
    )
    if workflow["trigger"]["type"] != "webhook":
        raise HTTPException(
            422, {"detail": "Déclencheur webhook requis", "code": "wrong_trigger"}
        )
    secret = secrets.token_urlsafe(32)
    key = db.get(WebhookKey, str(identifier))
    if key is None:
        key = WebhookKey(workflow_id=str(identifier), user_id=user)
        db.add(key)
    if key.user_id != user:
        raise HTTPException(
            404, {"detail": "Workflow introuvable", "code": "not_found"}
        )
    key.secret_hash = hashlib.sha256(secret.encode()).hexdigest()
    key.created_at = now()
    db.commit()
    return {
        "secret": secret,
        "header": "X-Webhook-Secret",
        "url": f"/api/v1/webhooks/{identifier}",
        "shown_once": True,
    }


@router.post("/webhooks/{identifier}", response_model=ExecutionOutput, status_code=202)
def webhook(
    identifier: UUID,
    body: dict,
    request: Request,
    x_webhook_secret: str = Header(default="", max_length=256),
    idempotency_key: str = Header(min_length=1, max_length=200),
    db: Session = Depends(get_db),
):
    try:
        json.dumps(body, allow_nan=False)
    except ValueError:
        raise HTTPException(
            422, {"detail": "JSON non fini interdit", "code": "invalid_payload"}
        ) from None
    key = db.get(WebhookKey, str(identifier))
    digest = hashlib.sha256(x_webhook_secret.encode()).hexdigest()
    if key is None or not hmac.compare_digest(digest, key.secret_hash):
        raise HTTPException(
            403,
            {"detail": "Secret de webhook invalide", "code": "invalid_webhook_secret"},
        )
    snapshot = request.app.state.services.snapshot(
        str(identifier), key.user_id, "webhook"
    )
    if snapshot["trigger"]["type"] != "webhook":
        raise HTTPException(
            409, {"detail": "Le déclencheur a changé", "code": "wrong_trigger"}
        )
    return create_execution(db, key.user_id, snapshot, body, "webhook", idempotency_key)
