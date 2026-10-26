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
from app.models.entities import Agent, AgentDraft, AgentType
from app.services.catalog import CATALOG


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
    with Session(engine) as db:
        db.add_all([AgentType(**item) for item in CATALOG])
        db.commit()

    def database():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as http:
        http.headers["Authorization"] = f"Bearer {token()}"
        original = app.state.integrations
        mock = httpx.Client(
            base_url="http://integration-service:8000",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"type": "telegram", "status": "active"}
                )
            ),
        )
        app.state.integrations = mock
        yield http, engine
        app.state.integrations.close()
        app.state.integrations = original
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


BODY = {
    "type_key": "transform",
    "name": "Préparer les données",
    "config": {"mapping": {"text": "Facture 100 EUR"}},
}


def create(http, **changes):
    response = http.post("/agents", json={**BODY, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_crud_filters_and_activation(client):
    http, engine = client
    catalogue = http.get("/agent-types").json()
    assert len(catalogue) == 11
    assert {item["key"] for item in catalogue} == {item["key"] for item in CATALOG}
    agent = create(http)
    identifier = agent["id"]
    assert agent["status"] == "draft" and agent["last_run_at"] is None
    assert http.get(f"/agents/{identifier}").json()["name"] == BODY["name"]
    assert http.post(f"/agents/{identifier}/activate").json()["status"] == "active"
    assert len(http.get("/agents?status=active&type_key=transform&limit=1").json()) == 1
    assert http.get("/agents?status=disabled").json() == []
    assert (
        http.put(f"/agents/{identifier}", json={"name": "Modifié"}).json()["status"]
        == "draft"
    )
    assert http.post(f"/agents/{identifier}/deactivate").json()["status"] == "disabled"
    assert http.delete(f"/agents/{identifier}").status_code == 204
    assert http.get(f"/agents/{identifier}").status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(Agent)) is None


def test_connection_required_and_clearable(client):
    http, _ = client
    agent = create(http, type_key="telegram_trigger", config={})
    path = f"/agents/{agent['id']}"
    response = http.post(path + "/activate")
    assert (
        response.status_code == 422 and response.json()["code"] == "connection_required"
    )
    connection = str(uuid4())
    assert http.put(path, json={"connection_id": connection}).status_code == 200
    assert http.post(path + "/activate").status_code == 200
    assert http.put(path, json={"connection_id": None}).json()["connection_id"] is None
    assert http.post(path + "/activate").status_code == 422


def test_invalid_type_schema_secrets_and_null_update(client):
    http, _ = client
    for body in (
        {**BODY, "type_key": "unknown"},
        {**BODY, "config": {}},
        {**BODY, "config": {"mapping": [], "unexpected": True}},
        {**BODY, "config": {"mapping": {"nested": {"token": "never-store-this"}}}},
        {**BODY, "name": "  "},
        {**BODY, "status": "active"},
    ):
        response = http.post("/agents", json=body)
        assert response.status_code == 422
        assert "never-store-this" not in response.text
    identifier = create(http)["id"]
    assert http.put(f"/agents/{identifier}", json={"config": None}).status_code == 422
    assert (
        http.put(f"/agents/{identifier}", json={"type_key": "gmail_send"}).status_code
        == 422
    )


def test_user_isolation_and_forged_identity(client):
    http, _ = client
    agent = create(http)
    draft = http.post(
        "/agent-drafts", json={"step": 2, "data": {"type_key": "gmail_send"}}
    ).json()
    http.headers["Authorization"] = f"Bearer {token()}"
    assert http.get("/agents").json() == []
    assert http.get("/agent-drafts").json() == []
    for method, suffix, body in (
        ("GET", "", None),
        ("PUT", "", {"name": "intrusion"}),
        ("POST", "/activate", None),
        ("DELETE", "", None),
    ):
        assert (
            http.request(
                method, f"/agents/{agent['id']}{suffix}", json=body
            ).status_code
            == 404
        )
    for method, body in (
        ("GET", None),
        ("PUT", {"step": 3, "data": {}}),
        ("DELETE", None),
    ):
        assert (
            http.request(method, f"/agent-drafts/{draft['id']}", json=body).status_code
            == 404
        )
    del http.headers["Authorization"]
    assert (
        http.get("/agents", headers={"X-User-ID": agent["user_id"]}).status_code == 401
    )
    assert (
        http.get(
            "/agent-types", headers={"Authorization": f"Bearer {token(kind='refresh')}"}
        ).status_code
        == 401
    )


def test_wizard_draft_round_trip_and_validation(client):
    http, engine = client
    draft = http.post(
        "/agent-drafts",
        json={
            "step": 3,
            "data": {
                "type_key": "gmail_send",
                "name": "Brouillon",
                "config": {"to": "example@example.com"},
            },
        },
    )
    assert draft.status_code == 201
    identifier = draft.json()["id"]
    assert http.get(f"/agent-drafts/{identifier}").json()["data"]["config"] == {
        "to": "example@example.com"
    }
    assert (
        http.put(
            f"/agent-drafts/{identifier}",
            json={
                "step": 4,
                "data": {
                    "type_key": "gmail_send",
                    "config": {"to": "someone@example.com"},
                },
            },
        ).json()["step"]
        == 4
    )
    assert len(http.get("/agent-drafts?limit=1").json()) == 1
    assert http.post("/agent-drafts", json={"step": 6}).status_code == 422
    assert (
        http.post("/agent-drafts", json={"data": {"type_key": "missing"}}).status_code
        == 422
    )
    assert (
        http.post(
            "/agent-drafts", json={"data": {"credentials": {"token": "secret"}}}
        ).status_code
        == 422
    )
    assert (
        http.post(
            "/agent-drafts", json={"data": {"config": {"refresh_token": "secret"}}}
        ).status_code
        == 422
    )
    assert http.delete(f"/agent-drafts/{identifier}").status_code == 204
    with Session(engine) as db:
        assert db.scalar(select(AgentDraft)) is None


def test_connection_ownership_type_status_and_failure(client):
    http, _ = client
    identifier = create(http, type_key="telegram_trigger", config={})["id"]
    path = f"/agents/{identifier}"
    connection = str(uuid4())

    def replace(handler):
        app.state.integrations.close()
        app.state.integrations = httpx.Client(
            base_url="http://integration-service:8000",
            transport=httpx.MockTransport(handler),
        )

    replace(lambda request: httpx.Response(404))
    assert (
        http.put(path, json={"connection_id": connection}).json()["code"]
        == "invalid_connection"
    )
    replace(
        lambda request: httpx.Response(200, json={"type": "google", "status": "active"})
    )
    assert (
        http.put(path, json={"connection_id": connection}).json()["code"]
        == "connection_type_mismatch"
    )

    def upstream(request):
        assert request.headers["authorization"] == http.headers["authorization"]
        return httpx.Response(200, json={"type": "telegram", "status": "expired"})

    replace(upstream)
    assert http.put(path, json={"connection_id": connection}).status_code == 200
    assert http.post(path + "/activate").json()["code"] == "connection_unavailable"
    replace(lambda request: httpx.Response(503))
    assert (
        http.post(path + "/activate").json()["code"]
        == "integration_service_unavailable"
    )


def test_agent_run_result_requires_service_token_and_preserves_disabled_state(client):
    http, _ = client
    agent = create(http)
    path = f"/internal/agents/{agent['id']}/run-result"
    body = {"execution_id": str(uuid4()), "status": "failed"}
    assert http.post(path, json=body).status_code == 403
    claims = jwt.decode(
        http.headers["authorization"].split()[1],
        get_settings().jwt_secret,
        algorithms=["HS256"],
        audience=get_settings().jwt_audience,
    )
    claims.update(type="service", service="execution-service")
    headers = {
        "Authorization": "Bearer "
        + jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")
    }
    assert http.post(path, json=body, headers=headers).status_code == 200
    assert http.get(f"/agents/{agent['id']}").json()["status"] == "error"
    body["status"] = "success"
    assert http.post(path, json=body, headers=headers).status_code == 200
    assert http.get(f"/agents/{agent['id']}").json()["last_run_at"] is not None
    http.post(f"/agents/{agent['id']}/deactivate")
    body["status"] = "failed"
    http.post(path, json=body, headers=headers)
    assert http.get(f"/agents/{agent['id']}").json()["status"] == "disabled"
