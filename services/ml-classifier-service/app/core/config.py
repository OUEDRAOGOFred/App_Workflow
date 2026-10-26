from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    jwt_secret: str = Field(default="", repr=False)
    jwt_secret_file: str = "/run/secrets/jwt"
    jwt_issuer: str = "workflow-auth"
    jwt_audience: str = "workflow-api"
    redis_url: str = "redis://localhost:6379/0"

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "phi3"
    classifier_backend: str = Field(default="auto", pattern="^(auto|ollama|demo)$")
    classes: str = "facture,contrat,cv"
    model_path: str = ""
    ollama_timeout: float = Field(default=30, gt=0, le=120)
    ollama_retries: int = Field(default=2, ge=0, le=3)
    ocr_enabled: bool = False
    max_file_bytes: int = Field(default=5242880, ge=1024, le=10485760)

    @property
    def labels(self) -> list[str]:
        return list(
            dict.fromkeys(
                item.strip() for item in self.classes.split(",") if item.strip()
            )
        )

    @model_validator(mode="after")
    def load_secret(self):
        if not self.jwt_secret:
            path = Path(self.jwt_secret_file)
            if not path.is_file():
                raise ValueError("JWT_SECRET ou JWT_SECRET_FILE requis")
            self.jwt_secret = path.read_text().strip()
        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET doit contenir au moins 32 caractères")
        if (
            not self.labels
            or len(self.labels) > 50
            or any(len(label) > 80 for label in self.labels)
        ):
            raise ValueError("CLASSES doit définir entre 1 et 50 classes non vides")
        return self


@lru_cache
def get_settings():
    return Settings()
