import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

os.environ["JWT_SECRET"] = "phase-three-test-key-never-use-in-production-123456789"

from app.core.config import get_settings

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["MOCK_INTEGRATIONS"] = "true"
os.environ["POLLING_ENABLED"] = "false"
from cryptography.fernet import Fernet

os.environ["FERNET_KEY"] = Fernet.generate_key().decode()

import base64
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from app.core.crypto import decrypt, encrypt
from app.core.database import Base, get_db
from app.main import app
from app.models.entities import Artifact, Connection, PollCursor
from app.services.google_auth import SCOPES
from google.auth.exceptions import RefreshError
from oauthlib.oauth2 import InvalidClientError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


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


class FakeLock:
    def acquire(self, blocking=False):
        return True

    def release(self):
        return None


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.events = []

    def setex(self, key, ttl, value):
        self.values[key] = value

    def getdel(self, key):
        return self.values.pop(key, None)

    def lock(self, *args, **kwargs):
        return FakeLock()

    def xadd(self, stream, event, **kwargs):
        self.events.append((stream, event))
        return str(len(self.events))

    def ping(self):
        return True


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
        original_http, original_redis = app.state.http, app.state.redis
        app.state.redis = FakeRedis()

        def no_network(request):
            raise AssertionError("Mock mode must not use the network")

        app.state.http = httpx.Client(transport=httpx.MockTransport(no_network))
        yield http, engine
        app.state.http.close()
        app.state.http, app.state.redis = original_http, original_redis
    app.dependency_overrides.clear()
    engine.dispose()


