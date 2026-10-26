"""Real HTTP smoke test of the Compose stack; uses a fresh disposable account."""

import json
import os
import secrets
import urllib.error
import urllib.request

BASE = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")


def request(path, body=None, token=None, expected=200, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            status = response.status
            output = json.load(response) if status != 204 else None
    except urllib.error.HTTPError as error:
        status, output = error.code, json.load(error)
    assert status == expected, f"{path}: expected {expected}, got {status}"
    return output


def main():
    health = request("/health")
    assert health == {
        "status": "ok",
        "service": "api-gateway",
    }, "API_URL ne pointe pas vers la passerelle du projet"
    credentials = {
        "email": f"smoke-{secrets.token_hex(6)}@example.com",
        "password": secrets.token_urlsafe(24),
    }
    profile = request("/api/v1/auth/register", credentials, expected=201)
    pair = request("/api/v1/auth/login", credentials)
    assert request("/api/v1/auth/me", token=pair["access_token"])["id"] == profile["id"]
    rotated = request("/api/v1/auth/refresh", {"refresh_token": pair["refresh_token"]})
    request(
        "/api/v1/auth/refresh", {"refresh_token": pair["refresh_token"]}, expected=401
    )
    assert (
        request("/api/v1/auth/me", token=rotated["access_token"])["email"]
        == credentials["email"]
    )
    assert "/api/v1/auth/login" in request("/openapi.json")["paths"]
    print("Smoke OK: health, register, login, profile, refresh, replay, OpenAPI")


if __name__ == "__main__":
    main()
