import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

os.environ["JWT_SECRET"] = "phase-three-test-key-never-use-in-production-123456789"

from app.core.config import get_settings

os.environ["CLASSIFIER_BACKEND"] = "demo"
from app.main import app
from app.services.adapter import DemoAdapter
from app.services.classifier import Classifier
from app.services.documents import extract


def token(user=None, kind="access"):
    settings = get_settings()
    return jwt.encode(
        {
            "sub": user or str(uuid4()),
            "type": kind,
            "jti": str(uuid4()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


@pytest.fixture
def client():
    with TestClient(app) as http:
        http.headers["Authorization"] = f"Bearer {token()}"
        yield http


def classifier(handler, backend="ollama"):
    settings = get_settings().model_copy(
        update={
            "classifier_backend": backend,
            "ollama_url": "http://ollama.test",
            "ollama_retries": 2,
        }
    )

    def transport(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "phi3:latest"}]})
        return handler(request)

    return Classifier(settings, httpx.Client(transport=httpx.MockTransport(transport)))


def test_text_classes_auth_and_demo_llm(client):
    assert client.get("/health").json()["backend"] == "demo"
    assert client.get("/classes").json()["classes"] == ["facture", "contrat", "cv"]
    for label, text in (
        ("facture", "Facture total TVA montant 125 euros"),
        ("contrat", "Contrat accord parties obligations signature"),
        ("cv", "CV curriculum vitae formation expérience professionnelle"),
    ):
        response = client.post("/classify", json={"text": text})
        assert response.status_code == 200
        assert response.json()["label"] == label
        assert 0 <= response.json()["confidence"] <= 1
    assert (
        client.post("/classify", json={"text": "zzzz inconnu"}).json()["label"]
        == "inconnu"
    )
    assert (
        client.post("/llm", json={"prompt": "Première phrase. Seconde phrase."}).json()[
            "mock"
        ]
        is True
    )
    assert client.post("/classify", json={"text": ""}).status_code == 422
    del client.headers["Authorization"]
    assert client.get("/classes").status_code == 401
    assert (
        client.post(
            "/classify",
            json={"text": "facture"},
            headers={"Authorization": f"Bearer {token(kind='refresh')}"},
        ).status_code
        == 401
    )


def test_valid_ollama_and_strict_prompt():
    def generate(request):
        import json

        payload = json.loads(request.content)
        assert payload["stream"] is False and payload["format"] == "json"
        assert "Document JSON" in payload["prompt"] and "facture" in payload["prompt"]
        return httpx.Response(
            200, json={"response": '{"label":"facture","confidence":0.93}'}
        )

    service = classifier(generate)
    assert service.model_available
    result = service.classify("Facture 42", 0.95)
    assert result.label == "inconnu" and result.reason == "below_threshold"
    assert service.classify("Facture 42").label == "facture"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"label":"other","confidence":0.9}',
        '{"label":"facture","confidence":2}',
        '{"label":"facture","confidence":true}',
        '{"label":"facture","confidence":0.9,"extra":"ignored?"}',
        '{"label":"facture","confidence":NaN}',
    ],
)
def test_invalid_output_retries_then_unknown(raw):
    calls = []

    def generate(request):
        calls.append(1)
        return httpx.Response(200, json={"response": raw})

    result = classifier(generate).classify("untrusted document")
    assert (
        result.label == "inconnu"
        and result.confidence == 0
        and result.reason == "invalid_model_output"
    )
    assert len(calls) == 3


def test_timeout_retry_and_auto_fallback():
    calls = []

    def timeout(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    result = classifier(timeout).classify("facture")
    assert result.reason == "ollama_unavailable" and len(calls) == 3
    settings = get_settings().model_copy(update={"classifier_backend": "auto"})
    service = Classifier(settings, httpx.Client(transport=httpx.MockTransport(timeout)))
    assert service.backend == "demo"


def test_retry_recovers_and_llm_uses_ollama():
    responses = iter(["bad", '{"label":"contrat","confidence":0.8}'])
    service = classifier(
        lambda request: httpx.Response(200, json={"response": next(responses)})
    )
    assert service.classify("contrat").label == "contrat"
    service = classifier(
        lambda request: httpx.Response(200, json={"response": "Résumé réel du modèle"})
    )
    assert service.llm("Résume", 0.2) == {
        "response": "Résumé réel du modèle",
        "backend": "ollama",
        "mock": False,
    }


def test_custom_classes_and_corpus(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(
        '[["lettre","courrier destinataire salutations '
        'lettre"],["rapport","analyse résultats rapport '
        'scientifique"]]'
    )
    adapter = DemoAdapter(["lettre", "rapport"], str(path))
    assert adapter.predict("Rapport analyse résultats")[0] == "rapport"
    assert adapter.predict("salutations lettre destinataire")[0] == "lettre"


def test_file_json_and_multipart_validation(client):
    for path in ("/classify", "/classify/file"):
        response = client.post(
            path,
            files={"file": ("facture.txt", b"Facture total TVA euros", "text/plain")},
        )
        assert response.status_code == 200 and response.json()["label"] == "facture"
    assert client.post("/classify", files={"other": ("a.txt", b"x")}).status_code == 422
    assert (
        client.post("/classify", files={"file": ("a.bin", b"binary")}).status_code
        == 415
    )
    assert (
        client.post("/classify", files={"file": ("a.pdf", b"%PDF-broken")}).status_code
        == 422
    )
    assert (
        client.post("/classify", files={"file": ("image.png", b"\x89PNGfake")}).json()[
            "code"
        ]
        == "ocr_disabled"
    )
    assert (
        client.post(
            "/classify",
            content="not JSON",
            headers={"Content-Type": "application/json"},
        ).status_code
        == 422
    )


def test_pdf_extraction_and_file_limits(client, monkeypatch):
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 100 700 Td (Contrat accord signature) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    assert (
        client.post(
            "/classify", files={"file": ("contrat.pdf", output.getvalue())}
        ).json()["label"]
        == "contrat"
    )
    monkeypatch.setattr(get_settings(), "max_file_bytes", 1024)
    assert (
        client.post(
            "/classify/file", files={"file": ("a.txt", b"x" * 1025)}
        ).status_code
        == 413
    )
    with pytest.raises(Exception) as error:
        extract(b"", "a.txt", get_settings())
    assert error.value.status_code == 422


def test_ocr_valid_image_uses_bounded_process_and_removes_temporary_file(
    client, monkeypatch
):
    import io
    import subprocess
    from pathlib import Path
    from unittest.mock import patch

    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    monkeypatch.setattr(get_settings(), "ocr_enabled", True)
    paths = []

    def recognize(command, **kwargs):
        paths.append(Path(command[1]))
        assert paths[-1].is_file() and kwargs["timeout"] == 30
        return subprocess.CompletedProcess(
            command, 0, stdout=b"Contrat accord signature", stderr=b""
        )

    with patch("app.services.documents.subprocess.run", side_effect=recognize):
        response = client.post(
            "/classify/file",
            files={"file": ("scan.png", image.getvalue(), "image/png")},
        )
    assert response.status_code == 200 and response.json()["label"] == "contrat"
    assert paths and not paths[0].exists()
