import asyncio
import time
from contextlib import suppress
from uuid import uuid4

import httpx
from redis import Redis
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import logger
from app.models.entities import Execution, ExecutionStep, now
from app.services.actions import Actions, NodeError
from app.services.expressions import MappingError, ensure_size, resolve
from app.services.store import event, finish, session

RENEW = (
    "if redis.call('GET',KEYS[1]) == ARGV[1] then "
    "return redis.call('EXPIRE',KEYS[1],30) else return 0 end"
)
RELEASE = (
    "if redis.call('GET',KEYS[1]) == ARGV[1] then "
    "return redis.call('DEL',KEYS[1]) else return 0 end"
)


class CancelledExecution(Exception):
    pass


class Monitor:
    def __init__(self, identifier: str, redis, token: str):
        self.identifier = identifier
        self.redis = redis
        self.token = token
        self.started = time.monotonic()
        self.renewed = 0.0

    def check(self):
        with session() as db:
            execution = db.get(Execution, self.identifier)
            if (
                execution is None
                or execution.cancel_requested
                or execution.status == "cancelled"
            ):
                raise CancelledExecution()
            if execution.status != "running":
                raise NodeError("execution_interrupted", "Exécution interrompue")
            if time.monotonic() - self.started > get_settings().execution_max_seconds:
                raise NodeError(
                    "execution_timeout", "Durée maximale de l’exécution dépassée"
                )
            if time.monotonic() - self.renewed >= 5:
                if not self.redis.eval(
                    RENEW, 1, f"execution:{self.identifier}", self.token
                ):
                    raise NodeError("lock_lost", "Verrou d’exécution perdu")
                execution.heartbeat_at = now()
                db.commit()
                self.renewed = time.monotonic()

    async def bounded(self, operation, seconds: int):
        task = asyncio.create_task(operation)
        deadline = time.monotonic() + seconds
        try:
            while True:
                self.check()
                left = deadline - time.monotonic()
                if left <= 0:
                    raise NodeError("node_timeout", "Délai du nœud dépassé", True)
                done, _ = await asyncio.wait([task], timeout=min(0.2, left))
                if done:
                    return task.result()
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


def inputs_for(
    node: str, graph: dict, outputs: dict, enabled: set, payload: dict
) -> dict:
    incoming = [
        edge
        for edge in graph["edges"]
        if edge["target"] == node and edge["id"] in enabled
    ]
    if not incoming:
        return dict(payload)
    values = {edge["source"]: outputs[edge["source"]] for edge in incoming}
    if len(values) == 1:
        return dict(next(iter(values.values())))
    result = {"parents": values}
    keys = set().union(*(set(value) for value in values.values()))
    for key in keys:
        candidates = [value[key] for value in values.values() if key in value]
        if all(item == candidates[0] for item in candidates):
            result[key] = candidates[0]
    return result


async def report(actions: Actions, agent: dict, status: str, identifier: str):
    if agent.get("id"):
        try:
            await asyncio.wait_for(
                actions.request(
                    "agent",
                    "POST",
                    f"/internal/agents/{agent['id']}/run-result",
                    {"status": status, "execution_id": identifier},
                    internal=True,
                ),
                timeout=5,
            )
        except Exception:
            logger.warning("État d’agent non synchronisé ; consulter l’exécution")


