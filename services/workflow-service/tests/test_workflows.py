import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

os.environ["JWT_SECRET"] = "phase-two-test-key-not-for-production-123456789"
os.environ["DATABASE_URL"] = "sqlite://"

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.entities import WorkflowVersion
from app.schemas.workflows import Graph, Trigger
from app.services.graph import validate_graph


def token(user=None, kind="access"):
    settings = get_settings()
    return jwt.encode(
        {
            "sub": user or str(uuid4()),
            "type": kind,
            "jti": str(uuid4()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def database():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as http:
        http.headers["Authorization"] = f"Bearer {token()}"
        original = app.state.agents
        mock = httpx.Client(
            base_url="http://agent-service:8000",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"status": "active", "type_key": "transform"}
                )
            ),
        )
        app.state.agents = mock
        yield http, engine
        app.state.agents.close()
        app.state.agents = original
    app.dependency_overrides.clear()
    engine.dispose()


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


def node(identifier, agent_id=None):
    return {"id": identifier, "data": {"agent_id": agent_id or str(uuid4())}}


def graph():
    return {
        "nodes": [node("start"), node("middle"), node("end")],
        "edges": [
            {"id": "e1", "source": "start", "target": "middle"},
            {"id": "e2", "source": "middle", "target": "end"},
        ],
    }


def create(http, **changes):
    response = http.post(
        "/workflows", json={"name": "Documents", "graph": graph(), **changes}
    )
    assert response.status_code == 201, response.text
    return response.json()


def set_agents(handler):
    app.state.agents.close()
    app.state.agents = httpx.Client(
        base_url="http://agent-service:8000", transport=httpx.MockTransport(handler)
    )


def test_crud_version_conflict_snapshots_and_activation(client):
    http, engine = client
    workflow = create(http)
    path = f"/workflows/{workflow['id']}"
    assert workflow["version"] == 1 and not workflow["is_active"]
    assert len(http.get("/workflows?is_active=false").json()) == 1
    report = http.post(path + "/validate").json()
    assert report == {"valid": True, "errors": [], "order": ["start", "middle", "end"]}
    active = http.post(path + "/activate").json()
    assert active["is_active"] and active["version"] == 2
    assert http.post(path + "/activate").json()["version"] == 2
    assert http.put(path, json={"version": 1, "name": "Stale edit"}).status_code == 409
    edited = http.put(path, json={"version": 2, "name": "Nouveau nom"}).json()
    assert edited["version"] == 3 and not edited["is_active"]
    versions = http.get(path + "/versions").json()
    assert [item["version"] for item in versions] == [3, 2, 1]
    assert versions[-1]["snapshot"]["name"] == "Documents"
    assert versions[1]["snapshot"]["is_active"] is True
    assert http.post(path + "/activate").json()["version"] == 4
    assert http.post(path + "/deactivate").json()["version"] == 5
    assert http.post(path + "/deactivate").json()["version"] == 5
    assert http.delete(path).status_code == 204
    assert http.get(path).status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(WorkflowVersion)) is None


def test_empty_draft_is_saved_but_cannot_activate(client):
    http, _ = client
    workflow = create(http, graph={"nodes": [], "edges": []})
    path = f"/workflows/{workflow['id']}"
    assert not http.post(path + "/validate").json()["valid"]
    assert http.post(path + "/activate").json()["code"] == "invalid_workflow"
    assert http.put(path, json={"name": "Missing version"}).status_code == 422
    assert http.put(path, json={"version": 1, "graph": None}).status_code == 422


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("cycle", "cycle"),
        ("dangling", "dangling_edge"),
        ("duplicate_node", "duplicate_node"),
        ("duplicate_edge", "duplicate_edge"),
        ("duplicate_link", "duplicate_link"),
        ("roots", "multiple_starts"),
    ],
)
def test_invalid_graphs(kind, expected):
    data = graph()
    if kind == "cycle":
        data["edges"].append({"id": "e3", "source": "end", "target": "start"})
    if kind == "dangling":
        data["edges"][0]["target"] = "absent"
    if kind == "duplicate_node":
        data["nodes"].append(data["nodes"][0])
    if kind == "duplicate_edge":
        data["edges"][1]["id"] = "e1"
    if kind == "duplicate_link":
        data["edges"].append({**data["edges"][0], "id": "another"})
    if kind == "roots":
        data["edges"] = []
    report = validate_graph(Graph.model_validate(data), Trigger())
    assert not report.valid and expected in {error.code for error in report.errors}
    assert report.order == []


def test_agent_dependency_unknown_inactive_and_unavailable(client):
    http, _ = client
    workflow = create(http)
    path = f"/workflows/{workflow['id']}"
    set_agents(lambda request: httpx.Response(404, json={"code": "not_found"}))
    assert "unknown_agent" in {
        e["code"] for e in http.post(path + "/validate").json()["errors"]
    }
    set_agents(
        lambda request: httpx.Response(
            200, json={"status": "disabled", "type_key": "transform"}
        )
    )
    assert http.post(path + "/activate").status_code == 422
    assert "inactive_agent" in {
        e["code"] for e in http.post(path + "/validate").json()["errors"]
    }

    def unavailable(request):
        raise httpx.ConnectError("offline")

    set_agents(unavailable)
    response = http.post(path + "/validate")
    assert (
        response.status_code == 503
        and response.json()["code"] == "agent_service_unavailable"
    )


