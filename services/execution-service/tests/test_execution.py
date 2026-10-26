import asyncio
import copy
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

os.environ["JWT_SECRET"] = "phase-four-test-secret-never-use-in-production-123456789"
os.environ["DATABASE_URL"] = "sqlite://"

from app.core import database
from app.core.config import get_settings
from app.main import app
from app.models.entities import (
    Execution,
    ExecutionEvent,
    WebhookKey,
    now,
)
from app.services.dispatch import Services, create_execution, dispatch_pending
from app.services.engine import run_execution
from app.services.expressions import MappingError, lookup, resolve
from app.services.scheduler import consume, recover_stale, schedule_cron
from app.services.store import session

USER = str(uuid4())
OTHER = str(uuid4())
WORKFLOW = str(uuid4())


def token(user=USER):
    settings = get_settings()
    return jwt.encode(
        {
            "sub": user,
            "type": "access",
            "jti": str(uuid4()),
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=10),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


class LeaseRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script, count, key, token):
        if self.values.get(key) != token:
            return 0
        if "DEL" in script:
            self.values.pop(key, None)
        return 1

    def ping(self):
        return True


def snapshot(kinds=None, configs=None):
    kinds = kinds or ["transform"] * 4
    configs = configs or [
        {"mapping": {"text": "Facture TVA euros"}},
        {"mapping": {"label": "facture", "confidence": 0.93}},
        {
            "mapping": {
                "label": "{{ nodes.n1.output.label }}",
                "score": "{{ input.confidence }}",
            }
        },
        {"mapping": {"summary": "Classe {{ input.label }}, score {{ input.score }}"}},
    ]
    agents = {}
    nodes = []
    for index, (kind, config) in enumerate(zip(kinds, configs, strict=True)):
        identifier = str(uuid4())
        agents[identifier] = {
            "id": identifier,
            "type_key": kind,
            "name": kind,
            "config": config,
            "connection_id": None,
            "status": "active",
        }
        nodes.append(
            {
                "id": f"n{index}",
                "data": {
                    "agent_id": identifier,
                    "input": {},
                    "timeout_seconds": 2,
                    "retries": 0,
                },
            }
        )
    return {
        "workflow_id": WORKFLOW,
        "workflow_version": 1,
        "name": "Test",
        "trigger": {"type": "manual"},
        "graph": {
            "nodes": nodes,
            "edges": [
                {"id": f"e{i}", "source": f"n{i}", "target": f"n{i+1}"}
                for i in range(len(nodes) - 1)
            ],
        },
        "order": [node["id"] for node in nodes],
        "agents": agents,
    }


class Backend:
    def __init__(self):
        self.snapshot = snapshot()
        self.effects = []
        self.user = USER

    def sync(self, request):
        claims = jwt.decode(
            request.headers["authorization"].split()[1],
            get_settings().jwt_secret,
            algorithms=["HS256"],
            audience=get_settings().jwt_audience,
        )
        if claims["sub"] != self.user:
            return httpx.Response(404, json={"code": "not_found"})
        path = request.url.path
        if path.endswith("/validate"):
            return httpx.Response(
                200, json={"valid": True, "order": self.snapshot["order"], "errors": []}
            )
        if path.startswith("/workflows/"):
            return httpx.Response(
                200,
                json={
                    "id": WORKFLOW,
                    "user_id": USER,
                    "version": 1,
                    "name": "Test",
                    "is_active": True,
                    "graph": self.snapshot["graph"],
                    "trigger": self.snapshot["trigger"],
                },
            )
        if path == "/agent-types":
            return httpx.Response(
                200,
                json=[
                    {
                        "key": "transform",
                        "requires_connection": False,
                        "config_schema": {"type": "object", "required": ["mapping"]},
                    }
                ],
            )
        if path.startswith("/agent-drafts/"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "type_key": "transform",
                        "name": "Brouillon",
                        "config": {"mapping": {"ok": True}},
                        "connection_id": None,
                    }
                },
            )
        identifier = path.split("/")[-1]
        if identifier in self.snapshot["agents"]:
            return httpx.Response(200, json=self.snapshot["agents"][identifier])
        return httpx.Response(404, json={"code": "not_found"})

    async def async_request(self, request):
        path = request.url.path
        if path.startswith("/internal/"):
            self.effects.append((path, json.loads(request.content)))
            return httpx.Response(200, json={"updated": True})
        if path.startswith("/agents/") or path.startswith("/workflows/"):
            return self.sync(request)
        if path == "/classify":
            return httpx.Response(
                200, json={"label": "facture", "confidence": 0.93, "backend": "demo"}
            )
        if path.startswith("/connections/"):
            self.effects.append((path, json.loads(request.content)))
            return httpx.Response(200, json={"mock": True, "updatedRows": 1})
        if path == "/integrations/http/request":
            return httpx.Response(
                200, json={"status_code": 200, "mock": True, "data": {"ok": True}}
            )
        return httpx.Response(404, json={"code": "not_found"})


