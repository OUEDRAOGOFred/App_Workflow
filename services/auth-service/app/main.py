from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from redis import Redis
from sqlalchemy import text

from app.api.routes import router
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.logging import configure


@asynccontextmanager
async def lifespan(app):
    get_settings()
    yield
    get_engine().dispose()


app = FastAPI(title="Workflow Auth Service", version="0.1.0", lifespan=lifespan)
configure(app)
app.include_router(router)


@app.get("/health", tags=["Infrastructure"])
def health():
    try:
        with get_engine().connect() as db:
            db.execute(text("SELECT 1"))
        with Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2
        ) as redis:
            redis.ping()
    except Exception:
        raise HTTPException(
            503, {"detail": "Dépendance indisponible", "code": "not_ready"}
        ) from None
    return {"status": "ok", "service": "auth-service"}
