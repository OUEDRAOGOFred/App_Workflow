import asyncio
import json
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.registry import REGISTRY
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.logging import logger
from app.models.entities import Connection, PollCursor
from app.services.connections import owned, perform

STREAM = "integration.events"


def poll_connection(db: Session, connection: Connection, http, redis) -> dict:
    lock = redis.lock(f"integration-poll:{connection.id}", timeout=600, blocking=False)
    if not lock.acquire(blocking=False):
        raise HTTPException(
            409, {"detail": "Une réception est déjà en cours", "code": "poll_busy"}
        )
    try:

        def operation(connector):
            cursor = db.get(PollCursor, connection.id)
            if cursor is None:
                cursor = PollCursor(connection_id=connection.id, state={})
                db.add(cursor)
            events, state = connector.poll(cursor.state)
            # Publish before committing the cursor. A crash may repeat an event;
            # consumers must deduplicate (connection_id, event_id) in phase 4.
            for event in events:
                redis.xadd(
                    STREAM,
                    {
                        "user_id": connection.user_id,
                        "connection_id": connection.id,
                        "event_id": event["event_id"],
                        "type": event["type"],
                        "payload": json.dumps(event["payload"]),
                    },
                    maxlen=10000,
                    approximate=True,
                )
            cursor.state = state
            return {
                "events": events,
                "published": len(events),
                "mock": connection.is_mock,
            }

        return perform(db, connection, http, operation)
    finally:
        lock.release()


def poll_one(identifier: str, user: str, app) -> None:
    with Session(get_engine(), expire_on_commit=False) as db:
        connection = owned(db, identifier, user, lock=True)
        poll_connection(db, connection, app.state.http, app.state.redis)


async def worker(app) -> None:
    settings = get_settings()
    due: dict[str, float] = {}
    semaphore = asyncio.Semaphore(4)

    async def receive(identifier, user):
        async with semaphore:
            try:
                await asyncio.to_thread(poll_one, identifier, user, app)
            except Exception:
                logger.warning(
                    "Polling interrompu pour une connexion ; "
                    "consulter son état et la tester"
                )

    while True:
        try:
            with Session(get_engine()) as db:
                connections = list(
                    db.scalars(
                        select(Connection)
                        .where(
                            Connection.status == "active",
                            Connection.is_mock.is_(False),
                            Connection.type.in_(
                                [key for key, cls in REGISTRY.items() if cls.triggers]
                            ),
                        )
                        .limit(100)
                    )
                )
                tasks = []
                for connection in connections:
                    if due.get(connection.id, 0) > time.monotonic():
                        continue
                    interval = connection.poll_config.get("interval_seconds") or (
                        settings.telegram_poll_seconds
                        if connection.type == "telegram"
                        else settings.gmail_poll_seconds
                    )
                    due[connection.id] = time.monotonic() + interval
                    tasks.append(receive(connection.id, connection.user_id))
            if tasks:
                await asyncio.gather(*tasks)
        except Exception:
            logger.warning("Réception temporairement indisponible ; nouvelle tentative")
        await asyncio.sleep(1)
