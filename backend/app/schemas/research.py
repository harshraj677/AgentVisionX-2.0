from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.chat import ChatMetadata, ResearchStatus  # noqa: F401  (re-exported)
from app.schemas.optimize import PromptSource


class ResearchRequest(BaseModel):
    question: str = Field(min_length=1, description="The approved research question. It is the only text sent to the search service.")
    search_depth: Literal["basic", "advanced"] | None = Field(default=None, description="Tavily search depth (advanced costs more).")
    max_sources: int | None = Field(default=None, ge=1, le=10, description="Capped by RESEARCH_MAX_RESULTS on the server.")
    time_range: Literal["day", "week", "month", "year"] | None = None
    # Routing options, validated by the existing router exactly as in /api/chat.
    provider: Literal["gemini", "openai"] | None = None
    model: str | None = Field(default=None, max_length=100)
    routing_policy: Literal["balanced", "cost", "quality", "latency", "manual"] | None = None
    privacy_ack: bool = False
    prompt_source: PromptSource | None = None


class ResearchSource(BaseModel):
    id: int = Field(description="Stable citation number used in the answer, e.g. [1].")
    title: str
    url: str = Field(description="Validated http(s) URL exactly as returned by the search service (not fetched or verified).")
    domain: str
    snippet: str | None = Field(description="Short excerpt for display; null if the response privacy policy withheld it.")
    published_date: str | None = None
    retrieved_at: str = Field(description="When the search service returned this result (UTC).")
    flags: list[str] = []
    cited: bool = False


class CitationInfo(BaseModel):
    cited_source_ids: list[int]
    invalid_citations_removed: int = Field(description="Citation numbers in the model output that matched no source.")
    unverified_links_removed: int = Field(description="URLs in the model output that were not one of the sources.")


class SearchInfo(BaseModel):
    provider: Literal["tavily"] = "tavily"
    depth: str
    time_range: str | None
    max_sources: int
    results_returned: int
    sources_used: int
    rejected_invalid_url: int
    rejected_duplicate: int
    rejected_empty: int
    truncated: int
    flagged_sources: int
    search_latency_ms: int


class ResearchMetadata(ChatMetadata):
    search: SearchInfo


class ResearchResponse(BaseModel):
    answer: str
    evidence_status: Literal["cited", "uncited", "insufficient_evidence"] = Field(
        description="'cited' means the answer references returned sources, not that every claim was verified."
    )
    sources: list[ResearchSource]
    citations: CitationInfo
    metadata: ResearchMetadata