@pytest.fixture
def fixture(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    backend = Backend()
    redis = LeaseRedis()
    with (
        TestClient(app) as client,
        httpx.Client(transport=httpx.MockTransport(backend.sync)) as sync,
    ):
        original = app.state.services
        app.state.services = Services(sync)
        client.headers["Authorization"] = "Bearer " + token()
        yield client, backend, redis
        app.state.services = original
    engine.dispose()


def create(snap, payload=None, event_id=None, source="manual"):
    with session() as db:
        return create_execution(db, USER, snap, payload or {}, source, event_id).id


def execute(identifier, backend, redis):
    async def call():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(backend.async_request)
        ) as client:
            await run_execution(identifier, client, redis)

    asyncio.run(call())


def test_four_node_graph_mapping_logs_and_no_duplicate_claim(fixture):
    client, backend, redis = fixture
    identifier = create(backend.snapshot)
    execute(identifier, backend, redis)
    result = client.get(f"/executions/{identifier}").json()
    assert result["status"] == "success"
    assert [step["status"] for step in result["steps"]] == ["success"] * 4
    assert result["steps"][-1]["output"]["summary"] == "Classe facture, score 0.93"
    effects = len(backend.effects)
    execute(identifier, backend, redis)
    assert len(backend.effects) == effects
    stream = client.get(f"/executions/{identifier}/stream")
    assert (
        "text/event-stream" in stream.headers["content-type"]
        and "event: complete" in stream.text
    )
    assert "event: step" in stream.text
    with session() as db:
        last = db.scalar(
            select(ExecutionEvent.id)
            .where(ExecutionEvent.execution_id == identifier)
            .order_by(ExecutionEvent.id.desc())
        )
    resumed = client.get(
        f"/executions/{identifier}/stream", headers={"Last-Event-ID": str(last)}
    )
    assert "event: complete" in resumed.text and "event: step" not in resumed.text


def test_real_dispatch_four_nodes_classifier_and_mock_sheets(fixture):
    client, backend, redis = fixture
    backend.snapshot = snapshot(
        ["transform", "document_classifier", "sheets_append_row", "transform"],
        [
            {"mapping": {"text": "Facture TVA euros"}},
            {"confidence_threshold": 0.5},
            {
                "spreadsheet_id": "demo-sheet",
                "range": "Documents!A:C",
                "values": ["{{ input.label }}", "{{ input.confidence }}"],
            },
            {"mapping": {"saved": "{{ input.updatedRows }}"}},
        ],
    )
    third = backend.snapshot["graph"]["nodes"][2]["data"]["agent_id"]
    backend.snapshot["agents"][third]["connection_id"] = str(uuid4())
    identifier = create(backend.snapshot)
    execute(identifier, backend, redis)
    result = client.get(f"/executions/{identifier}").json()
    assert result["status"] == "success"
    append = next(
        body for path, body in backend.effects if path.endswith("sheets_append")
    )
    assert append["params"]["values"] == [["facture", 0.93]]
    assert result["steps"][-1]["output"] == {"saved": 1}


@pytest.mark.parametrize("take_true", [True, False])
def test_condition_selects_branch_and_join(fixture, take_true):
    client, backend, redis = fixture
    backend.snapshot = snapshot(
        ["condition", "transform", "transform", "transform"],
        [
            {"field": "take_true", "operator": "eq", "value": True},
            {"mapping": {"selected": "yes"}},
            {"mapping": {"selected": "no"}},
            {"mapping": {"result": "{{ input.selected }}"}},
        ],
    )
    backend.snapshot["graph"]["edges"] = [
        {"id": "yes", "source": "n0", "target": "n1", "sourceHandle": "true"},
        {"id": "no", "source": "n0", "target": "n2", "sourceHandle": "false"},
        {"id": "a", "source": "n1", "target": "n3"},
        {"id": "b", "source": "n2", "target": "n3"},
    ]
    identifier = create(backend.snapshot, {"take_true": take_true})
    execute(identifier, backend, redis)
    result = client.get(f"/executions/{identifier}").json()
    assert result["status"] == "success"
    assert result["steps"][2 if take_true else 1]["status"] == "skipped"
    assert result["steps"][-1]["output"]["result"] == ("yes" if take_true else "no")


