from collections import deque

from croniter import croniter

from app.schemas.workflows import Graph, Trigger, ValidationIssue, ValidationReport


def validate_graph(graph: Graph, trigger: Trigger) -> ValidationReport:
    errors: list[ValidationIssue] = []

    def issue(code: str, detail: str, node_id: str | None = None):
        errors.append(ValidationIssue(code=code, detail=detail, node_id=node_id))

    nodes = {node.id: node for node in graph.nodes}
    if not nodes:
        issue("missing_start", "Le graphe doit contenir un nœud de départ")
    if len(nodes) != len(graph.nodes):
        issue("duplicate_node", "Les identifiants des nœuds doivent être uniques")
    edge_ids = set()
    links = set()
    children: dict[str, list[str]] = {node: [] for node in nodes}
    indegree = dict.fromkeys(nodes, 0)
    for edge in graph.edges:
        if edge.id in edge_ids:
            issue("duplicate_edge", "Les identifiants des liens doivent être uniques")
        edge_ids.add(edge.id)
        if edge.source not in nodes or edge.target not in nodes:
            issue("dangling_edge", "Un lien référence un nœud absent")
            continue
        key = (edge.source, edge.target, edge.sourceHandle)
        if key in links:
            issue("duplicate_link", "Deux liens identiques sont interdits")
        links.add(key)
        children[edge.source].append(edge.target)
        indegree[edge.target] += 1
    starts = [node for node, degree in indegree.items() if degree == 0]
    if nodes and len(starts) != 1:
        issue(
            "missing_start" if not starts else "multiple_starts",
            "Un seul nœud de départ est requis",
        )
    queue = deque(starts)
    order = []
    remaining = indegree.copy()
    while queue:
        node = queue.popleft()
        order.append(node)
        for target in children[node]:
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    if len(order) != len(nodes):
        issue("cycle", "Le graphe contient un cycle")
    if trigger.type == "cron":
        if (
            not trigger.schedule
            or len(trigger.schedule.split()) != 5
            or not croniter.is_valid(trigger.schedule)
        ):
            issue(
                "invalid_cron", "Une expression cron valide à cinq champs est requise"
            )
    elif trigger.schedule is not None:
        issue(
            "unexpected_schedule",
            "La planification concerne uniquement un déclencheur cron",
        )
    if trigger.type in {"telegram", "gmail"} and not trigger.connection_id:
        issue("connection_required", "Le déclencheur requiert une connexion")
    return ValidationReport(
        valid=not errors, errors=errors, order=order if not errors else []
    )
