import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

os.environ["JWT_SECRET"] = "test-key-only-never-use-in-production-123456789"

from app.core.config import get_settings
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as http:
        original_http, original_redis = app.state.http, app.state.redis
        original_business = app.state.business
        app.state.business = {}
        app.state.redis = AsyncMock()
        app.state.redis.eval.return_value = 1
        yield http
        app.state.http, app.state.redis = original_http, original_redis
        app.state.business = original_business


def transport(handler):
    return httpx.AsyncClient(
        base_url="http://auth-service:8000", transport=httpx.MockTransport(handler)
    )


def access_token(kind="access", **changes):
    settings = get_settings()
    payload = {
        "sub": "user-1",
        "type": kind,
        "jti": "unique-session",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    payload.update(changes)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def test_forward_login_and_preserve_response(client):
    def upstream(request):
        assert request.url.path == "/auth/login"
        assert request.method == "POST"
        assert b"password" in request.content
        assert "x-user-id" not in request.headers
        return httpx.Response(200, json={"access_token": "opaque"})

    app.state.http = transport(upstream)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.com", "password": "password-for-test"},
        headers={"X-User-ID": "forged"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "opaque"
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers


def test_access_protection(client):
    app.state.http = transport(
        lambda request: httpx.Response(200, json={"id": "user-1"})
    )
    for token in (
        "invalid",
        access_token("refresh"),
        access_token(exp=datetime.now(UTC) - timedelta(minutes=1)),
    ):
        assert (
            client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token()}"}
        ).status_code
        == 200
    )


def test_rate_limit_and_redis_failure(client):
    app.state.redis.eval.return_value = 10000
    assert client.post("/api/v1/auth/login", json={}).status_code == 429
    app.state.redis.eval.side_effect = RedisConnectionError()
    assert client.post("/api/v1/auth/login", json={}).status_code == 503


def test_unavailable_service(client):
    def failed(request):
        raise httpx.ConnectError("not available")

    app.state.http = transport(failed)
    assert client.post("/api/v1/auth/login", json={}).status_code == 503
    app.state.http = transport(lambda request: httpx.Response(500))
    assert client.post("/api/v1/auth/login", json={}).status_code == 502


def test_allowed_routes_and_body_size(client):
    assert client.get("/api/v1/auth/login").status_code == 405
    assert client.post("/api/v1/auth/unknown", json={}).status_code == 404
    assert client.post("/api/v1/auth/login", content="x" * 17000).status_code == 413