def test_failed_mapping_preserves_error_and_skips_downstream(fixture):
    client, backend, redis = fixture
    first = next(iter(backend.snapshot["agents"].values()))
    first["config"] = {"mapping": {"invalid": "{{ nodes.missing.output.field }}"}}
    identifier = create(backend.snapshot)
    execute(identifier, backend, redis)
    result = client.get(f"/executions/{identifier}").json()
    assert result["status"] == "failed" and result["error_code"] == "invalid_mapping"
    assert [step["status"] for step in result["steps"]] == [
        "failed",
        "skipped",
        "skipped",
        "skipped",
    ]


def test_expired_connection_fails_without_retry(fixture):
    client, backend, redis = fixture
    backend.snapshot = snapshot(["telegram_send"], [{"chat_id": "1", "text": "Test"}])
    next(iter(backend.snapshot["agents"].values()))["connection_id"] = str(uuid4())
    original = backend.async_request

    async def handler(request):
        if request.url.path.startswith("/connections/"):
            return httpx.Response(409, json={"code": "connection_expired"})
        return await original(request)

    backend.async_request = handler
    backend.snapshot["graph"]["nodes"][0]["data"]["retries"] = 3
    identifier = create(backend.snapshot)
    execute(identifier, backend, redis)
    result = client.get(f"/executions/{identifier}").json()
    assert result["status"] == "failed" and result["error_code"] == "connection_expired"
    assert result["steps"][0]["attempt"] == 1


def test_transient_error_retries_then_succeeds(fixture):
    client, backend, redis = fixture
    backend.snapshot = snapshot(["document_classifier"], [{}])
    backend.snapshot["graph"]["nodes"][0]["data"]["retries"] = 1
    calls = []
    original = backend.async_request

    async def handler(request):
        if request.url.path == "/classify":
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(503, json={"code": "ollama_unavailable"})
        return await original(request)

    backend.async_request = handler
    identifier = create(backend.snapshot, {"text": "Facture"})
    execute(identifier, backend, redis)
    result = client.get(f"/executions/{identifier}").json()
    assert (
        result["status"] == "success"
        and result["steps"][0]["attempt"] == 2
        and len(calls) == 2
    )


def test_node_timeout(fixture):
    client, backend, redis = fixture
    backend.snapshot = snapshot(["document_classifier"], [{}])
    backend.snapshot["graph"]["nodes"][0]["data"]["timeout_seconds"] = 1
    original = backend.async_request

    async def handler(request):
        if request.url.path == "/classify":
            await asyncio.sleep(3)
        return await original(request)

    backend.async_request = handler
    identifier = create(backend.snapshot, {"text": "Facture"})
    execute(identifier, backend, redis)
    assert (
        client.get(f"/executions/{identifier}").json()["error_code"] == "node_timeout"
    )


