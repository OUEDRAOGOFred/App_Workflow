import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException, Request
from redis import Redis
from sqlalchemy import text

from app.api.routes import router
from app.connectors.registry import load_connectors
from app.core.config import get_settings
from app.core.crypto import cipher
from app.core.database import get_engine
from app.core.logging import configure, logger
from app.services.polling import worker


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    cipher()
    load_connectors()
    if settings.effective_mock:
        logger.warning(
            "MOCK_INTEGRATIONS=true : mode démonstration, aucun "
            "envoi externe. Renseigner Google OAuth et désactiver "
            "MOCK_INTEGRATIONS pour les connexions réelles"
        )
    with (
        httpx.Client(
            follow_redirects=False, trust_env=False, headers={"Cookie": ""}
        ) as client,
        Redis.from_url(
            settings.redis_url, socket_connect_timeout=3, socket_timeout=5
        ) as redis,
    ):
        app.state.http = client
        app.state.redis = redis
        task = (
            asyncio.create_task(worker(app))
            if settings.polling_enabled and not settings.effective_mock
            else None
        )
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    get_engine().dispose()


app = FastAPI(title="Workflow Integration Service", version="0.3.0", lifespan=lifespan)
configure(app)
app.include_router(router)


@app.get("/health", tags=["Infrastructure"])
def health(request: Request):
    try:
        with get_engine().connect() as db:
            db.execute(text("SELECT 1"))
        request.app.state.redis.ping()
        cipher()
    except Exception:
        raise HTTPException(
            503, {"detail": "Dépendance indisponible", "code": "not_ready"}
        ) from None
    return {
        "status": "ok",
        "service": "integration-service",
        "mock_integrations": get_settings().effective_mock,
    }
