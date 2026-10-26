import json
import logging
import secrets
import socket
import time
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from croniter import croniter
from fastapi import HTTPException
from redis import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import JsonFormatter, logger
from app.models.entities import Execution, now
from app.services.auth import access_token
from app.services.dispatch import Services, create_execution, dispatch_pending
from app.services.store import finish, session

STREAM = "integration.events"
GROUP = "execution-schedulers"
GLOBAL_USER = str(UUID(int=0))


def active_workflows(client) -> list[dict]:
    settings = get_settings()
    result = []
    offset = 0
    while True:
        response = client.get(
            settings.workflow_service_url + "/internal/workflows/active",
            params={"limit": 200, "offset": offset},
            headers={
                "Authorization": "Bearer "
                + access_token(GLOBAL_USER, service=True, scope="scheduler"),
                "Cookie": "",
            },
            timeout=15,
        )
        response.raise_for_status()
        page = response.json()
        result.extend(page)
        if len(page) < 200:
            return result
        offset += 200


def enqueue(
    workflow: dict, services: Services, payload: dict, source: str, event_id: str
) -> str:
    failure = None
    try:
        snapshot = services.snapshot(workflow["id"], workflow["user_id"], source)
    except HTTPException as error:
        if error.status_code >= 500:
            raise
        snapshot = {
            "workflow_id": workflow["id"],
            "workflow_version": workflow.get("version"),
            "name": "Préparation échouée",
            "trigger": workflow["trigger"],
            "graph": {"nodes": [], "edges": []},
            "order": [],
            "agents": {},
        }
        failure = "Préparation impossible ; vérifier les agents et connexions"
    with session() as db:
        return create_execution(
            db,
            workflow["user_id"],
            snapshot,
            payload,
            source,
            event_id,
            failure=failure,
        ).id


def consume(event: dict, workflows: list[dict], services: Services) -> list[str]:
    result = []
    transient = False
    for workflow in workflows:
        kind = {"telegram.message": "telegram", "gmail.new_mail": "gmail"}.get(
            event["type"]
        )
        trigger = workflow["trigger"]
        if (
            workflow["user_id"] != event["user_id"]
            or trigger["type"] != kind
            or trigger.get("connection_id") != event["connection_id"]
        ):
            continue
        try:
            result.append(
                enqueue(
                    workflow,
                    services,
                    json.loads(event["payload"]),
                    kind,
                    event["connection_id"] + ":" + event["event_id"],
                )
            )
        except Exception:
            logger.warning("Un workflow attend le rétablissement d’un service")
            # Successful matches are already idempotent; replay can safely retry.
            transient = True
    if transient:
        raise RuntimeError("Trigger dispatch incomplete")
    return result


def schedule_cron(
    workflows: list[dict], services: Services, moment: datetime
) -> list[str]:
    result = []
    for workflow in workflows:
        trigger = workflow["trigger"]
        if trigger["type"] != "cron" or not croniter.match(trigger["schedule"], moment):
            continue
        event_id = moment.replace(second=0, microsecond=0).isoformat()
        try:
            result.append(
                enqueue(
                    workflow, services, {"scheduled_at": event_id}, "cron", event_id
                )
            )
        except Exception:
            logger.warning("Une planification attend le rétablissement d’un service")
    return result


def recover_stale():
    with session() as db:
        stale = list(
            db.scalars(
                select(Execution).where(
                    Execution.status == "running",
                    Execution.heartbeat_at
                    < now() - timedelta(seconds=get_settings().stale_execution_seconds),
                )
            )
        )
        for job in stale:
            finish(
                db,
                job,
                "failed",
                "Worker interrompu ; relance explicite requise",
                "worker_interrupted",
            )


def run():
    settings = get_settings()
    consumer = socket.gethostname() + "-" + secrets.token_hex(3)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    with (
        Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=3,
        ) as redis,
        httpx.Client(timeout=15, trust_env=False, headers={"Cookie": ""}) as client,
    ):
        services = Services(client)
        last_scan = 0
        workflows = []
        while True:
            try:
                redis.set("execution-scheduler:heartbeat", str(time.time()), ex=30)
                try:
                    redis.xgroup_create(STREAM, GROUP, id="0-0", mkstream=True)
                except ResponseError as error:
                    if "BUSYGROUP" not in str(error):
                        raise
                if time.monotonic() - last_scan >= 5:
                    dispatch_pending()
                    recover_stale()
                    workflows = (
                        active_workflows(client) if settings.scheduler_enabled else []
                    )
                    if settings.scheduler_enabled:
                        schedule_cron(
                            workflows,
                            services,
                            datetime.now(ZoneInfo(settings.scheduler_timezone)),
                        )
                    last_scan = time.monotonic()
                claimed = redis.xautoclaim(
                    STREAM,
                    GROUP,
                    consumer,
                    min_idle_time=60000,
                    start_id="0-0",
                    count=10,
                )[1]
                batches = redis.xreadgroup(
                    GROUP, consumer, {STREAM: ">"}, count=10, block=1000
                )
                entries = [*claimed, *[item for _, rows in batches for item in rows]]
                if entries:
                    workflows = (
                        active_workflows(client) if settings.scheduler_enabled else []
                    )
                for identifier, data in entries:
                    try:
                        consume(data, workflows, services)
                        redis.xack(STREAM, GROUP, identifier)
                    except Exception:
                        logger.warning("Événement conservé pour nouvelle tentative")
            except Exception:
                logger.warning(
                    "Ordonnanceur temporairement indisponible ; nouvelle tentative"
                )
                time.sleep(1)


if __name__ == "__main__":
    run()
