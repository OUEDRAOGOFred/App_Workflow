from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from redis import Redis
from sqlalchemy import text

from app.api.routes import router
from app.core import database
from app.core.config import get_settings
from app.core.logging import configure
from app.services.dispatch import Services


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    with (
        httpx.Client(
            timeout=15, follow_redirects=False, trust_env=False, headers={"Cookie": ""}
        ) as client,
        Redis.from_url(
            settings.redis_url, socket_timeout=3, socket_connect_timeout=3
        ) as redis,
    ):
        app.state.services = Services(client)
        app.state.redis = redis
        yield
    database.get_engine().dispose()


app = FastAPI(title="Workflow Execution Service", version="0.4.0", lifespan=lifespan)
configure(app)
app.include_router(router)


@app.get("/health", tags=["Infrastructure"])
def health(request: Request):
    try:
        with database.get_engine().connect() as db:
            db.execute(text("SELECT 1"))
        request.app.state.redis.ping()
    except Exception:
        raise HTTPException(
            503, {"detail": "Dépendance indisponible", "code": "not_ready"}
        ) from None
    return {"status": "ok", "service": "execution-service"}
