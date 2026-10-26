import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

os.environ["JWT_SECRET"] = "test-key-only-never-use-in-production-123456789"
os.environ["DATABASE_URL"] = "sqlite://"

from app.core.database import Base, get_db
from app.core.security import encode_token, hasher
from app.main import app
from app.models.user import RefreshSession, User


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def database():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = database
    with TestClient(app) as test_client:
        yield test_client, engine
    app.dependency_overrides.clear()
    engine.dispose()


ACCOUNT = {"email": "student@example.com", "password": "a-strong-test-password"}


def login(client):
    assert client.post("/auth/register", json=ACCOUNT).status_code == 201
    response = client.post("/auth/login", json=ACCOUNT)
    assert response.status_code == 200
    return response.json()


def test_register_login_profile_and_hash(client):
    http, engine = client
    tokens = login(http)
    profile = http.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert profile.status_code == 200
    assert profile.json()["email"] == ACCOUNT["email"]
    assert "password_hash" not in profile.json()
    with Session(engine) as db:
        user = db.scalar(select(User))
        assert user.password_hash != ACCOUNT["password"]
        assert hasher.verify(user.password_hash, ACCOUNT["password"])
        session = db.scalar(select(RefreshSession))
        assert len(session.token_hash) == 64
        assert tokens["refresh_token"] != session.token_hash


def test_duplicate_case_insensitive_and_invalid_input(client):
    http, _ = client
    http.post("/auth/register", json=ACCOUNT)
    response = http.post(
        "/auth/register", json={**ACCOUNT, "email": ACCOUNT["email"].upper()}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "email_exists"
    bad = http.post("/auth/register", json={"email": "invalid", "password": "short"})
    assert bad.status_code == 422
    assert "short" not in bad.text
    assert "invalid" not in [e["field"] for e in bad.json()["errors"]]


def test_bad_login_and_missing_token(client):
    http, _ = client
    login(http)
    assert (
        http.post(
            "/auth/login", json={**ACCOUNT, "password": "incorrect-password"}
        ).status_code
        == 401
    )
    assert (
        http.post(
            "/auth/login", json={**ACCOUNT, "email": "nobody@example.com"}
        ).status_code
        == 401
    )
    assert http.get("/auth/me").status_code == 401


def test_refresh_rotates_and_rejects_replay_and_token_confusion(client):
    http, _ = client
    tokens = login(http)
    refreshed = http.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert (
        http.post(
            "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        http.post(
            "/auth/refresh", json={"refresh_token": tokens["access_token"]}
        ).status_code
        == 401
    )
    assert (
        http.get(
            "/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
        ).status_code
        == 401
    )
    assert (
        http.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
        ).status_code
        == 200
    )


def test_expired_and_tampered_tokens(client):
    http, _ = client
    token, _ = encode_token("unknown", "access", timedelta(seconds=-1))
    for value in (token, token + "tampered"):
        assert (
            http.get(
                "/auth/me", headers={"Authorization": f"Bearer {value}"}
            ).status_code
            == 401
        )


def test_health_checks_dependencies(client):
    http, _ = client
    with (
        patch("app.main.get_engine") as engine,
        patch("app.main.Redis.from_url") as redis,
    ):
        engine.return_value.connect.return_value.__enter__.return_value = MagicMock()
        redis.return_value.__enter__.return_value.ping.return_value = True
        assert http.get("/health").status_code == 200
        redis.return_value.__enter__.return_value.ping.side_effect = ConnectionError()
        assert http.get("/health").status_code == 503


def test_alembic_upgrade_and_downgrade(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'migration.db'}"}
    base = Path(__file__).resolve().parents[1]
    for revision in ("head", "base", "head"):
        command = "downgrade" if revision == "base" else "upgrade"
        subprocess.run(
            [sys.executable, "-m", "alembic", command, revision],
            cwd=base,
            env=env,
            check=True,
        )
