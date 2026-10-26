from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure
from app.services.classifier import Classifier


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    with httpx.Client(follow_redirects=False) as client:
        app.state.classifier = Classifier(settings, client)
        yield


app = FastAPI(title="Workflow ML Classifier", version="0.3.0", lifespan=lifespan)
configure(app)
app.include_router(router)


@app.get("/health", tags=["Infrastructure"])
def health(request: Request):
    classifier = request.app.state.classifier
    return {
        "status": "ok",
        "service": "ml-classifier-service",
        "backend": classifier.backend,
        "model_available": classifier.model_available,
    }
