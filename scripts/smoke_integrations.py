"""Phase-3 demo round trip; requires mock external integrations, uses real APIs."""

import base64
import secrets

import httpx
from smoke import BASE, request


def main():
    credentials = {
        "email": f"integrations-{secrets.token_hex(6)}@example.com",
        "password": secrets.token_urlsafe(24),
    }
    request("/api/v1/auth/register", credentials, expected=201)
    token = request("/api/v1/auth/login", credentials)["access_token"]

    def api(path, body=None, expected=200, method=None):
        return request("/api/v1" + path, body, token, expected, method)

    assert api("/integrations/status")["mock_integrations"], (
        "Use MOCK_INTEGRATIONS=true for this test; no real messages " "should be sent"
    )
    google = api("/integrations/google/oauth/start")["connection"]
    telegram = api("/integrations/telegram/connect", {"name": "Bot de test"}, 201)
    smtp = api(
        "/connections", {"type": "gmail_smtp", "name": "Gmail secours de test"}, 201
    )
    http = api(
        "/connections",
        {
            "type": "http",
            "name": "HTTP de test",
            "credentials": {"base_url": "https://example.invalid/"},
        },
        201,
    )

    def action(connection, name, params=None):
        return api(
            f"/connections/{connection['id']}/actions/{name}", {"params": params or {}}
        )

    for connection in (google, telegram, smtp, http):
        assert api(f"/connections/{connection['id']}/test", {})["mock"] is True
    document = (
        "FACTURE numéro 42. Montant total 125 euros TTC, TVA, paiement "
        "sous trente jours."
    )
    action(
        telegram,
        "inject_update",
        {
            "chat_id": "demo-chat",
            "text": document,
            "document": {
                "filename": "facture.txt",
                "content_base64": base64.b64encode(document.encode()).decode(),
            },
        },
    )
    events = api(f"/connections/{telegram['id']}/poll", {})
    assert events["published"] == 1
    assert api(f"/connections/{telegram['id']}/poll", {})["published"] == 0
    with httpx.Client(
        base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=180
    ) as client:
        response = client.post(
            "/api/v1/classify", json={"text": events["events"][0]["payload"]["text"]}
        )
        response.raise_for_status()
        result = response.json()
        assert result["label"] == "facture", result.get("reason")
        response = client.post(
            "/api/v1/classify",
            files={"file": ("facture.txt", document.encode(), "text/plain")},
        )
        response.raise_for_status()
        assert response.json()["label"] == "facture"
    assert "facture" in api("/classes")["classes"]
    values = [[result["label"], result["confidence"], "facture.txt"]]
    action(
        google,
        "sheets_append",
        {"spreadsheet_id": "demo-sheet", "range": "Documents!A:C", "values": values},
    )
    assert (
        action(
            google,
            "sheets_read",
            {"spreadsheet_id": "demo-sheet", "range": "Documents!A1:C1"},
        )["values"]
        == values
    )
    action(
        google,
        "sheets_update",
        {
            "spreadsheet_id": "demo-sheet",
            "range": "Documents!C1",
            "values": [["document-reçu.txt"]],
        },
    )
    action(
        google, "sheets_create", {"spreadsheet_id": "demo-sheet", "title": "Archives"}
    )
    assert api(f"/integrations/sheets/{google['id']}/spreadsheets")["spreadsheets"]
    assert (
        len(
            api(f"/integrations/sheets/{google['id']}/spreadsheets/demo-sheet/sheets")[
                "sheets"
            ]
        )
        == 2
    )
    assert api(f"/integrations/gmail/{google['id']}/labels")["labels"]
    assert action(google, "gmail_read", {"unread": True})["messages"]
    attachment = action(
        google,
        "gmail_attachment",
        {"message_id": "demo-mail", "attachment_id": "demo-attachment"},
    )
    assert base64.b64decode(attachment["content_base64"])
    summary = f"Classe : {result['label']}, score : {result['confidence']}"
    mail = {
        "to": "student@example.com",
        "subject": "Classification du document",
        "body": summary,
    }
    assert action(google, "gmail_send", mail)["mock"] is True
    assert action(smtp, "gmail_send", mail)["mock"] is True
    assert (
        action(telegram, "send_message", {"chat_id": "demo-chat", "text": summary})[
            "mock"
        ]
        is True
    )
    assert (
        action(
            http,
            "request",
            {"path": "/example", "method": "POST", "body": {"label": result["label"]}},
        )["mock"]
        is True
    )
    for connection in (google, telegram, smtp, http):
        assert api(f"/connections/{connection['id']}/outbox")
    forbidden = api(
        f"/connections/{google['id']}/actions/gmail_add_label",
        {"params": {"message_id": "demo-mail", "label_id": "INBOX"}},
        403,
    )
    assert forbidden["code"] == "insufficient_scope"
    # Connection references are checked through HTTP at agent creation/activation.
    agent = api(
        "/agents",
        {
            "name": "Telegram connecté",
            "type_key": "telegram_trigger",
            "connection_id": telegram["id"],
            "config": {},
        },
        201,
    )
    assert api(f"/agents/{agent['id']}/activate", {})["status"] == "active"
    api(f"/agents/{agent['id']}", expected=204, method="DELETE")
    for connection in (google, telegram, smtp, http):
        api(f"/connections/{connection['id']}", expected=204, method="DELETE")
    print(
        f"Integration smoke OK ({result['backend']}): "
        "demo receipt/classify/Sheets/mail/reply"
    )


if __name__ == "__main__":
    main()