def test_condition_branch_validation(client):
    http, _ = client
    data = graph()
    condition_id = data["nodes"][0]["data"]["agent_id"]
    set_agents(
        lambda request: httpx.Response(
            200,
            json={
                "status": "active",
                "type_key": (
                    "condition"
                    if request.url.path.endswith(condition_id)
                    else "transform"
                ),
            },
        )
    )
    workflow = create(http, graph=data)
    path = f"/workflows/{workflow['id']}"
    assert "invalid_branches" in {
        e["code"] for e in http.post(path + "/validate").json()["errors"]
    }
    data["edges"] = [
        {"id": "yes", "source": "start", "target": "middle", "sourceHandle": "true"},
        {"id": "no", "source": "start", "target": "end", "sourceHandle": "false"},
    ]
    assert http.put(path, json={"version": 1, "graph": data}).status_code == 200
    assert http.post(path + "/validate").json()["valid"]
    # A transform cannot expose condition handles.
    set_agents(
        lambda request: httpx.Response(
            200, json={"status": "active", "type_key": "transform"}
        )
    )
    assert "unexpected_branch" in {
        e["code"] for e in http.post(path + "/validate").json()["errors"]
    }


def test_cron_and_telegram_trigger_validation(client):
    http, _ = client
    for schedule, valid in (
        ("*/5 * * * *", True),
        ("invalid", False),
        ("0 0 0 * * *", False),
    ):
        workflow = create(http, trigger={"type": "cron", "schedule": schedule})
        assert (
            http.post(f"/workflows/{workflow['id']}/validate").json()["valid"] is valid
        )
    workflow = create(http, trigger={"type": "telegram"})
    assert not http.post(f"/workflows/{workflow['id']}/validate").json()["valid"]
    connection = str(uuid4())
    data = graph()
    first = data["nodes"][0]["data"]["agent_id"]
    set_agents(
        lambda request: httpx.Response(
            200,
            json={
                "status": "active",
                "type_key": (
                    "telegram_trigger"
                    if request.url.path.endswith(first)
                    else "transform"
                ),
                "connection_id": connection,
            },
        )
    )
    workflow = create(
        http, graph=data, trigger={"type": "telegram", "connection_id": connection}
    )
    path = f"/workflows/{workflow['id']}"
    assert http.post(path + "/validate").json()["valid"]
    assert (
        http.put(
            path,
            json={
                "version": 1,
                "trigger": {"type": "telegram", "connection_id": str(uuid4())},
            },
        ).status_code
        == 200
    )
    assert "connection_mismatch" in {
        e["code"] for e in http.post(path + "/validate").json()["errors"]
    }


def test_user_isolation_and_bearer_forwarding(client):
    http, _ = client
    workflow = create(http)
    expected = http.headers["authorization"]

    def upstream(request):
        assert request.headers["authorization"] == expected
        return httpx.Response(200, json={"status": "active", "type_key": "transform"})

    set_agents(upstream)
    assert http.post(f"/workflows/{workflow['id']}/validate").json()["valid"]
    http.headers["Authorization"] = f"Bearer {token()}"
    assert http.get("/workflows").json() == []
    path = f"/workflows/{workflow['id']}"
    for method, suffix, body in (
        ("GET", "", None),
        ("PUT", "", {"version": 1, "name": "other"}),
        ("DELETE", "", None),
        ("GET", "/versions", None),
        ("POST", "/validate", None),
        ("POST", "/activate", None),
        ("POST", "/deactivate", None),
    ):
        assert http.request(method, path + suffix, json=body).status_code == 404
    del http.headers["Authorization"]
    assert (
        http.get("/workflows", headers={"X-User-ID": workflow["user_id"]}).status_code
        == 401
    )


def test_malformed_agent_response_returns_dependency_error(client):
    http, _ = client
    workflow = create(http)
    set_agents(lambda request: httpx.Response(200, json={"unexpected": "response"}))
    response = http.post(f"/workflows/{workflow['id']}/validate")
    assert response.status_code == 503
    assert response.json()["code"] == "agent_service_unavailable"


def test_scheduler_listing_requires_scoped_service_token(client):
    http, _ = client
    assert http.get("/internal/workflows/active").status_code == 403
    claims = jwt.decode(
        http.headers["authorization"].split()[1],
        get_settings().jwt_secret,
        algorithms=["HS256"],
        audience=get_settings().jwt_audience,
    )
    claims.update(type="service", service="execution-service", scope="scheduler")
    headers = {
        "Authorization": "Bearer "
        + jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")
    }
    assert http.get("/internal/workflows/active", headers=headers).status_code == 200
    claims["scope"] = "other"
    headers = {
        "Authorization": "Bearer "
        + jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")
    }
    assert http.get("/internal/workflows/active", headers=headers).status_code == 403
