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
    db_user: str = "workflow_integrations"
    db_password: str = ""
    db_name: str = "workflow_integrations"

    fernet_key: str = Field(default="", repr=False)
    fernet_key_file: str = "/run/secrets/fernet"
    mock_integrations: bool = True
    google_client_id: str = Field(default="", repr=False)
    google_client_secret: str = Field(default="", repr=False)
    google_redirect_uri: str = (
        "http://localhost:8000/api/v1/integrations/google/oauth/callback"
    )
    google_test_user_email: str = ""
    telegram_token: str = Field(default="", repr=False)
    gmail_app_password: str = Field(default="", repr=False)
    polling_enabled: bool = True
    gmail_poll_seconds: int = Field(default=30, ge=5, le=3600)
    telegram_poll_seconds: int = Field(default=1, ge=1, le=3600)
    http_allowed_hosts: str = ""
    max_file_bytes: int = Field(default=5242880, ge=1024, le=10485760)

    @property
    def effective_mock(self) -> bool:
        return self.mock_integrations or not (
            self.google_client_id and self.google_client_secret
        )

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
