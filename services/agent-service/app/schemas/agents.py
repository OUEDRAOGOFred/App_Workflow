from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Status = Literal["draft", "active", "error", "disabled"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentCreate(StrictModel):
    type_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    connection_id: UUID | None = None


class AgentUpdate(StrictModel):
    type_key: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    config: dict[str, JsonValue] | None = None
    connection_id: UUID | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in self.model_fields_set - {"connection_id"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} ne peut pas être null")
        return self


class AgentOutput(AgentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    status: Status
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentTypeOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    label: str
    description: str
    icon: str
    requires_connection: bool
    config_schema: dict[str, JsonValue]


class DraftData(StrictModel):
    type_key: str | None = Field(default=None, max_length=64)
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=4000)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    connection_id: UUID | None = None


class DraftBody(StrictModel):
    step: int = Field(default=1, ge=1, le=5)
    data: DraftData = Field(default_factory=DraftData)


class DraftOutput(DraftBody):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime
