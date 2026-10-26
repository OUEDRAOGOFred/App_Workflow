from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Status = Literal["queued", "running", "success", "failed", "cancelled"]


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    event_id: str | None = Field(default=None, min_length=1, max_length=200)


class AgentTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    input: dict[str, JsonValue] = Field(default_factory=dict)


class StepOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    node_id: str
    agent_id: UUID | None
    status: str
    input: dict[str, JsonValue]
    output: dict[str, JsonValue] | None
    attempt: int
    error: str | None
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workflow_id: UUID | None
    workflow_version: int | None
    status: Status
    source: str
    trigger_payload: dict[str, JsonValue]
    cancel_requested: bool
    error: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExecutionDetail(ExecutionOutput):
    workflow_name: str
    graph: dict[str, JsonValue]
    steps: list[StepOutput]
