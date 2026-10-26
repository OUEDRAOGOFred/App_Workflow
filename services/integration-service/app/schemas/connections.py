from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )


class PollConfig(StrictModel):
    query: str = Field(default="is:unread", max_length=1000)
    label_ids: list[str] = Field(default_factory=list, max_length=20)
    interval_seconds: int | None = Field(default=None, ge=5, le=3600)


class ConnectionCreate(StrictModel):
    type: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=120)
    credentials: dict[str, SecretStr] = Field(default_factory=dict)
    poll_config: PollConfig = Field(default_factory=PollConfig)

    @field_validator("type")
    @classmethod
    def registered_type(cls, value):
        from app.connectors.registry import REGISTRY

        if value not in REGISTRY:
            raise ValueError("Type de connecteur inconnu")
        return value


class TelegramConnect(StrictModel):
    name: str = Field(default="Bot Telegram", min_length=1, max_length=120)
    token: SecretStr | None = None


class ConnectionOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    type: str
    name: str
    status: str
    is_mock: bool
    poll_config: PollConfig
    created_at: datetime
    updated_at: datetime


class ActionInput(StrictModel):
    params: dict[str, JsonValue] = Field(default_factory=dict)
