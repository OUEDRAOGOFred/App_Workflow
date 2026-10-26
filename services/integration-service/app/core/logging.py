import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger("workflow")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
            }
        )


def configure(app: FastAPI):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # Never log body, query strings, tokens, passwords or headers.
        logger.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        content = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"detail": str(exc.detail), "code": f"http_{exc.status_code}"}
        )
        return JSONResponse(content, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors can contain input values: return only locations/messages.
        fields = [
            {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            {
                "detail": "Données invalides",
                "code": "validation_error",
                "errors": fields,
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("Unhandled error: %s", type(exc).__name__)
        return JSONResponse(
            {"detail": "Erreur interne", "code": "internal_error"}, status_code=500
        )
