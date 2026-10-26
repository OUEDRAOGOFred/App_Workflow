from pydantic import BaseModel, ConfigDict, Field


class ClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=200000)
    confidence_threshold: float = Field(default=0, ge=0, le=1)


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    label: str
    confidence: float = Field(ge=0, le=1, strict=True)


class ClassificationOutput(Prediction):
    raw: str
    scores: dict[str, float]
    backend: str
    reason: str | None = None


class LLMInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    prompt: str = Field(min_length=1, max_length=50000)
    temperature: float = Field(default=0.2, ge=0, le=2)