async def execute_graph(identifier: str, actions: Actions, monitor: Monitor):
    with session() as db:
        execution = db.get(Execution, identifier)
        snapshot = execution.snapshot
        payload = execution.trigger_payload
        source = execution.source
    if source not in {"manual", "agent_test"}:
        current = await monitor.bounded(
            actions.request("workflow", "GET", f"/workflows/{snapshot['workflow_id']}"),
            10,
        )
        if not current["is_active"]:
            raise NodeError("workflow_inactive", "Workflow désactivé avant exécution")
    graph = snapshot["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    root_agent = snapshot["agents"][nodes[snapshot["order"][0]]["data"]["agent_id"]]
    source_connection = snapshot["trigger"].get("connection_id") or (
        root_agent.get("connection_id")
        if root_agent["type_key"] == "gmail_read"
        else None
    )
    outputs = {}
    enabled = set()
    for index, node_id in enumerate(snapshot["order"]):
        monitor.check()
        node = nodes[node_id]
        agent = snapshot["agents"][node["data"]["agent_id"]]
        incoming = [edge for edge in graph["edges"] if edge["target"] == node_id]
        if incoming and not any(edge["id"] in enabled for edge in incoming):
            with session() as db:
                step = db.scalar(
                    select(ExecutionStep).where(
                        ExecutionStep.execution_id == identifier,
                        ExecutionStep.node_id == node_id,
                    )
                )
                step.status = "skipped"
                step.finished_at = now()
                event(
                    db,
                    identifier,
                    "step",
                    {
                        "node_id": node_id,
                        "status": "skipped",
                        "reason": "branch_not_selected",
                    },
                )
                db.commit()
            continue
        data = {}
        try:
            data = inputs_for(node_id, graph, outputs, enabled, payload)
            context = {
                "input": data,
                "source_connection_id": source_connection,
                "trigger": payload,
                "nodes": {key: {"output": value} for key, value in outputs.items()},
            }
            overrides = resolve(node["data"].get("input", {}), context)
            data = {**data, **overrides}
            context["input"] = data
            ensure_size(data)
            if agent.get("id"):
                current = await monitor.bounded(
                    actions.request("agent", "GET", f"/agents/{agent['id']}"), 10
                )
                if source != "agent_test" and current["status"] != "active":
                    raise NodeError(
                        "agent_inactive", "Agent désactivé ou modifié avant exécution"
                    )
                if any(
                    current.get(key) != agent.get(key)
                    for key in ("type_key", "config", "connection_id")
                ):
                    raise NodeError(
                        "agent_changed",
                        "La configuration de l’agent a changé ; relancer le workflow",
                    )
            attempts = node["data"].get("retries", 0) + 1
            for attempt in range(1, attempts + 1):
                with session() as db:
                    step = db.scalar(
                        select(ExecutionStep).where(
                            ExecutionStep.execution_id == identifier,
                            ExecutionStep.node_id == node_id,
                        )
                    )
                    step.status = "running"
                    step.started_at = step.started_at or now()
                    step.input = data
                    step.attempt = attempt
                    event(
                        db,
                        identifier,
                        "step",
                        {"node_id": node_id, "status": "running", "attempt": attempt},
                    )
                    db.commit()
                try:
                    output = await monitor.bounded(
                        actions.run(
                            agent,
                            data,
                            context,
                            snapshot["trigger"]["type"],
                            is_root=index == 0,
                        ),
                        node["data"].get("timeout_seconds", 60),
                    )
                    break
                except NodeError as error:
                    if not error.retryable or attempt == attempts:
                        raise
                    with session() as db:
                        event(
                            db,
                            identifier,
                            "retry",
                            {
                                "node_id": node_id,
                                "attempt": attempt,
                                "error_code": error.code,
                            },
                        )
                        db.commit()
                    await monitor.bounded(asyncio.sleep(min(2 ** (attempt - 1), 5)), 10)
            ensure_size(output)
            outputs[node_id] = output
            with session() as db:
                step = db.scalar(
                    select(ExecutionStep).where(
                        ExecutionStep.execution_id == identifier,
                        ExecutionStep.node_id == node_id,
                    )
                )
                step.status = "success"
                step.output = output
                step.finished_at = now()
                step.error = None
                step.error_code = None
                event(
                    db,
                    identifier,
                    "step",
                    {"node_id": node_id, "status": "success", "attempt": attempt},
                )
                db.commit()
            await report(actions, agent, "success", identifier)
            for edge in graph["edges"]:
                if edge["source"] == node_id and (
                    agent["type_key"] != "condition"
                    or edge.get("sourceHandle") == output["branch"]
                ):
                    enabled.add(edge["id"])
        except CancelledExecution:
            raise
        except Exception as error:
            if isinstance(error, NodeError):
                code, message = error.code, error.message
            elif isinstance(error, MappingError):
                code, message = "invalid_mapping", str(error)
            else:
                code, message = (
                    "node_failed",
                    "Configuration ou sortie de nœud invalide",
                )
            with session() as db:
                step = db.scalar(
                    select(ExecutionStep).where(
                        ExecutionStep.execution_id == identifier,
                        ExecutionStep.node_id == node_id,
                    )
                )
                step.status = "failed"
                step.input = data
                step.started_at = step.started_at or now()
                step.error = message
                step.error_code = code
                step.finished_at = now()
                event(
                    db,
                    identifier,
                    "step",
                    {
                        "node_id": node_id,
                        "status": "failed",
                        "error": message,
                        "error_code": code,
                    },
                )
                db.commit()
            await report(actions, agent, "failed", identifier)
            raise NodeError(code, message) from None


async def run_execution(
    identifier: str, client: httpx.AsyncClient | None = None, redis=None
):
    settings = get_settings()
    owned_redis = redis is None
    redis = redis or Redis.from_url(
        settings.redis_url, socket_timeout=3, socket_connect_timeout=3
    )
    token = str(uuid4())
    key = f"execution:{identifier}"
    if not redis.set(key, token, nx=True, ex=30):
        if owned_redis:
            redis.close()
        return
    owned_client = client is None
    client = client or httpx.AsyncClient(
        timeout=300, follow_redirects=False, trust_env=False, headers={"Cookie": ""}
    )
    try:
        with session() as db:
            claimed = db.execute(
                update(Execution)
                .where(
                    Execution.id == identifier,
                    Execution.status == "queued",
                    Execution.cancel_requested.is_(False),
                )
                .values(status="running", started_at=now(), heartbeat_at=now())
            )
            if claimed.rowcount != 1:
                db.rollback()
                return
            execution = db.get(Execution, identifier)
            event(db, identifier, "execution", {"status": "running"})
            db.commit()
            user = execution.user_id
        try:
            await execute_graph(
                identifier, Actions(client, user), Monitor(identifier, redis, token)
            )
            with session() as db:
                execution = db.get(Execution, identifier)
                finish(
                    db,
                    execution,
                    "cancelled" if execution.cancel_requested else "success",
                )
        except CancelledExecution:
            with session() as db:
                finish(db, db.get(Execution, identifier), "cancelled", code="cancelled")
        except Exception as error:
            code = (
                error.code if isinstance(error, NodeError) else "execution_interrupted"
            )
            message = (
                error.message
                if isinstance(error, NodeError)
                else "Exécution interrompue ; consulter les étapes"
            )
            with session() as db:
                finish(db, db.get(Execution, identifier), "failed", message, code)
    finally:
        if owned_client:
            await client.aclose()
        with suppress(Exception):
            redis.eval(RELEASE, 1, key, token)
        if owned_redis:
            redis.close()
