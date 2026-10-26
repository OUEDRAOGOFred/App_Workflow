import io
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader

from app.core.config import Settings


def extract(data: bytes, filename: str, settings: Settings) -> str:
    if len(data) > settings.max_file_bytes:
        raise HTTPException(
            413, {"detail": "Document trop volumineux", "code": "file_too_large"}
        )
    suffix = Path(filename).suffix.lower()
    try:
        if data.startswith(b"%PDF-"):
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 100:
                raise ValueError("Unsupported PDF")
            pieces = []
            for page in reader.pages:
                pieces.append(page.extract_text() or "")
                if sum(map(len, pieces)) > 200000:
                    break
            text = "\n".join(pieces)
        elif data.startswith((b"\x89PNG", b"\xff\xd8")):
            if not settings.ocr_enabled:
                raise HTTPException(
                    422,
                    {
                        "detail": (
                            "Activer OCR_ENABLED et construire avec "
                            "INSTALL_OCR=true pour les images"
                        ),
                        "code": "ocr_disabled",
                    },
                )
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 20000000:
                    raise ValueError("Image too large")
                image.verify()
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "document"
                path.write_bytes(data)
                result = subprocess.run(
                    ["tesseract", str(path), "stdout", "-l", "fra+eng"],
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
                text = result.stdout.decode("utf-8")
        elif suffix in {".txt", ".csv", ".md", ""}:
            text = data.decode("utf-8-sig")
        else:
            raise HTTPException(
                415,
                {
                    "detail": "Format pris en charge : texte UTF-8, PDF, PNG ou JPEG",
                    "code": "unsupported_document",
                },
            )
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(
            422,
            {
                "detail": "Tesseract est absent de cette image Docker",
                "code": "ocr_unavailable",
            },
        ) from None
    except Exception:
        raise HTTPException(
            422,
            {
                "detail": "Document illisible ou extraction impossible",
                "code": "invalid_document",
            },
        ) from None
    if not text.strip():
        raise HTTPException(
            422,
            {
                "detail": (
                    "Aucun texte extrait ; un PDF scanné nécessite un " "OCR préalable"
                ),
                "code": "empty_document",
            },
        )
    if len(text) > 200000:
        raise HTTPException(
            413, {"detail": "Texte extrait trop long", "code": "text_too_large"}
        )
    return text
