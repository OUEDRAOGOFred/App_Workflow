import hashlib
from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.entities import Execution, ExecutionStep, now
from app.services.auth import access_token
from app.services.store import event, session


class Services:
    def __init__(self, client: httpx.Client):
        self.client = client
        settings = get_settings()
        self.urls = {
            "workflow": settings.workflow_service_url,
            "agent": settings.agent_service_url,
            "integration": settings.integration_service_url,
            "classifier": settings.classifier_service_url,
        }

    def request(self, service: str, method: str, path: str, user: str, body=None):
        try:
            response = self.client.request(
                method,
                self.urls[service] + path,
                json=body,
                headers={"Authorization": "Bearer " + access_token(user), "Cookie": ""},
                timeout=15,
            )
            if response.status_code == 404:
                raise HTTPException(
                    404,
                    {
                        "detail": "Ressource absente ou inaccessible",
                        "code": "not_found",
                    },
                )
            if response.status_code >= 400:
                raise HTTPException(
                    422 if response.status_code < 500 else 503,
                    {
                        "detail": "Préparation refusée ; vérifier les agents",
                        "code": "preflight_failed",
                    },
                )
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(
                503,
                {
                    "detail": "Service métier indisponible",
                    "code": "upstream_unavailable",
                },
            ) from None

    def snapshot(self, identifier: str, user: str, source: str) -> dict:
        workflow = self.request("workflow", "GET", f"/workflows/{identifier}", user)
        if source != "manual" and not workflow["is_active"]:
            raise HTTPException(
                409, {"detail": "Workflow inactif", "code": "workflow_inactive"}
            )
        report = self.request(
            "workflow", "POST", f"/workflows/{identifier}/validate", user
        )
        if not report["valid"]:
            raise HTTPException(
                422,
                {
                    "detail": "Graphe invalide",
                    "code": "invalid_workflow",
                    "errors": report["errors"],
                },
            )
        agents = {}
        for node in workflow["graph"]["nodes"]:
            identifier = node["data"]["agent_id"]
            if identifier not in agents:
                agent = self.request("agent", "GET", f"/agents/{identifier}", user)
                if agent["status"] != "active":
                    raise HTTPException(
                        422, {"detail": "Agent inactif", "code": "agent_inactive"}
                    )
                agents[identifier] = agent
        return {
            "workflow_id": workflow["id"],
            "workflow_version": workflow["version"],
            "name": workflow["name"],
            "trigger": workflow["trigger"],
            "graph": workflow["graph"],
            "order": report["order"],
            "agents": agents,
        }

    def test_snapshot(self, identifier: str, user: str, draft: bool = False) -> dict:
        if draft:
            body = self.request("agent", "GET", f"/agent-drafts/{identifier}", user)[
                "data"
            ]
            agent = {"id": None, "status": "draft", **body}
        else:
            agent = self.request("agent", "GET", f"/agents/{identifier}", user)
        catalogue = self.request("agent", "GET", "/agent-types", user)
        kind = next(
            (kind for kind in catalogue if kind["key"] == agent.get("type_key")), None
        )
        if kind is None or list(
            Draft202012Validator(kind["config_schema"]).iter_errors(
                agent.get("config", {})
            )
        ):
            raise HTTPException(
                422,
                {
                    "detail": "Configuration incomplète ou invalide",
                    "code": "invalid_agent_config",
                },
            )
        if kind["requires_connection"] and not agent.get("connection_id"):
            raise HTTPException(
                422, {"detail": "Connexion requise", "code": "connection_required"}
            )
        return {
            "workflow_id": None,
            "workflow_version": None,
            "name": agent.get("name", "Test"),
            "trigger": {"type": "manual"},
            "graph": {
                "nodes": [
                    {
                        "id": "test",
                        "type": "agent",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "label": agent.get("name") or kind["label"],
                            "agent_id": "test",
                            "input": {},
                            "timeout_seconds": 60,
                            "retries": 0,
                        },
                    }
                ],
                "edges": [],
            },
            "order": ["test"],
            "agents": {"test": agent},
        }


def key_for(
    user: str, workflow: str | None, source: str, event_id: str | None
) -> str | None:
    if not event_id:
        return None
    return hashlib.sha256(f"{user}:{workflow}:{source}:{event_id}".encode()).hexdigest()


def create_execution(
    db,
    user: str,
    snapshot: dict,
    payload: dict,
    source: str,
    event_id: str | None = None,
    failure: str | None = None,
) -> Execution:
    key = key_for(user, snapshot["workflow_id"], source, event_id)
    if key:
        previous = db.scalar(select(Execution).where(Execution.event_key == key))
        if previous:
            return previous
    execution = Execution(
        id=str(uuid4()),
        user_id=user,
        workflow_id=snapshot["workflow_id"],
        workflow_version=snapshot["workflow_version"],
        snapshot=snapshot,
        trigger_payload=payload,
        source=source,
        event_key=key,
        status="failed" if failure else "queued",
        error=failure,
        error_code="preflight_failed" if failure else None,
        finished_at=now() if failure else None,
    )
    db.add(execution)
    try:
        db.flush()
        nodes = {node["id"]: node for node in snapshot["graph"]["nodes"]}
        for position, identifier in enumerate(snapshot["order"]):
            agent = snapshot["agents"][nodes[identifier]["data"]["agent_id"]]
            db.add(
                ExecutionStep(
                    execution_id=execution.id,
                    node_id=identifier,
                    agent_id=agent.get("id"),
                    position=position,
                )
            )
        event(
            db,
            execution.id,
            "execution",
            {
                "status": execution.status,
                "workflow_version": execution.workflow_version,
                "error": failure,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = (
            db.scalar(select(Execution).where(Execution.event_key == key))
            if key
            else None
        )
        if previous:
            return previous
        raise
    db.refresh(execution)
    return execution


def dispatch_pending() -> None:
    from app.core.celery_app import celery

    with session() as db:
        jobs = db.scalars(
            select(Execution)
            .where(
                Execution.status == "queued",
                (Execution.dispatched_at.is_(None))
                | (Execution.dispatched_at < now() - timedelta(seconds=30)),
            )
            .limit(100)
        ).all()
        for job in jobs:
            celery.send_task(
                "execution.run", args=[job.id], task_id=job.id, queue="celery"
            )
            job.dispatched_at = now()
        db.commit()
