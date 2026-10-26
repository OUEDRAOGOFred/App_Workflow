import asyncio
import copy
import re
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.services.proxy import rate_limit, verify_access

router = APIRouter()
ALLOWED = {"register": "POST", "login": "POST", "refresh": "POST", "me": "GET"}


@router.api_route(
    "/api/v1/auth/{action}", methods=["GET", "POST"], include_in_schema=False
)
async def proxy_auth(action: str, request: Request):
    if action not in ALLOWED:
        raise HTTPException(404, {"detail": "Route inconnue", "code": "not_found"})
    if request.method != ALLOWED[action]:
        raise HTTPException(
            405,
            {"detail": "Méthode non autorisée", "code": "method_not_allowed"},
            headers={"Allow": ALLOWED[action]},
        )
    await rate_limit(request, sensitive=action != "me")
    if action == "me":
        verify_access(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise HTTPException(
                413, {"detail": "Requête trop volumineuse", "code": "body_too_large"}
            )
    headers = {"Content-Type": request.headers.get("content-type", "application/json")}
    if action == "me":
        headers["Authorization"] = request.headers["authorization"]
    try:
        response = await request.app.state.http.request(
            request.method, f"/auth/{action}", content=bytes(body), headers=headers
        )
    except httpx.RequestError:
        raise HTTPException(
            503,
            {
                "detail": "Service d'authentification indisponible",
                "code": "upstream_unavailable",
            },
        ) from None
    if response.status_code >= 500:
        raise HTTPException(
            502,
            {
                "detail": "Erreur du service d'authentification",
                "code": "upstream_error",
            },
        )
    forwarded_headers = {"Cache-Control": "no-store"}
    if "www-authenticate" in response.headers:
        forwarded_headers["WWW-Authenticate"] = response.headers["www-authenticate"]
    return Response(
        response.content,
        status_code=response.status_code,
        media_type="application/json",
        headers=forwarded_headers,
    )


@router.get("/api/v1/executions/{identifier}/stream", include_in_schema=False)
async def execution_stream(identifier: UUID, request: Request):
    await rate_limit(request)
    verify_access(request)
    client = request.app.state.business["execution"]
    upstream = client.build_request(
        "GET",
        f"/executions/{identifier}/stream",
        params=request.query_params.multi_items(),
        headers={
            "Authorization": request.headers["authorization"],
            "Cookie": "",
            "Last-Event-ID": request.headers.get("last-event-id", ""),
        },
        timeout=httpx.Timeout(10, read=None),
    )
    try:
        response = await client.send(upstream, stream=True)
    except httpx.RequestError:
        raise HTTPException(
            503,
            {
                "detail": "Service d’exécution indisponible",
                "code": "upstream_unavailable",
            },
        ) from None
    if response.status_code != 200:
        data = await response.aread()
        await response.aclose()
        return Response(
            data, status_code=response.status_code, media_type="application/json"
        )
    return StreamingResponse(
        response.aiter_raw(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        background=BackgroundTask(response.aclose),
    )


BUSINESS_RESOURCES = {
    "agents": "agent",
    "agent-types": "agent",
    "agent-drafts": "agent",
    "workflows": "workflow",
    "connections": "integration",
    "integrations": "integration",
    "classify": "classifier",
    "classes": "classifier",
    "llm": "classifier",
    "executions": "execution",
    "webhooks": "execution",
}


@router.api_route(
    "/api/v1/{resource}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
@router.api_route(
    "/api/v1/{resource}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def proxy_business(resource: str, request: Request, path: str = ""):
    service = BUSINESS_RESOURCES.get(resource)
    if resource in {"workflows", "agents", "agent-drafts"} and re.fullmatch(
        r"[0-9a-fA-F-]{36}/(?:run|test|webhook-key)", path
    ):
        service = "execution"
    if service is None:
        raise HTTPException(404, {"detail": "Route inconnue", "code": "not_found"})
    await rate_limit(request)
    public_google = (
        resource == "integrations"
        and path in {"google/oauth/callback", "google/checklist"}
        and request.method == "GET"
    )
    public_webhook = (
        resource == "webhooks"
        and request.method == "POST"
        and re.fullmatch(r"[0-9a-fA-F-]{36}", path)
    )
    if not public_google and not public_webhook:
        verify_access(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        maximum = 10485760 if service in {"integration", "classifier"} else 1048576
        if len(body) > maximum:
            raise HTTPException(
                413, {"detail": "Requête trop volumineuse", "code": "body_too_large"}
            )
    route = f"/{resource}" + (f"/{path}" if path else "")
    headers = {
        "Authorization": request.headers.get("authorization", ""),
        "Content-Type": request.headers.get("content-type", "application/json"),
        "Cookie": "",
    }
    oauth_route = resource == "integrations" and path in {
        "google/oauth/start",
        "google/oauth/callback",
    }
    if oauth_route and path.endswith("callback"):
        binding = request.cookies.get("workflow_oauth_binding", "")
        if (
            binding
            and len(binding) <= 128
            and all(
                char.isascii() and (char.isalnum() or char in "-_") for char in binding
            )
        ):
            headers["Cookie"] = f"workflow_oauth_binding={binding}"
    if "idempotency-key" in request.headers:
        headers["Idempotency-Key"] = request.headers["idempotency-key"]
    if resource == "webhooks" and "x-webhook-secret" in request.headers:
        headers["X-Webhook-Secret"] = request.headers["x-webhook-secret"]
    try:
        response = await request.app.state.business[service].request(
            request.method,
            route,
            content=bytes(body),
            params=request.query_params.multi_items(),
            headers=headers,
        )
    except httpx.RequestError:
        raise HTTPException(
            503,
            {"detail": "Service métier indisponible", "code": "upstream_unavailable"},
        ) from None
    # Preserve structured business errors, including dependency failures.
    if response.status_code >= 500:
        try:
            error = response.json()
        except ValueError:
            error = {}
        if not isinstance(error, dict) or not {"detail", "code"}.issubset(error):
            raise HTTPException(
                502, {"detail": "Erreur du service métier", "code": "upstream_error"}
            )
    outgoing_headers = {"Cache-Control": "no-store"}
    if oauth_route and "set-cookie" in response.headers:
        outgoing_headers["Set-Cookie"] = response.headers["set-cookie"]
    return Response(
        response.content,
        status_code=response.status_code,
        media_type="application/json" if response.status_code != 204 else None,
        headers=outgoing_headers,
    )


def namespace_components(schema: dict, prefix: str) -> dict:
    schema = copy.deepcopy(schema)
    components = schema.get("components", {})
    # Prefix every component category, then rewrite refs and security requirements.
    replacements = {
        f"#/components/{category}/{name}": f"#/components/{category}/{prefix}_{name}"
        for category, values in components.items()
        for name in values
    }
    schemes = set(components.get("securitySchemes", {}))

    def rewrite(value):
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key == "$ref" and isinstance(item, str):
                    value[key] = replacements.get(item, item)
                elif key == "security" and isinstance(item, list):
                    value[key] = [
                        {
                            (f"{prefix}_{name}" if name in schemes else name): scopes
                            for name, scopes in requirement.items()
                        }
                        for requirement in item
                    ]
                else:
                    rewrite(item)
        elif isinstance(value, list):
            for item in value:
                rewrite(item)

    rewrite(schema)
    schema["components"] = {
        category: {f"{prefix}_{name}": value for name, value in values.items()}
        for category, values in components.items()
    }
    return schema


@router.get("/openapi.json", include_in_schema=False)
async def aggregated_openapi(request: Request):
    clients = {"auth": request.app.state.http, **request.app.state.business}
    try:
        responses = await asyncio.gather(
            *(client.get("/openapi.json") for client in clients.values())
        )
        documents = []
        for key, response in zip(clients, responses, strict=True):
            response.raise_for_status()
            documents.append((key, namespace_components(response.json(), key)))
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            503,
            {"detail": "Documentation indisponible", "code": "upstream_unavailable"},
        ) from None
    result = {
        "openapi": "3.1.0",
        "info": {
            "title": "Workflow API Gateway",
            "version": "0.4.0",
            "description": "Agents, workflows, intégrations, IA et moteur d’exécution.",
        },
        "paths": {},
        "components": {},
        "servers": [{"url": "/"}],
    }
    for key, document in documents:
        for path, operations in document.get("paths", {}).items():
            if path == "/health":
                continue
            for operation in operations.values():
                if isinstance(operation, dict) and "operationId" in operation:
                    operation["operationId"] = f"{key}_{operation['operationId']}"
            result["paths"][f"/api/v1{path}"] = operations
        for category, values in document["components"].items():
            result["components"].setdefault(category, {}).update(values)
    return JSONResponse(result)
