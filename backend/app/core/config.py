from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator, field_validator

from app.privacy.response_policy import ResponsePolicy
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelProfile(BaseModel):
    """Prices YOU supply (per million tokens). The app ships no prices; missing values mean "unknown"."""

    input_cost_per_mtok: float | None = Field(default=None, ge=0)
    output_cost_per_mtok: float | None = Field(default=None, ge=0)


class Settings(BaseSettings):
    """Backend configuration, loaded from environment variables / the .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_models: str = ""  # extra Gemini models the router may use (comma-separated); GEMINI_MODEL is always included

    openai_api_key: SecretStr | None = None
    openai_model: str = ""

    provider_timeout_seconds: float = 30
    max_message_chars: int = 8000
    max_messages: int = 40
    max_request_bytes: int = 200_000

    # Privacy engine. PRIVACY_POLICY is JSON, e.g. {"EMAIL_ADDRESS": "warn", "PERSON": "allow"}.
    privacy_policy: dict[str, Literal["allow", "warn", "redact", "block"]] = {}
    privacy_default_action: Literal["allow", "warn", "redact", "block"] = "warn"
    privacy_min_score: float = 0.4

    # Response privacy (Phase 5): what to do when the AI's REPLY contains sensitive data. Separate from the input policy.
    # RESPONSE_PRIVACY_MODE: "policy" = built-in per-category table (secrets redacted, contact data warned);
    # or one uniform action for every category: allow | warn | redact | block.
    response_privacy_mode: Literal["policy", "allow", "warn", "redact", "block"] = "policy"
    response_privacy_policy: dict[str, Literal["ignore", "allow", "warn", "redact", "block"]] = {}
    response_privacy_default_action: Literal["ignore", "allow", "warn", "redact", "block"] = "warn"
    response_privacy_max_chars: int = Field(default=200_000, ge=1000)

    # Documents / RAG (Phase 7). DATABASE_URL must point at PostgreSQL with the pgvector extension; without it the
    # document endpoints return documents_database_not_configured. Embeddings use GEMINI_API_KEY (backend only).
    database_url: SecretStr | None = None
    embedding_model: str = "gemini-embedding-001"
    # Must equal the vector column size created by the migration. pgvector HNSW indexes support at most 2000.
    embedding_dimensions: int = Field(default=768, ge=128, le=2000)
    embedding_batch_size: int = Field(default=32, ge=1, le=100)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=120)
    document_max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    document_max_pages: int = Field(default=300, ge=1, le=2000)
    document_max_extracted_chars: int = Field(default=300_000, ge=1000, le=2_000_000)
    document_min_text_chars: int = Field(default=20, ge=1, le=10_000)
    document_extraction_timeout_seconds: float = Field(default=30, gt=0, le=300)
    chunk_size_chars: int = Field(default=1000, ge=200, le=4000)
    chunk_overlap_chars: int = Field(default=150, ge=0, le=2000)
    max_chunks_per_document: int = Field(default=500, ge=1, le=5000)
    rag_top_k: int = Field(default=6, ge=1, le=20)
    # Cosine similarity (-1..1) between the question and a passage embedding; passages below it are not used.
    rag_min_similarity: float = Field(default=0.45, ge=-1, le=1)
    rag_max_context_chars: int = Field(default=8000, ge=1000, le=30000)

    # Research (Phase 6): Tavily web search + grounded synthesis. Without TAVILY_API_KEY, /api/research returns
    # research_provider_not_configured. Bounds keep latency, cost and prompt size predictable.
    tavily_api_key: SecretStr | None = None
    tavily_timeout_seconds: float = Field(default=15, gt=0, le=60)
    research_max_results: int = Field(default=5, ge=1, le=10)
    research_max_source_chars: int = Field(default=1500, ge=200, le=5000)
    research_max_total_source_chars: int = Field(default=8000, ge=1000, le=30000)
    research_max_query_chars: int = Field(default=400, ge=20, le=400)
    research_default_depth: Literal["basic", "advanced"] = "basic"

    # Router (Phase 4). All optional; the defaults keep a Gemini-only setup working exactly as before.
    router_default_policy: Literal["balanced", "cost", "quality", "latency", "manual"] = "balanced"
    router_task_models: dict[
        Literal["conversation", "coding", "summarization", "reasoning", "extraction", "research", "documents"], str
    ] = {}
    model_profiles: dict[str, ModelProfile] = {}
    router_allowed_providers: str = ""  # empty = every configured provider
    router_sensitive_providers: str = ""  # providers allowed when the user confirmed a privacy warning; empty = same as allowed
    router_fallback: Literal["off", "cheaper_or_equal", "any_eligible"] = "cheaper_or_equal"
    router_max_fallbacks: int = Field(default=1, ge=0, le=2)
    router_failure_threshold: int = Field(default=3, ge=1)
    router_cooldown_seconds: float = Field(default=60, ge=0)
    router_min_latency_samples: int = Field(default=3, ge=1)
    router_total_timeout_seconds: float = Field(default=80, gt=0)

    cors_origins: str = "http://localhost:8081,http://localhost:19006"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_chunking(self) -> "Settings":
        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("CHUNK_OVERLAP_CHARS must be smaller than CHUNK_SIZE_CHARS")
        return self

    @property
    def gemini_model_list(self) -> list[str]:
        extra = [m.strip() for m in self.gemini_models.split(",") if m.strip()]
        return list(dict.fromkeys([self.gemini_model, *extra]))

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [p.strip().lower() for p in value.split(",") if p.strip()]

    @property
    def allowed_providers(self) -> list[str]:
        return self._csv(self.router_allowed_providers)

    @property
    def sensitive_providers(self) -> list[str]:
        return self._csv(self.router_sensitive_providers)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
