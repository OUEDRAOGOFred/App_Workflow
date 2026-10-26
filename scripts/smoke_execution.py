"""Run actual Celery workflows and Redis triggers against demo integrations."""

import base64
import secrets
import time

import httpx
from smoke import BASE, request


def main():
    credentials = {
        "email": f"engine-{secrets.token_hex(6)}@example.com",
        "password": secrets.token_urlsafe(24),
    }
    request("/api/v1/auth/register", credentials, expected=201)
    token = request("/api/v1/auth/login", credentials)["access_token"]

    def api(path, body=None, expected=200, method=None):
        return request("/api/v1" + path, body, token, expected, method)

    assert api("/integrations/status")[
        "mock_integrations"
    ], "Execution smoke requires demo integrations"
    google = api("/integrations/google/oauth/start")["connection"]
    telegram = api("/integrations/telegram/connect", {"name": "Bot moteur démo"}, 201)
    agents = []
    workflows = []

    def agent(kind, config, connection=None):
        body = {"name": kind, "type_key": kind, "config": config}
        if connection:
            body["connection_id"] = connection["id"]
        value = api("/agents", body, 201)
        agents.append(value)
        api(f"/agents/{value['id']}/activate", {})
        return value

    def node(identifier, value, **extra):
        return {"id": identifier, "data": {"agent_id": value["id"], **extra}}

    def workflow(name, nodes, edges, trigger=None):
        value = api(
            "/workflows",
            {
                "name": name,
                "graph": {"nodes": nodes, "edges": edges},
                "trigger": trigger or {"type": "manual"},
            },
            201,
        )
        workflows.append(value)
        return value

    def wait(identifier):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            result = api(f"/executions/{identifier}")
            if result["status"] in {"success", "failed", "cancelled"}:
                return result
            time.sleep(0.5)
        raise AssertionError("Execution did not finish")

    def successful(identifier):
        result = wait(identifier)
        assert result["status"] == "success", result.get("error_code")
        return result

    trigger_agent = agent("telegram_trigger", {}, telegram)
    classifier = agent("document_classifier", {"confidence_threshold": 0.5})
    sheets = agent(
        "sheets_append_row",
        {
            "spreadsheet_id": "demo-sheet",
            "range": "Documents!A:C",
            "values": [
                "{{ nodes.classify.output.label }}",
                "{{ nodes.classify.output.confidence }}",
            ],
        },
        google,
    )
    mail = agent(
        "gmail_send",
        {
            "to": "student@example.com",
            "subject": "Document reçu",
            "body": "Classe {{ nodes.classify.output.label }}",
        },
        google,
    )
    reply = agent(
        "telegram_send",
        {
            "chat_id": "{{ nodes.receive.output.chat_id }}",
            "text": "Classe {{ nodes.classify.output.label }}",
        },
        telegram,
    )
    names = ["receive", "classify", "save", "mail", "reply"]
    nodes = [
        node(name, value)
        for name, value in zip(
            names, [trigger_agent, classifier, sheets, mail, reply], strict=True
        )
    ]
    edges = [
        {"id": f"e{i}", "source": names[i], "target": names[i + 1]} for i in range(4)
    ]
    flow = workflow(
        "Telegram → classification → Sheets → Gmail → réponse",
        nodes,
        edges,
        {"type": "telegram", "connection_id": telegram["id"]},
    )
    payload = {
        "chat_id": "demo-chat",
        "text": "Facture TVA montant total 125 euros",
        "document": {
            "filename": "facture.txt",
            "content_base64": base64.b64encode(b"Facture TVA total euros 125").decode(),
        },
    }
    queued = api(
        f"/workflows/{flow['id']}/run",
        {"payload": payload, "event_id": "manual-one"},
        202,
    )
    result = successful(queued["id"])
    assert [step["status"] for step in result["steps"]] == ["success"] * 5
    assert (
        api(
            f"/workflows/{flow['id']}/run",
            {"payload": payload, "event_id": "manual-one"},
            202,
        )["id"]
        == queued["id"]
    )
    with httpx.Client(
        base_url=BASE, headers={"Authorization": "Bearer " + token}, timeout=15
    ) as client:
        with client.stream(
            "GET", f"/api/v1/executions/{queued['id']}/stream"
        ) as response:
            response.raise_for_status()
            text = "".join(response.iter_text())
            assert "event: complete" in text and "event: step" in text
    assert api(f"/agents/{classifier['id']}")["last_run_at"] is not None
    api(f"/workflows/{flow['id']}/activate", {})
    api(
        f"/connections/{telegram['id']}/actions/inject_update",
        {"params": {"chat_id": "demo-chat", "text": "Facture TVA total 42 euros"}},
    )
    assert api(f"/connections/{telegram['id']}/poll", {})["published"] == 1
    deadline = time.monotonic() + 30
    automatic = None
    while time.monotonic() < deadline:
        automatic = next(
            (
                item
                for item in api(f"/executions?workflow_id={flow['id']}")
                if item["source"] == "telegram"
            ),
            None,
        )
        if automatic:
            break
        time.sleep(0.5)
    assert automatic, "Telegram event did not schedule execution"
    successful(automatic["id"])
    api(f"/workflows/{flow['id']}/deactivate", {})
    rows = api(
        f"/connections/{google['id']}/actions/sheets_read",
        {"params": {"spreadsheet_id": "demo-sheet", "range": "Documents!A:B"}},
    )["values"]
    assert len(rows) == 2 and all(row[0] == "facture" for row in rows)
    assert len(api(f"/connections/{google['id']}/outbox")) == 2
    assert len(api(f"/connections/{telegram['id']}/outbox")) == 2
    # Async tests are available before creating an active agent.
    draft = api(
        "/agent-drafts",
        {
            "step": 4,
            "data": {"type_key": "transform", "config": {"mapping": {"ok": True}}},
        },
        201,
    )
    test = api(f"/agent-drafts/{draft['id']}/test", {"input": {}}, 202)
    assert successful(test["id"])["steps"][0]["output"] == {"ok": True}
    api(f"/agent-drafts/{draft['id']}", expected=204, method="DELETE")
    condition = agent("condition", {"field": "take", "operator": "eq", "value": True})
    yes = agent("transform", {"mapping": {"selected": "yes"}})
    no = agent("transform", {"mapping": {"selected": "no"}})
    join = agent("transform", {"mapping": {"result": "{{ input.selected }}"}})
    branch = workflow(
        "Branche conditionnelle",
        [
            node("condition", condition),
            node("yes", yes),
            node("no", no),
            node("join", join),
        ],
        [
            {"id": "a", "source": "condition", "target": "yes", "sourceHandle": "true"},
            {"id": "b", "source": "condition", "target": "no", "sourceHandle": "false"},
            {"id": "c", "source": "yes", "target": "join"},
            {"id": "d", "source": "no", "target": "join"},
        ],
    )
    job = api(f"/workflows/{branch['id']}/run", {"payload": {"take": True}}, 202)
    results = successful(job["id"])
    assert (
        next(step for step in results["steps"] if step["node_id"] == "no")["status"]
        == "skipped"
    )
    assert results["steps"][-1]["output"] == {"result": "yes"}
    webhook = workflow("Webhook", [node("start", yes)], [], {"type": "webhook"})
    api(f"/workflows/{webhook['id']}/activate", {})
    key = api(f"/workflows/{webhook['id']}/webhook-key", {})
    headers = {"X-Webhook-Secret": key["secret"], "Idempotency-Key": "webhook-one"}
    with httpx.Client(base_url=BASE, timeout=15) as client:
        response = client.post(key["url"], json={"text": "Facture"}, headers=headers)
        response.raise_for_status()
        hook = response.json()
        response = client.post(key["url"], json={"text": "Facture"}, headers=headers)
        assert response.json()["id"] == hook["id"]
    successful(hook["id"])
    api(f"/workflows/{webhook['id']}/deactivate", {})
    cron = workflow(
        "Cron minute",
        [node("start", yes)],
        [],
        {"type": "cron", "schedule": "* * * * *"},
    )
    api(f"/workflows/{cron['id']}/activate", {})
    deadline = time.monotonic() + 30
    scheduled = None
    while time.monotonic() < deadline:
        scheduled = next(
            (
                item
                for item in api(f"/executions?workflow_id={cron['id']}")
                if item["source"] == "cron"
            ),
            None,
        )
        if scheduled:
            break
        time.sleep(0.5)
    assert scheduled, "Cron did not schedule execution"
    successful(scheduled["id"])
    api(f"/workflows/{cron['id']}/deactivate", {})
    for value in workflows:
        api(f"/workflows/{value['id']}", expected=204, method="DELETE")
    for value in agents:
        api(f"/agents/{value['id']}", expected=204, method="DELETE")
    for value in (google, telegram):
        api(f"/connections/{value['id']}", expected=204, method="DELETE")
    print(
        "Execution smoke OK: Celery, workflow, Telegram event, branches, "
        "SSE, idempotency, draft test, webhook, cron"
    )


if __name__ == "__main__":
    main()
