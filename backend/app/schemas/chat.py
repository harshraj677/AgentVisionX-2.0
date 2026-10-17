from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.optimize import PromptSource
from app.schemas.routing import RoutingInfo
from app.schemas.privacy import PrivacyStatus, PrivacySummary, ResponsePrivacy


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    provider: Literal["gemini", "openai"] | None = Field(
        default=None, description="Optional: restrict routing to one provider. Omit to let the router choose."
    )
    model: str | None = Field(
        default=None, max_length=100, description="Manual model choice. Must be a configured, eligible model."
    )
    routing_policy: Literal["balanced", "cost", "quality", "latency", "manual"] | None = Field(
        default=None, description="Omit to use the server default policy."
    )
    prompt_source: PromptSource | None = Field(default=None, description="Which version of the prompt the user chose to send.")
    privacy_ack: bool = Field(default=False, description="User confirmed sending content that triggered a privacy warning.")


class Usage(BaseModel):
    """Token counts. `source` says where they came from; the MVP never estimates."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    source: Literal["provider", "unavailable"] = "unavailable"


class ChatMetadata(BaseModel):
    request_id: str
    provider: str
    model: str
    latency_ms: int
    status: Literal["success"] = "success"
    usage: Usage
    privacy: PrivacySummary
    prompt_source: PromptSource | None = None
    routing: RoutingInfo | None = None
    response_privacy: ResponsePrivacy | None = None


class ChatResponse(BaseModel):
    message: ChatMessage
    metadata: ChatMetadata


class ResearchStatus(BaseModel):
    provider: Literal["tavily"] = "tavily"
    configured: bool


class DocumentsHealth(BaseModel):
    database_configured: bool
    embedding_configured: bool
    embedding_model: str
    embedding_dimensions: int


class ReadyResponse(BaseModel):
    """Readiness, separate from liveness (/health). Always HTTP 200 when the server itself is up: chat, privacy and
    research work without a database, so an unavailable database is reported here, not as a failure of the server."""

    status: Literal["ok", "degraded"]
    database: Literal["not_configured", "ok", "not_migrated", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    providers: dict[str, bool] = Field(description="Whether each provider has an API key configured.")
    privacy: PrivacyStatus
    research: ResearchStatus | None = None
    documents: DocumentsHealth | None = None