def test_cancel_queued_and_running(fixture):
    client, backend, redis = fixture
    identifier = create(backend.snapshot)
    assert (
        client.post(f"/executions/{identifier}/cancel").json()["status"] == "cancelled"
    )
    execute(identifier, backend, redis)
    assert not backend.effects
    backend.snapshot = snapshot(["document_classifier"], [{}])
    original = backend.async_request
    entered = asyncio.Event()

    async def handler(request):
        if request.url.path == "/classify":
            entered.set()
            await asyncio.sleep(10)
        return await original(request)

    backend.async_request = handler
    identifier = create(backend.snapshot, {"text": "Facture"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            task = asyncio.create_task(run_execution(identifier, http, redis))
            await asyncio.wait_for(entered.wait(), 2)
            with session() as db:
                execution = db.get(Execution, identifier)
                execution.cancel_requested = True
                db.commit()
            await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    assert client.get(f"/executions/{identifier}").json()["status"] == "cancelled"


def test_api_ownership_idempotency_filters_agent_and_draft_tests(fixture):
    client, backend, redis = fixture
    response = client.post(
        f"/workflows/{WORKFLOW}/run",
        json={"payload": {"value": 1}},
        headers={"Idempotency-Key": "same-event"},
    )
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    assert (
        client.post(
            f"/workflows/{WORKFLOW}/run",
            json={"payload": {"value": 2}},
            headers={"Idempotency-Key": "same-event"},
        ).json()["id"]
        == identifier
    )
    assert (
        len(client.get(f"/executions?workflow_id={WORKFLOW}&status=queued").json()) == 1
    )
    agent = next(iter(backend.snapshot["agents"]))
    assert client.post(f"/agents/{agent}/test", json={"input": {}}).status_code == 202
    draft = client.post(f"/agent-drafts/{uuid4()}/test", json={"input": {}})
    assert draft.status_code == 202
    execute(draft.json()["id"], backend, redis)
    assert client.get(f"/executions/{draft.json()['id']}").json()["status"] == "success"
    client.headers["Authorization"] = "Bearer " + token(OTHER)
    assert client.get("/executions").json() == []
    for suffix, method in (("", "GET"), ("/stream", "GET"), ("/cancel", "POST")):
        assert (
            client.request(method, f"/executions/{identifier}{suffix}").status_code
            == 404
        )
    assert (
        client.post(f"/workflows/{WORKFLOW}/run", json={"payload": {}}).status_code
        == 404
    )


def test_webhook_secret_once_rotation_and_deduplication(fixture):
    client, backend, _ = fixture
    backend.snapshot["trigger"] = {"type": "webhook"}
    first = client.post(f"/workflows/{WORKFLOW}/webhook-key").json()
    with session() as db:
        assert first["secret"] not in db.get(WebhookKey, WORKFLOW).secret_hash
    headers = {"X-Webhook-Secret": first["secret"], "Idempotency-Key": "event-42"}
    client.headers.pop("Authorization")
    assert (
        client.post(
            f"/webhooks/{WORKFLOW}",
            json={"text": "x"},
            headers={"Idempotency-Key": "e"},
        ).status_code
        == 403
    )
    result = client.post(
        f"/webhooks/{WORKFLOW}", json={"text": "Facture"}, headers=headers
    )
    assert result.status_code == 202, result.text
    assert (
        client.post(
            f"/webhooks/{WORKFLOW}", json={"text": "Facture"}, headers=headers
        ).json()["id"]
        == result.json()["id"]
    )
    client.headers["Authorization"] = "Bearer " + token()
    second = client.post(f"/workflows/{WORKFLOW}/webhook-key").json()
    assert second["secret"] != first["secret"]
    client.headers.pop("Authorization")
    assert (
        client.post(f"/webhooks/{WORKFLOW}", json={}, headers=headers).status_code
        == 403
    )


def test_cron_and_telegram_event_dispatch_deduplicate(fixture):
    _, backend, _ = fixture
    connection = str(uuid4())
    workflow = {
        "id": WORKFLOW,
        "user_id": USER,
        "trigger": {"type": "telegram", "connection_id": connection},
    }
    backend.snapshot["trigger"] = workflow["trigger"]
    with httpx.Client(transport=httpx.MockTransport(backend.sync)) as http:
        services = Services(http)
        event = {
            "type": "telegram.message",
            "user_id": USER,
            "connection_id": connection,
            "event_id": "7",
            "payload": json.dumps({"text": "Facture"}),
        }
        assert consume(event, [workflow], services) == consume(
            event, [workflow], services
        )
        event["user_id"] = OTHER
        assert consume(event, [workflow], services) == []
        workflow["trigger"] = {"type": "cron", "schedule": "* * * * *"}
        backend.snapshot["trigger"] = workflow["trigger"]
        moment = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
        assert schedule_cron([workflow], services, moment) == schedule_cron(
            [workflow], services, moment
        )


def test_queued_outbox_and_stale_worker_recovery(fixture):
    client, backend, _ = fixture
    identifier = create(backend.snapshot)
    with patch("app.core.celery_app.celery.send_task") as send:
        dispatch_pending()
        dispatch_pending()
    assert send.call_count == 1 and send.call_args.kwargs["args"] == [identifier]
    with session() as db:
        job = db.get(Execution, identifier)
        job.status = "running"
        job.heartbeat_at = now() - timedelta(minutes=10)
        db.commit()
    recover_stale()
    result = client.get(f"/executions/{identifier}").json()
    assert result["status"] == "failed" and result["error_code"] == "worker_interrupted"


def test_http_agent_without_connection_and_changed_agent(fixture):
    client, backend, redis = fixture
    backend.snapshot = snapshot(
        ["http_request"], [{"url": "https://example.test/", "method": "GET"}]
    )
    identifier = create(backend.snapshot)
    execute(identifier, backend, redis)
    assert client.get(f"/executions/{identifier}").json()["status"] == "success"
    identifier = create(backend.snapshot)
    next(iter(backend.snapshot["agents"].values()))["status"] = "disabled"
    execute(identifier, backend, redis)
    assert (
        client.get(f"/executions/{identifier}").json()["error_code"] == "agent_inactive"
    )


@pytest.mark.parametrize(
    "path",
    [
        "__import__('os').system('bad')",
        "nodes.x.output.__class__",
        "input[0].constructor()",
        "input.x + 1",
    ],
)
def test_mapping_never_evaluates_code(path):
    with pytest.raises(MappingError):
        lookup({"input": {}, "nodes": {}}, path)


def test_mapping_preserves_types_arrays_and_nested_values():
    context = {
        "input": {"values": [{"number": 7}], "boolean": True},
        "nodes": {},
        "trigger": {},
    }
    assert resolve(
        {"list": ["{{ input.values[0].number }}"], "bool": "{{ input.boolean }}"},
        context,
    ) == {"list": [7], "bool": True}
    assert resolve("score={{ input.values[0].number }}", context) == "score=7"


def test_migrations(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'schema.db'}"}
    for command in (
        ("upgrade", "head"),
        ("check",),
        ("downgrade", "base"),
        ("upgrade", "head"),
    ):
        subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=True,
        )


def test_inserted_text_is_not_reinterpreted_as_mapping():
    context = {"input": {"text": "{{ données brutes }}"}, "nodes": {}, "trigger": {}}
    assert resolve("Texte: {{ input.text }}", context) == "Texte: {{ données brutes }}"


def test_failed_trigger_preflight_does_not_block_other_workflows(fixture):
    from fastapi import HTTPException

    client, backend, _ = fixture
    connection = str(uuid4())
    bad = str(uuid4())
    trigger = {"type": "telegram", "connection_id": connection}
    workflows = [
        {"id": bad, "user_id": USER, "trigger": trigger, "version": 1},
        {"id": WORKFLOW, "user_id": USER, "trigger": trigger, "version": 1},
    ]

    class Prepare:
        def snapshot(self, identifier, user, source):
            if identifier == bad:
                raise HTTPException(422, {"code": "invalid_workflow"})
            return backend.snapshot

    event = {
        "type": "telegram.message",
        "user_id": USER,
        "connection_id": connection,
        "event_id": "1",
        "payload": "{}",
    }
    identifiers = consume(event, workflows, Prepare())
    assert len(identifiers) == 2
    assert (
        client.get(f"/executions/{identifiers[0]}").json()["error_code"]
        == "preflight_failed"
    )
    assert client.get(f"/executions/{identifiers[1]}").json()["status"] == "queued"


def test_gmail_attachment_is_downloaded_before_classification():
    import base64

    from app.services.actions import Actions

    connection = str(uuid4())
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("gmail_attachment"):
            assert json.loads(request.content)["params"] == {
                "message_id": "mail-1",
                "attachment_id": "attachment-1",
            }
            return httpx.Response(
                200, json={"content_base64": base64.b64encode(b"Facture TVA").decode()}
            )
        assert (
            request.url.path == "/classify/file" and b"Facture TVA" in request.content
        )
        return httpx.Response(200, json={"label": "facture", "confidence": 0.9})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await Actions(http, USER).run(
                {"type_key": "document_classifier", "config": {}},
                {
                    "id": "mail-1",
                    "attachments": [{"id": "attachment-1", "filename": "a.txt"}],
                },
                {
                    "input": {},
                    "trigger": {},
                    "nodes": {},
                    "source_connection_id": connection,
                },
                "gmail",
            )

    assert asyncio.run(run())["label"] == "facture" and len(seen) == 2


def test_history_keeps_archived_graph_after_workflow_changes(fixture):
    client, backend, _ = fixture
    original = copy.deepcopy(backend.snapshot["graph"])
    identifier = create(backend.snapshot)
    backend.snapshot["graph"]["nodes"].clear()
    result = client.get(f"/executions/{identifier}").json()
    assert result["graph"] == original and result["workflow_name"] == "Test"
