from typing import Literal

from pydantic import BaseModel, Field

TaskType = Literal["conversation", "coding", "summarization", "reasoning", "extraction", "research", "documents", "unknown"]
Policy = Literal["balanced", "cost", "quality", "latency", "manual"]
Confidence = Literal["high", "medium", "low"]


class RoutingAttempt(BaseModel):
    provider: str
    model: str
    outcome: str = Field(description="'success' or the error code of a failed attempt.")


class RoutingInfo(BaseModel):
    """Why a model was chosen. Contains no prompt text, credentials or internal reasoning."""

    task_type: TaskType
    task_confidence: Confidence
    routing_policy: Policy
    selected_provider: str
    selected_model: str
    routing_reason: str
    fallback_used: bool = False
    fallback_reason: str | None = None
    attempts: list[RoutingAttempt] = []


class ModelStatus(BaseModel):
    id: str
    is_default: bool
    available: bool
    unavailable_reason: str | None = None
    input_cost_per_mtok: float | None = Field(default=None, description="From your MODEL_PROFILES config; null = unknown.")
    output_cost_per_mtok: float | None = None
    measured_latency_ms: float | None = Field(default=None, description="Median of this process's recent successful calls; null until measured.")
    latency_samples: int = 0


class ProviderModels(BaseModel):
    name: str
    configured: bool
    implemented: bool
    allowed: bool
    models: list[ModelStatus]


class RouterModelsResponse(BaseModel):
    default_policy: Policy
    policies: list[Policy]
    fallback_mode: str
    default_model: str | None
    eligible_count: int
    providers: list[ProviderModels]
