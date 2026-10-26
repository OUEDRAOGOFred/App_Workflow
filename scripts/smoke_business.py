"""Exercise phase-2 APIs via the real gateway, PostgreSQL and interservice HTTP."""

import secrets

from smoke import request


def account():
    credentials = {
        "email": f"business-{secrets.token_hex(6)}@example.com",
        "password": secrets.token_urlsafe(24),
    }
    request("/api/v1/auth/register", credentials, expected=201)
    return request("/api/v1/auth/login", credentials)["access_token"]


def main():
    assert request("/health") == {"status": "ok", "service": "api-gateway"}
    token = account()

    def api(path, body=None, expected=200, method=None):
        return request("/api/v1" + path, body, token, expected, method)

    catalogue = api("/agent-types")
    assert len(catalogue) == 11
    agents = []
    for kind, config in (
        ("transform", {"mapping": {"text": "Facture numéro 42"}}),
        ("document_classifier", {"confidence_threshold": 0.5}),
        ("llm", {"prompt": "Résume le document reçu"}),
        ("transform", {"mapping": {"result": "{{ nodes.classify.output.label }}"}}),
    ):
        agent = api("/agents", {"name": kind, "type_key": kind, "config": config}, 201)
        agents.append(agent)
        assert api(f"/agents/{agent['id']}/activate", {})["status"] == "active"
    assert len(api("/agents?status=active")) == 4
    draft = api(
        "/agent-drafts",
        {
            "step": 3,
            "data": {"type_key": "gmail_send", "config": {"to": "student@example.com"}},
        },
        201,
    )
    assert api(f"/agent-drafts/{draft['id']}")["step"] == 3
    assert (
        api(
            f"/agent-drafts/{draft['id']}",
            {"step": 4, "data": {"type_key": "gmail_send"}},
            method="PUT",
        )["step"]
        == 4
    )
    names = ["start", "classify", "summarize", "finish"]
    graph = {
        "nodes": [
            {"id": name, "data": {"agent_id": agent["id"]}}
            for name, agent in zip(names, agents, strict=True)
        ],
        "edges": [
            {"id": f"e{i}", "source": names[i], "target": names[i + 1]}
            for i in range(3)
        ],
    }
    workflow = api(
        "/workflows",
        {"name": "Phase 2 — validation de quatre agents", "graph": graph},
        201,
    )
    path = f"/workflows/{workflow['id']}"
    assert api(path + "/validate", {}) == {"valid": True, "errors": [], "order": names}
    active = api(path + "/activate", {})
    assert active["is_active"] and active["version"] == 2
    api(path, {"version": 1, "name": "Modification périmée"}, 409, "PUT")
    edited = api(path, {"version": 2, "name": "Modification valide"}, method="PUT")
    assert edited["version"] == 3 and not edited["is_active"]
    history = api(path + "/versions")
    assert [item["version"] for item in history] == [3, 2, 1]
    assert history[-1]["snapshot"]["name"] == workflow["name"]
    graph["edges"].append({"id": "cycle", "source": "finish", "target": "start"})
    api(path, {"version": 3, "graph": graph}, method="PUT")
    invalid = api(path + "/validate", {})
    assert not invalid["valid"] and "cycle" in {
        error["code"] for error in invalid["errors"]
    }
    api(path + "/activate", {}, 422)
    second = account()
    assert request("/api/v1/agents", token=second) == []
    request("/api/v1" + path, token=second, expected=404)
    request(f"/api/v1/agent-drafts/{draft['id']}", token=second, expected=404)
    docs = request("/openapi.json")
    for route in (
        "/agents",
        "/agent-types",
        "/agent-drafts",
        "/workflows",
        "/auth/login",
    ):
        assert "/api/v1" + route in docs["paths"]
    api(path, expected=204, method="DELETE")
    for agent in agents:
        api(f"/agents/{agent['id']}", expected=204, method="DELETE")
    api(f"/agent-drafts/{draft['id']}", expected=204, method="DELETE")
    print("Business smoke OK: CRUD, drafts, ownership, DAG, versions, OpenAPI")


if __name__ == "__main__":
    main()
