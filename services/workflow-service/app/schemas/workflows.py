from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Position(StrictModel):
    x: float = Field(default=0, allow_inf_nan=False)
    y: float = Field(default=0, allow_inf_nan=False)


class NodeData(StrictModel):
    agent_id: UUID
    label: str = Field(default="", max_length=120)
    # Mapping expressions are resolved by the execution engine in phase 4.
    input: dict[str, JsonValue] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    retries: int = Field(default=0, ge=0, le=5)


class Node(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    type: Literal["agent"] = "agent"
    position: Position = Field(default_factory=Position)
    data: NodeData


class Edge(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    sourceHandle: Literal["true", "false"] | None = None


class Graph(StrictModel):
    nodes: list[Node] = Field(default_factory=list, max_length=200)
    edges: list[Edge] = Field(default_factory=list, max_length=1000)


class Trigger(StrictModel):
    type: Literal["manual", "telegram", "gmail", "webhook", "cron"] = "manual"
    schedule: str | None = Field(default=None, max_length=120)
    connection_id: UUID | None = None


class WorkflowCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    graph: Graph = Field(default_factory=Graph)
    trigger: Trigger = Field(default_factory=Trigger)


class WorkflowUpdate(StrictModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    graph: Graph | None = None
    trigger: Trigger | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} ne peut pas être null")
        return self


class WorkflowOutput(WorkflowCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class VersionOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    snapshot: dict[str, JsonValue]
    created_at: datetime


class ValidationIssue(BaseModel):
    code: str
    detail: str
    node_id: str | None = None


class ValidationReport(BaseModel):
    valid: bool
    errors: list[ValidationIssue]
    order: list[str]


class AgentReference(BaseModel):
    status: Literal["draft", "active", "error", "disabled"]
    type_key: str = Field(min_length=1, max_length=64)
    connection_id: UUID | None = None