def test_cors_and_aggregated_openapi(client):
    app.state.http = transport(
        lambda request: httpx.Response(
            200,
            json={
                "openapi": "3.1.0",
                "info": {},
                "paths": {"/auth/login": {"post": {}}, "/health": {}},
                "components": {"schemas": {"Credentials": {"type": "object"}}},
            },
        )
    )
    response = client.get("/openapi.json", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "/api/v1/auth/login" in response.json()["paths"]
    assert "/health" not in response.json()["paths"]
    assert "auth_Credentials" in response.json()["components"]["schemas"]
    response = client.get(
        "/openapi.json", headers={"Origin": "https://untrusted.example"}
    )
    assert "access-control-allow-origin" not in response.headers


def test_health_checks_upstream(client):
    app.state.http = transport(
        lambda request: httpx.Response(200, json={"status": "ok"})
    )
    assert client.get("/health").status_code == 200
    app.state.redis.ping.side_effect = RedisConnectionError()
    assert client.get("/health").status_code == 503


def test_business_routing_auth_query_and_body(client):
    def upstream(request):
        assert request.url.path == "/agents"
        assert request.url.params["status"] == "active"
        assert request.headers["authorization"].startswith("Bearer ")
        assert "x-user-id" not in request.headers
        return httpx.Response(200, json=[{"id": "agent-1"}])

    app.state.business = {"agent": transport(upstream)}
    assert client.get("/api/v1/agents").status_code == 401
    headers = {"Authorization": f"Bearer {access_token()}", "X-User-ID": "forged"}
    response = client.get("/api/v1/agents?status=active", headers=headers)
    assert response.status_code == 200
    assert response.json() == [{"id": "agent-1"}]
    assert (
        client.post(
            "/api/v1/agents", content="x" * 1048577, headers=headers
        ).status_code
        == 413
    )
    assert client.get("/api/v1/unknown", headers=headers).status_code == 404
    app.state.business["agent"] = transport(lambda request: httpx.Response(204))
    assert client.delete("/api/v1/agents/agent-1", headers=headers).status_code == 204


def test_openapi_namespaces_refs_and_security_across_services(client):
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/agents": {
                "post": {
                    "operationId": "create",
                    "security": [{"HTTPBearer": []}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Body"}
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {"Body": {"type": "object"}},
            "securitySchemes": {"HTTPBearer": {"type": "http", "scheme": "bearer"}},
        },
    }
    app.state.http = transport(
        lambda request: httpx.Response(
            200,
            json={
                "paths": {"/auth/login": {"post": {"operationId": "login"}}},
                "components": {"schemas": {"Body": {"type": "string"}}},
            },
        )
    )
    app.state.business = {
        "agent": transport(lambda request: httpx.Response(200, json=schema))
    }
    response = client.get("/openapi.json")
    assert response.status_code == 200
    result = response.json()
    operation = result["paths"]["/api/v1/agents"]["post"]
    assert operation["operationId"] == "agent_create"
    assert operation["security"] == [{"agent_HTTPBearer": []}]
    assert (
        operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/agent_Body"
    )
    assert result["components"]["schemas"]["auth_Body"]["type"] == "string"
    assert result["components"]["schemas"]["agent_Body"]["type"] == "object"


def test_business_dependency_errors_and_health(client):
    def unavailable(request):
        raise httpx.ConnectError("offline")

    app.state.business = {"workflow": transport(unavailable)}
    headers = {"Authorization": f"Bearer {access_token()}"}
    assert client.get("/api/v1/workflows", headers=headers).status_code == 503
    app.state.business["workflow"] = transport(
        lambda request: httpx.Response(
            503,
            json={
                "detail": "Impossible de vérifier les agents",
                "code": "agent_service_unavailable",
            },
        )
    )
    assert (
        client.post("/api/v1/workflows/id/validate", headers=headers).json()["code"]
        == "agent_service_unavailable"
    )
    app.state.http = transport(lambda request: httpx.Response(200))
    assert client.get("/health").status_code == 503


def test_oauth_callback_is_public_but_other_integrations_require_access(client):
    app.state.business = {
        "integration": transport(
            lambda request: httpx.Response(
                400, json={"detail": "invalid state", "code": "invalid_oauth_state"}
            )
        )
    }
    response = client.get(
        "/api/v1/integrations/google/oauth/callback?state=unknown&code=one-time"
    )
    assert (
        response.status_code == 400 and response.json()["code"] == "invalid_oauth_state"
    )
    assert client.get("/api/v1/integrations/google/oauth/start").status_code == 401
    assert client.post("/api/v1/integrations/google/oauth/callback").status_code == 401
    assert client.get("/api/v1/connections").status_code == 401


def test_classifier_forwards_multipart_and_protects_routes(client):
    def upstream(request):
        assert request.url.path == "/classify"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b"Facture" in request.content
        return httpx.Response(200, json={"label": "facture"})

    app.state.business = {"classifier": transport(upstream)}
    assert client.get("/api/v1/classes").status_code == 401
    response = client.post(
        "/api/v1/classify",
        files={"file": ("a.txt", b"Facture")},
        headers={"Authorization": f"Bearer {access_token()}"},
    )
    assert response.status_code == 200 and response.json()["label"] == "facture"


def test_oauth_cookie_forwarding_does_not_share_upstream_cookie_jar(client):
    seen = []

    def upstream(request):
        seen.append(request.headers.get("cookie", ""))
        if request.url.path.endswith("start"):
            return httpx.Response(
                200,
                json={"authorization_url": "https://accounts.google.com/"},
                headers={
                    "Set-Cookie": (
                        "workflow_oauth_binding=browser-nonce; Path=/; "
                        "HttpOnly; SameSite=lax"
                    )
                },
            )
        return httpx.Response(
            400, json={"detail": "test", "code": "invalid_oauth_state"}
        )

    app.state.business = {"integration": transport(upstream)}
    response = client.get(
        "/api/v1/integrations/google/oauth/start",
        headers={"Authorization": f"Bearer {access_token()}"},
    )
    assert "set-cookie" in response.headers and seen == [""]
    client.get("/api/v1/integrations/google/oauth/callback?state=test")
    assert seen[-1] == "workflow_oauth_binding=browser-nonce"
    client.cookies.clear()
    client.get("/api/v1/integrations/google/oauth/callback?state=other")
    assert seen[-1] == ""


def test_execution_routes_and_public_webhook_headers(client):
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(202, json={"id": "execution"})

    app.state.business = {"execution": transport(upstream)}
    identifier = "11111111-1111-4111-8111-111111111111"
    assert (
        client.post(f"/api/v1/workflows/{identifier}/run", json={}).status_code == 401
    )
    headers = {
        "Authorization": f"Bearer {access_token()}",
        "Idempotency-Key": "same-event",
    }
    assert (
        client.post(
            f"/api/v1/workflows/{identifier}/run", json={}, headers=headers
        ).status_code
        == 202
    )
    assert (
        seen[-1].url.path.endswith("/run")
        and seen[-1].headers["idempotency-key"] == "same-event"
    )
    assert (
        client.post(
            f"/api/v1/agents/{identifier}/test", json={}, headers=headers
        ).status_code
        == 202
    )
    assert (
        client.post(
            f"/api/v1/webhooks/{identifier}",
            json={},
            headers={"X-Webhook-Secret": "secret", "Idempotency-Key": "event"},
        ).status_code
        == 202
    )
    assert seen[-1].headers["x-webhook-secret"] == "secret"


def test_sse_is_forwarded_without_buffering_and_checks_access(client):
    closed = []

    class Events(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'id: 1\nevent: complete\ndata: {"status":"success"}\n\n'

        async def aclose(self):
            closed.append(True)

    def upstream(request):
        assert request.headers["last-event-id"] == "0"
        return httpx.Response(
            200, stream=Events(), headers={"Content-Type": "text/event-stream"}
        )

    app.state.business = {"execution": transport(upstream)}
    path = "/api/v1/executions/11111111-1111-4111-8111-111111111111/stream"
    assert client.get(path).status_code == 401
    response = client.get(
        path,
        headers={"Authorization": f"Bearer {access_token()}", "Last-Event-ID": "0"},
    )
    assert response.status_code == 200 and "event: complete" in response.text
    assert response.headers["x-accel-buffering"] == "no" and closed
