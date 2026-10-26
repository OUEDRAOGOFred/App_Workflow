from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    jwt_secret: str = Field(default="", repr=False)
    jwt_secret_file: str = "/run/secrets/jwt"
    jwt_issuer: str = "workflow-auth"
    jwt_audience: str = "workflow-api"
    redis_url: str = "redis://localhost:6379/0"

    database_url: str = ""
    db_host: str = "localhost"
    db_user: str = "workflow_agents"
    db_password: str = ""
    db_name: str = "workflow_agents"
    integration_service_url: str = "http://integration-service:8000"

    @property
    def sqlalchemy_url(self):
        if self.database_url:
            return self.database_url
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            database=self.db_name,
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
        return self


@lru_cache
def get_settings():
    return Settings()
