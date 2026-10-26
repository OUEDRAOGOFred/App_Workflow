from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.security import current_user
from app.schemas.classification import (
    ClassificationInput,
    ClassificationOutput,
    LLMInput,
)
from app.services.documents import extract

router = APIRouter(tags=["Classification et IA"])


@router.get("/classes")
def classes(request: Request, user: str = Depends(current_user)):
    return {
        "classes": get_settings().labels,
        "backend": request.app.state.classifier.backend,
    }


async def classify_upload(file: UploadFile, request: Request) -> ClassificationOutput:
    settings = get_settings()
    data = await file.read(settings.max_file_bytes + 1)
    text = extract(data, file.filename or "document.txt", settings)
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(request.app.state.classifier.classify, text)


@router.post("/classify/file", response_model=ClassificationOutput)
async def file_classification(
    request: Request, file: UploadFile = File(...), user: str = Depends(current_user)
):
    return await classify_upload(file, request)


@router.post(
    "/classify",
    response_model=ClassificationOutput,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": ClassificationInput.model_json_schema()},
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                        "required": ["file"],
                    }
                },
            },
        }
    },
)
async def classify(request: Request, user: str = Depends(current_user)):
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        async with request.form(
            max_files=1, max_fields=2, max_part_size=get_settings().max_file_bytes
        ) as form:
            file = form.get("file")
            from starlette.datastructures import UploadFile as StarletteUploadFile

            if not isinstance(file, StarletteUploadFile):
                raise HTTPException(
                    422, {"detail": "Champ file requis", "code": "validation_error"}
                )
            return await classify_upload(file, request)
    try:
        body = ClassificationInput.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(
            422,
            {
                "detail": "Texte et seuil de classification invalides",
                "code": "validation_error",
            },
        ) from None
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(
        request.app.state.classifier.classify, body.text, body.confidence_threshold
    )


@router.post("/llm")
def llm(body: LLMInput, request: Request, user: str = Depends(current_user)):
    try:
        return request.app.state.classifier.llm(body.prompt, body.temperature)
    except Exception:
        raise HTTPException(
            503,
            {"detail": "Le modèle Ollama ne répond pas", "code": "ollama_unavailable"},
        ) from None
