import json
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet

from app.core.config import get_settings


@lru_cache
def cipher() -> Fernet:
    settings = get_settings()
    key = settings.fernet_key or Path(settings.fernet_key_file).read_text().strip()
    return Fernet(key.encode())


def encrypt(credentials: dict) -> str:
    return cipher().encrypt(json.dumps(credentials).encode()).decode()


def decrypt(value: str) -> dict:
    return json.loads(cipher().decrypt(value.encode()))