def create(http, kind="google", credentials=None):
    response = http.post(
        "/connections",
        json={"type": kind, "name": "Test " + kind, "credentials": credentials or {}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def action(http, identifier, action, params=None):
    return http.post(
        f"/connections/{identifier}/actions/{action}", json={"params": params or {}}
    )


def real_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "mock_integrations", False)
    monkeypatch.setattr(settings, "google_client_id", "test-google-client")
    monkeypatch.setattr(settings, "google_client_secret", "test-google-secret")


def make_real(engine, identifier, credentials):
    with Session(engine) as db:
        connection = db.get(Connection, identifier)
        connection.is_mock = False
        connection.encrypted_credentials = encrypt(credentials)
        db.commit()


def replace_transport(handler):
    app.state.http.close()
    app.state.http = httpx.Client(transport=httpx.MockTransport(handler))


def test_connection_encryption_owner_isolation_and_revoke(client):
    http, engine = client
    secret = "private-test-credential-value"
    connection = create(http, "telegram", {"token": secret})
    identifier = connection["id"]
    assert "credentials" not in connection and "encrypted_credentials" not in connection
    assert secret not in http.get("/connections").text
    with Session(engine) as db:
        stored = db.get(Connection, identifier)
        assert secret not in stored.encrypted_credentials
        assert decrypt(stored.encrypted_credentials)["token"] == secret
    assert http.post(f"/connections/{identifier}/test").json()["mock"] is True
    original = http.headers["Authorization"]
    http.headers["Authorization"] = f"Bearer {token()}"
    assert http.get("/connections").json() == []
    for method, suffix, body in (
        ("GET", "", None),
        ("DELETE", "", None),
        ("POST", "/test", None),
        ("POST", "/poll", None),
        ("GET", "/outbox", None),
        (
            "POST",
            "/actions/send_message",
            {"params": {"chat_id": "1", "text": "hello"}},
        ),
    ):
        assert (
            http.request(
                method, f"/connections/{identifier}{suffix}", json=body
            ).status_code
            == 404
        )
    http.headers["Authorization"] = original
    assert http.delete(f"/connections/{identifier}").status_code == 204
    assert http.get(f"/connections/{identifier}").status_code == 404


def test_mock_status_catalogue_and_google_same_connection(client):
    http, _ = client
    assert http.get("/integrations/status").json()["mock_integrations"] is True
    assert {item["key"] for item in http.get("/integrations/connectors").json()} == {
        "google",
        "telegram",
        "gmail_smtp",
        "http",
    }
    result = http.get("/integrations/google/oauth/start").json()
    assert result["mock"] is True and result["authorization_url"] is None
    identifier = result["connection"]["id"]
    assert (
        http.get("/integrations/google/oauth/start").json()["connection"]["id"]
        == identifier
    )
    assert http.get(f"/integrations/gmail/{identifier}/labels").json()["labels"]
    assert (
        http.get(f"/integrations/sheets/{identifier}/spreadsheets").json()[
            "spreadsheets"
        ][0]["id"]
        == "demo-sheet"
    )
    assert (
        http.get(
            f"/integrations/sheets/{identifier}/spreadsheets/demo-sheet/sheets"
        ).json()["sheets"][0]["title"]
        == "Documents"
    )


def test_mock_sheets_append_update_read_create_and_revoke(client):
    http, engine = client
    identifier = create(http)["id"]
    params = {
        "spreadsheet_id": "demo-sheet",
        "range": "Documents!A:C",
        "values": [["facture", 0.93, "file.pdf"]],
    }
    assert action(http, identifier, "sheets_append", params).json()["updatedRows"] == 1
    assert action(http, identifier, "sheets_append", params).status_code == 200
    values = action(
        http,
        identifier,
        "sheets_read",
        {"spreadsheet_id": "demo-sheet", "range": "Documents!A1:C2"},
    ).json()["values"]
    assert len(values) == 2 and values[0][0] == "facture"
    assert (
        action(
            http,
            identifier,
            "sheets_update",
            {
                "spreadsheet_id": "demo-sheet",
                "range": "Documents!B1",
                "values": [[0.99]],
            },
        ).status_code
        == 200
    )
    assert action(
        http,
        identifier,
        "sheets_read",
        {"spreadsheet_id": "demo-sheet", "range": "Documents!B1"},
    ).json()["values"] == [[0.99]]
    assert (
        action(
            http,
            identifier,
            "sheets_create",
            {"spreadsheet_id": "demo-sheet", "title": "Archives"},
        ).status_code
        == 200
    )
    assert (
        action(
            http,
            identifier,
            "sheets_create",
            {"spreadsheet_id": "demo-sheet", "title": "Archives"},
        ).status_code
        == 409
    )
    assert (
        action(
            http,
            identifier,
            "sheets_read",
            {"spreadsheet_id": "demo-sheet", "range": "Documents!A999999"},
        ).status_code
        == 422
    )
    assert http.delete(f"/connections/{identifier}").status_code == 204
    with Session(engine) as db:
        assert db.scalar(select(Artifact)) is None


def test_mock_gmail_attachments_outbox_scopes_and_poll_dedup(client):
    http, _ = client
    identifier = create(http)["id"]
    messages = action(
        http, identifier, "gmail_read", {"query": "from:supplier@example.com is:unread"}
    ).json()["messages"]
    assert len(messages) == 1
    attachment = action(
        http,
        identifier,
        "gmail_attachment",
        {"message_id": "demo-mail", "attachment_id": "demo-attachment"},
    ).json()
    assert b"Facture" in base64.b64decode(attachment["content_base64"])
    assert (
        action(
            http,
            identifier,
            "gmail_send",
            {"to": "student@example.com", "subject": "Résumé", "body": "facture"},
        ).status_code
        == 200
    )
    assert len(http.get(f"/connections/{identifier}/outbox").json()) == 1
    assert (
        action(
            http,
            identifier,
            "gmail_add_label",
            {"message_id": "demo-mail", "label_id": "INBOX"},
        ).json()["code"]
        == "insufficient_scope"
    )
    assert http.post(f"/connections/{identifier}/poll").json()["published"] == 1
    assert http.post(f"/connections/{identifier}/poll").json()["published"] == 0
    assert app.state.redis.events[0][0] == "integration.events"
    assert app.state.redis.events[0][1]["type"] == "gmail.new_mail"


def test_telegram_mock_injection_document_and_poll(client):
    http, _ = client
    connection = http.post(
        "/integrations/telegram/connect", json={"name": "Mon bot"}
    ).json()
    identifier = connection["id"]
    assert (
        action(
            http,
            identifier,
            "inject_update",
            {
                "text": "facture",
                "document": {
                    "filename": "a.txt",
                    "content_base64": base64.b64encode(b"Facture TVA").decode(),
                },
            },
        ).status_code
        == 200
    )
    event = http.post(f"/connections/{identifier}/poll").json()
    assert (
        event["published"] == 1 and event["events"][0]["payload"]["text"] == "facture"
    )
    assert http.post(f"/connections/{identifier}/poll").json()["published"] == 0
    assert (
        action(
            http,
            identifier,
            "send_message",
            {"chat_id": "42", "text": "Classe facture"},
        ).json()["mock"]
        is True
    )
    assert (
        http.get(f"/connections/{identifier}/outbox").json()[0]["action"]["text"]
        == "Classe facture"
    )


def test_invalid_action_and_credentials_are_not_echoed(client):
    http, _ = client
    assert (
        http.post(
            "/connections",
            json={
                "name": "x",
                "type": "telegram",
                "credentials": {"unknown": "secret"},
            },
        ).status_code
        == 422
    )
    identifier = create(http)["id"]
    assert action(http, identifier, "missing").status_code == 422
    assert (
        action(
            http,
            identifier,
            "gmail_send",
            {"to": "not-an-email", "subject": "x", "body": "y"},
        ).status_code
        == 422
    )
    assert (
        action(
            http,
            identifier,
            "gmail_send",
            {"to": "a@example.com", "subject": "x\r\nInjected: secret", "body": "y"},
        ).status_code
        == 422
    )
    assert (
        action(
            http,
            identifier,
            "gmail_send",
            {
                "to": "a@example.com",
                "subject": "x",
                "body": "y",
                "attachment": {"filename": "x.txt", "content_base64": "?"},
            },
        ).status_code
        == 422
    )
    del http.headers["Authorization"]
    assert http.get("/connections").status_code == 401


def test_oauth_pkce_minimal_scopes_state_single_use_callback(client, monkeypatch):
    http, engine = client
    real_settings(monkeypatch)
    result = http.get("/integrations/google/oauth/start").json()
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert set(query["scope"][0].split()) == set(SCOPES)
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"]
    state = query["state"][0]
    credentials = SimpleNamespace(
        token="oauth-private-access",
        refresh_token="oauth-private-refresh",
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )
    oauth = MagicMock()
    oauth.credentials = credentials
    with patch("app.services.oauth.flow", return_value=oauth):
        del http.headers["Authorization"]
        response = http.get(
            "/integrations/google/oauth/callback",
            params={"state": state, "code": "one-time-code"},
        )
    assert response.status_code == 200, response.text
    assert "oauth-private" not in response.text
    with Session(engine) as db:
        connection = db.get(Connection, response.json()["id"])
        assert not connection.is_mock
        assert (
            decrypt(connection.encrypted_credentials)["refresh_token"]
            == "oauth-private-refresh"
        )
    assert (
        http.get(
            "/integrations/google/oauth/callback",
            params={"state": state, "code": "replay"},
        ).json()["code"]
        == "invalid_oauth_state"
    )


@pytest.mark.parametrize("error", ["access_denied", "redirect_uri_mismatch"])
def test_oauth_errors_help_and_state_validation(client, monkeypatch, error):
    http, _ = client
    real_settings(monkeypatch)
    result = http.get("/integrations/google/oauth/start").json()
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    response = http.get(
        "/integrations/google/oauth/callback", params={"state": state, "error": error}
    )
    assert response.status_code == 400 and response.json()["code"] == error
    assert response.json()["help_url"].endswith("/checklist")
    assert (
        http.get(
            "/integrations/google/oauth/callback",
            params={"state": "invented", "code": "code"},
        ).status_code
        == 400
    )


def test_google_refresh_failure_marks_expired(client, monkeypatch):
    http, engine = client
    identifier = create(http)["id"]
    make_real(
        engine,
        identifier,
        {
            "token": "old-access",
            "refresh_token": "private-refresh",
            "expiry": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        },
    )
    real_settings(monkeypatch)
    with patch(
        "app.services.google_auth.Credentials.refresh",
        side_effect=RefreshError("revoked"),
    ):
        response = http.post(f"/connections/{identifier}/test")
    assert (
        response.status_code == 409 and response.json()["code"] == "connection_expired"
    )
    assert http.get(f"/connections/{identifier}").json()["status"] == "expired"


def test_google_refresh_success_is_encrypted_and_persisted(client, monkeypatch):
    http, engine = client
    identifier = create(http)["id"]
    make_real(
        engine,
        identifier,
        {
            "token": "old-access",
            "refresh_token": "private-refresh",
            "expiry": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        },
    )
    real_settings(monkeypatch)

    def refresh(credentials, request):
        credentials.token = "new-private-access"
        credentials.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    with (
        patch("app.services.google_auth.Credentials.refresh", refresh),
        patch(
            "app.connectors.google.GoogleConnector.test_connection",
            return_value={"ok": True, "mock": False},
        ),
    ):
        assert http.post(f"/connections/{identifier}/test").status_code == 200
    with Session(engine) as db:
        connection = db.get(Connection, identifier)
        assert "new-private-access" not in connection.encrypted_credentials
        assert (
            decrypt(connection.encrypted_credentials)["token"] == "new-private-access"
        )


def test_google_sdk_gmail_send_and_sheets_append(client, monkeypatch):
    http, engine = client
    identifier = create(http)["id"]
    make_real(
        engine,
        identifier,
        {
            "token": "private-token",
            "refresh_token": "refresh-token",
            "expiry": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    real_settings(monkeypatch)
    sdk = MagicMock()
    sdk.users().messages().send().execute.return_value = {"id": "sent-mail"}
    sdk.spreadsheets().values().append().execute.return_value = {
        "updates": {"updatedRows": 1}
    }
    with patch("app.connectors.google.GoogleConnector.service", return_value=sdk):
        response = action(
            http,
            identifier,
            "gmail_send",
            {"to": "student@example.com", "subject": "Résumé", "body": "Facture"},
        )
        assert response.json()["id"] == "sent-mail"
        response = action(
            http,
            identifier,
            "sheets_append",
            {"spreadsheet_id": "sheet", "range": "A:C", "values": [["facture", 0.9]]},
        )
        assert response.status_code == 200
    assert (
        sdk.spreadsheets().values().append.call_args.kwargs["valueInputOption"] == "RAW"
    )
    raw = sdk.users().messages().send.call_args.kwargs["body"]["raw"]
    assert b"student@example.com" in base64.urlsafe_b64decode(raw)


def test_telegram_real_long_poll_download_without_token_leak(client, monkeypatch):
    http, engine = client
    identifier = create(http, "telegram")["id"]
    secret = "telegram-private-test-secret"
    make_real(engine, identifier, {"token": secret})
    real_settings(monkeypatch)
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith("getMe"):
            result = {"id": 1, "username": "bot"}
        elif request.url.path.endswith("getUpdates"):
            params = json.loads(request.content)
            assert params["timeout"] == 20
            result = [
                {
                    "update_id": 42,
                    "message": {
                        "chat": {"id": 7},
                        "document": {"file_id": "file-id", "file_name": "facture.txt"},
                    },
                }
            ]
        elif request.url.path.endswith("getFile"):
            result = {"file_path": "documents/a.txt", "file_size": 7}
        elif "/file/" in request.url.path:
            return httpx.Response(200, content=b"Facture")
        else:
            raise AssertionError("Unexpected method")
        return httpx.Response(200, json={"ok": True, "result": result})

    replace_transport(transport)
    assert http.post(f"/connections/{identifier}/test").status_code == 200
    response = http.post(f"/connections/{identifier}/poll")
    assert response.status_code == 200, response.text
    assert secret not in response.text
    payload = response.json()["events"][0]["payload"]
    assert base64.b64decode(payload["document"]["content_base64"]) == b"Facture"
    with Session(engine) as db:
        assert db.get(PollCursor, identifier).state["offset"] == 43


def test_http_blocks_private_network_and_pins_public_dns(client, monkeypatch):
    http, engine = client
    identifier = create(
        http,
        "http",
        {
            "base_url": "https://api.example.test/",
            "authorization": "Bearer private-http-secret",
        },
    )["id"]
    make_real(
        engine,
        identifier,
        {
            "base_url": "https://api.example.test/",
            "authorization": "Bearer private-http-secret",
        },
    )
    real_settings(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    assert action(http, identifier, "request").json()["code"] == "blocked_host"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )

    def transport(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "api.example.test"
        assert request.extensions["sni_hostname"] == "api.example.test"
        assert request.headers["authorization"] == "Bearer private-http-secret"
        return httpx.Response(200, json={"ok": True})

    replace_transport(transport)
    assert action(http, identifier, "request").json()["data"] == {"ok": True}
    assert (
        action(
            http, identifier, "request", {"url": "https://other.example.test/"}
        ).json()["code"]
        == "origin_mismatch"
    )


def test_smtp_real_send_uses_starttls_and_app_password(client, monkeypatch):
    http, engine = client
    identifier = create(http, "gmail_smtp")["id"]
    make_real(
        engine,
        identifier,
        {"email": "student@example.com", "app_password": "private-app-password"},
    )
    real_settings(monkeypatch)
    smtp = MagicMock()
    with patch("app.connectors.gmail_smtp.smtplib.SMTP", return_value=smtp):
        response = action(
            http,
            identifier,
            "gmail_send",
            {"to": "receiver@example.com", "subject": "Test", "body": "Bonjour"},
        )
    assert response.status_code == 200 and response.json()["mock"] is False
    assert smtp.__enter__().starttls.called
    smtp.__enter__().login.assert_called_once_with(
        "student@example.com", "private-app-password"
    )
    assert smtp.__enter__().send_message.called


def test_mock_mode_never_uses_existing_real_connection(client):
    http, engine = client
    identifier = create(http, "telegram")["id"]
    make_real(engine, identifier, {"token": "private"})
    assert (
        http.post(f"/connections/{identifier}/test").json()["code"] == "mode_mismatch"
    )


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


def test_http_response_cannot_reflect_stored_credentials(client, monkeypatch):
    http, engine = client
    credentials = {
        "base_url": "https://api.example.test/",
        "authorization": "Bearer private-echo-secret",
    }
    identifier = create(http, "http", credentials)["id"]
    make_real(engine, identifier, credentials)
    real_settings(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    replace_transport(
        lambda request: httpx.Response(
            200, json={"echo": request.headers["authorization"]}
        )
    )
    response = action(http, identifier, "request")
    assert response.status_code == 200 and "private-echo-secret" not in response.text
    assert response.json()["data"]["echo"] == "[REDACTED]"


def test_poll_lock_prevents_concurrent_consumer(client):
    http, _ = client
    identifier = create(http, "telegram")["id"]
    lock = MagicMock()
    lock.acquire.return_value = False
    app.state.redis.lock = lambda *args, **kwargs: lock
    assert http.post(f"/connections/{identifier}/poll").json()["code"] == "poll_busy"


def test_one_file_connector_is_discovered_creatable_and_redacted(
    client, tmp_path, monkeypatch
):
    import app.connectors
    from app.connectors.registry import REGISTRY, load_connectors

    plugin = tmp_path / "echo_demo.py"
    plugin.write_text(
        """from app.connectors.base import BaseConnector
from app.connectors.registry import register
from app.schemas.actions import Empty
@register
class EchoConnector(BaseConnector):
    key="echo_demo"
    credential_fields={"api_key"}
    actions={"ping":Empty}
    def test_connection(self):
        return {"ok":True}
    def execute(self,action,params):
        return {"pong":True,"echo":self.ctx.credentials["api_key"]}
"""
    )
    monkeypatch.setattr(app.connectors, "__path__", [str(tmp_path)])
    try:
        load_connectors()
        http, _ = client
        identifier = create(http, "echo_demo", {"api_key": "private-plugin-key"})["id"]
        response = action(http, identifier, "ping")
        assert response.status_code == 200
        assert response.json() == {"pong": True, "echo": "[REDACTED]"}
    finally:
        REGISTRY.pop("echo_demo", None)
        sys.modules.pop("app.connectors.echo_demo", None)


def test_smtp_imap_reads_without_marking_seen_and_returns_attachments(
    client, monkeypatch
):
    from email.message import EmailMessage

    http, engine = client
    identifier = create(http, "gmail_smtp")["id"]
    assert (
        action(http, identifier, "gmail_read", {"query": "is:unread"}).json()["code"]
        == "unsupported_imap_filter"
    )
    make_real(
        engine,
        identifier,
        {"email": "student@example.com", "app_password": "private-imap-password"},
    )
    real_settings(monkeypatch)
    message = EmailMessage()
    message["From"] = "supplier@example.com"
    message["Subject"] = "Facture"
    message.set_content("Facture 42")
    message.add_attachment(
        b"document", maintype="text", subtype="plain", filename="a.txt"
    )
    imap = MagicMock()
    imap.__enter__.return_value = imap
    imap.select.return_value = ("OK", [b"1"])

    def command(action_name, *args):
        if action_name == "search":
            assert "UNSEEN" in args and "FROM" in args
            return "OK", [b"7"]
        assert action_name == "fetch" and "BODY.PEEK" in args[-1]
        return "OK", [(b"7", message.as_bytes())]

    imap.uid.side_effect = command
    with patch("app.connectors.gmail_smtp.imaplib.IMAP4_SSL", return_value=imap):
        response = action(
            http,
            identifier,
            "gmail_read",
            {"unread": True, "from_email": "supplier@example.com"},
        )
    assert response.status_code == 200, response.text
    mail = response.json()["messages"][0]
    assert "Facture 42" in mail["text"]
    assert base64.b64decode(mail["attachments"][0]["content_base64"]) == b"document"


def test_background_polling_recovers_after_database_outage():
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.polling import worker

    connection = MagicMock()
    connection.__enter__().scalars.return_value = []
    sleeps = []

    async def pause(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError()

    with (
        patch("app.services.polling.get_engine"),
        patch(
            "app.services.polling.Session",
            side_effect=[RuntimeError("offline"), connection],
        ) as session,
        patch("app.services.polling.asyncio.sleep", new=AsyncMock(side_effect=pause)),
    ):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker(SimpleNamespace()))
    assert session.call_count == 2


def test_oauth_callback_is_bound_to_initiating_browser(client, monkeypatch):
    http, _ = client
    real_settings(monkeypatch)
    response = http.get("/integrations/google/oauth/start")
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    http.cookies.clear()
    with patch("app.services.oauth.flow") as flow:
        result = http.get(
            "/integrations/google/oauth/callback",
            params={"state": state, "code": "victim-code"},
        )
    assert (
        result.status_code == 400 and result.json()["code"] == "invalid_oauth_browser"
    )
    flow.assert_not_called()


def test_failed_http_connection_test_sets_error_and_can_recover(client, monkeypatch):
    http, engine = client
    credentials = {"base_url": "https://api.example.test/"}
    identifier = create(http, "http", credentials)["id"]
    make_real(engine, identifier, credentials)
    real_settings(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    replace_transport(
        lambda request: httpx.Response(401, json={"error": "unauthorized"})
    )
    assert http.post(f"/connections/{identifier}/test").json()["ok"] is False
    assert http.get(f"/connections/{identifier}").json()["status"] == "error"
    replace_transport(lambda request: httpx.Response(200, json={"ok": True}))
    assert http.post(f"/connections/{identifier}/test").json()["ok"] is True
    assert http.get(f"/connections/{identifier}").json()["status"] == "active"


def test_http_connections_do_not_reuse_other_accounts_cookies(client, monkeypatch):
    http, engine = client
    credentials = {"base_url": "https://api.example.test/"}
    identifier = create(http, "http", credentials)["id"]
    make_real(engine, identifier, credentials)
    real_settings(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    requests = []

    def transport(request):
        requests.append(request)
        assert not request.headers.get("cookie")
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"Set-Cookie": "server_session=private-other-account; Path=/"},
        )

    replace_transport(transport)
    assert action(http, identifier, "request").status_code == 200
    assert app.state.http.cookies.get("server_session") == "private-other-account"
    assert action(http, identifier, "request").status_code == 200
    assert len(requests) == 2


def test_stateless_http_request_needs_auth_and_respects_network_policy(
    client, monkeypatch
):
    http, engine = client
    assert (
        http.post(
            "/integrations/http/request", json={"url": "https://example.test/"}
        ).json()["mock"]
        is True
    )
    with Session(engine) as db:
        assert db.scalar(select(Connection)) is None
    assert http.post("/integrations/http/request", json={}).status_code == 422
    real_settings(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ],
    )
    assert (
        http.post(
            "/integrations/http/request", json={"url": "http://local.test/"}
        ).json()["code"]
        == "blocked_host"
    )
    http.headers.pop("Authorization")
    assert (
        http.post(
            "/integrations/http/request", json={"url": "https://example.test/"}
        ).status_code
        == 401
    )


def test_oauth_invalid_client_is_actionable_without_exposing_provider_secrets(
    client, monkeypatch
):
    http, engine = client
    real_settings(monkeypatch)
    start = http.get("/integrations/google/oauth/start").json()
    state = parse_qs(urlsplit(start["authorization_url"]).query)["state"][0]
    oauth = MagicMock()
    oauth.fetch_token.side_effect = InvalidClientError(
        description="Rejected credential: private-provider-secret"
    )
    with patch("app.services.oauth.flow", return_value=oauth):
        response = http.get(
            "/integrations/google/oauth/callback",
            params={"state": state, "code": "one-use-authorization-code"},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "oauth_invalid_client"
    assert "même client Web" in response.json()["detail"]
    assert response.json()["help_url"].endswith("/checklist")
    assert "private-provider-secret" not in response.text
    assert "one-use-authorization-code" not in response.text
    with Session(engine) as db:
        assert db.scalar(select(Connection)) is None
    replay = http.get(
        "/integrations/google/oauth/callback",
        params={"state": state, "code": "one-use-authorization-code"},
    )
    assert replay.json()["code"] == "invalid_oauth_state"
