from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    app.state.redis = Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    async with AsyncExitStack() as stack:
        app.state.http = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url=settings.auth_service_url, timeout=10, follow_redirects=False
            )
        )
        app.state.business = {}
        for key, url in (
            ("agent", settings.agent_service_url),
            ("workflow", settings.workflow_service_url),
            ("integration", settings.integration_service_url),
            ("classifier", settings.classifier_service_url),
            ("execution", settings.execution_service_url),
        ):
            app.state.business[key] = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=url,
                    timeout=(
                        360
                        if key == "integration"
                        else 200 if key == "classifier" else 15
                    ),
                    follow_redirects=False,
                )
            )
        yield
    await app.state.redis.aclose()


app = FastAPI(
    title="Workflow API Gateway",
    version="0.4.0",
    lifespan=lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)
configure(app)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Last-Event-ID",
        "Idempotency-Key",
        "X-Webhook-Secret",
    ],
    expose_headers=["X-Request-ID"],
)
app.include_router(router)


@app.get("/docs", include_in_schema=False)
def docs():
    from fastapi.openapi.docs import get_swagger_ui_html

    return get_swagger_ui_html(openapi_url="/openapi.json", title="Workflow API")


@app.get("/health", tags=["Infrastructure"])
async def health(request: Request):
    try:
        await request.app.state.redis.ping()
        for client in [request.app.state.http, *request.app.state.business.values()]:
            response = await client.get("/health")
            response.raise_for_status()
    except Exception:
        raise HTTPException(
            503, {"detail": "Dépendance indisponible", "code": "not_ready"}
        ) from None
    return {"status": "ok", "service": "api-gateway"}
