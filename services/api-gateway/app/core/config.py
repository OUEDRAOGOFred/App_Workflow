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

    auth_service_url: str = "http://auth-service:8000"
    agent_service_url: str = "http://agent-service:8000"
    workflow_service_url: str = "http://workflow-service:8000"
    integration_service_url: str = "http://integration-service:8000"
    execution_service_url: str = "http://execution-service:8000"
    classifier_service_url: str = "http://ml-classifier-service:8000"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]
    rate_limit_per_minute: int = Field(default=120, ge=1)
    auth_rate_limit_per_minute: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def load_secret(self):
        if not self.jwt_secret:
            path = Path(self.jwt_secret_file)
            if not path.is_file():
                raise ValueError("JWT_SECRET ou JWT_SECRET_FILE requis")
            self.jwt_secret = path.read_text().strip()
        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET doit contenir au moins 32 caractères")
        return self


@lru_cache
def get_settings():
    return Settings()
