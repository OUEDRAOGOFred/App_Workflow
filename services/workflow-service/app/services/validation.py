import httpx
from fastapi import HTTPException, Request

from app.models.entities import Workflow
from app.schemas.workflows import (
    AgentReference,
    Graph,
    Trigger,
    ValidationIssue,
    ValidationReport,
)
from app.services.graph import validate_graph


def validate_workflow(workflow: Workflow, request: Request) -> ValidationReport:
    graph = Graph.model_validate(workflow.graph)
    report = validate_graph(graph, Trigger.model_validate(workflow.trigger))
    if not report.valid:
        return report
    headers = {"Authorization": request.headers["authorization"]}
    agents: dict[str, AgentReference] = {}
    for node in graph.nodes:
        identifier = str(node.data.agent_id)
        if identifier in agents:
            continue
        try:
            response = request.app.state.agents.get(
                f"/agents/{identifier}", headers=headers
            )
            if response.status_code == 404:
                report.errors.append(
                    ValidationIssue(
                        code="unknown_agent",
                        detail="Agent absent ou inaccessible",
                        node_id=node.id,
                    )
                )
                continue
            response.raise_for_status()
            agents[identifier] = AgentReference.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            raise HTTPException(
                503,
                {
                    "detail": "Impossible de vérifier les agents",
                    "code": "agent_service_unavailable",
                },
            ) from None
    for node in graph.nodes:
        agent = agents.get(str(node.data.agent_id))
        if not agent:
            continue
        if agent.status != "active":
            report.errors.append(
                ValidationIssue(
                    code="inactive_agent",
                    detail="L’agent doit être actif",
                    node_id=node.id,
                )
            )
        outgoing = [edge for edge in graph.edges if edge.source == node.id]
        if agent.type_key == "condition":
            handles = [edge.sourceHandle for edge in outgoing]
            if (
                sorted(h for h in handles if h is not None) != ["false", "true"]
                or len(handles) != 2
            ):
                report.errors.append(
                    ValidationIssue(
                        code="invalid_branches",
                        detail="Deux branches requises : true et false",
                        node_id=node.id,
                    )
                )
        elif any(edge.sourceHandle is not None for edge in outgoing):
            report.errors.append(
                ValidationIssue(
                    code="unexpected_branch",
                    detail="Les sorties true/false sont réservées aux conditions",
                    node_id=node.id,
                )
            )
        if agent.type_key == "telegram_trigger":
            if workflow.trigger["type"] != "telegram" or report.order[0] != node.id:
                report.errors.append(
                    ValidationIssue(
                        code="invalid_telegram_start",
                        detail="Départ Telegram trigger incompatible",
                        node_id=node.id,
                    )
                )
            elif str(agent.connection_id) != workflow.trigger.get("connection_id"):
                report.errors.append(
                    ValidationIssue(
                        code="connection_mismatch",
                        detail="Les connexions Telegram doivent correspondre",
                        node_id=node.id,
                    )
                )
    if workflow.trigger["type"] == "telegram":
        first = next(node for node in graph.nodes if node.id == report.order[0])
        agent = agents.get(str(first.data.agent_id))
        if agent and agent.type_key != "telegram_trigger":
            report.errors.append(
                ValidationIssue(
                    code="invalid_telegram_start",
                    detail="Le départ doit être un agent Telegram trigger",
                    node_id=first.id,
                )
            )
    if workflow.trigger["type"] == "gmail":
        first = next(node for node in graph.nodes if node.id == report.order[0])
        agent = agents.get(str(first.data.agent_id))
        if agent and (
            agent.type_key != "gmail_read"
            or str(agent.connection_id) != workflow.trigger.get("connection_id")
        ):
            report.errors.append(
                ValidationIssue(
                    code="invalid_gmail_start",
                    detail="Départ Gmail et connexion identique requis",
                    node_id=first.id,
                )
            )
    report.valid = not report.errors
    if not report.valid:
        report.order = []
    return report
