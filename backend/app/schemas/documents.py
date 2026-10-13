from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.chat import ChatMetadata
from app.schemas.optimize import PromptSource


class DocumentPrivacy(BaseModel):
    """Ingestion privacy state. Category names and counts only, never document text."""

    required_action: Literal["none", "confirm", "redact"]
    decision: Literal["none", "not_required", "confirmed", "redacted"]
    categories: dict[str, int] = {}


class DocumentOut(BaseModel):
    id: str
    filename: str
    file_type: Literal["pdf", "txt"]
    size_bytes: int
    page_count: int | None
    char_count: int | None
    status: Literal["processing", "needs_review", "ready", "failed"]
    error_code: str | None = Field(description="Safe error code when status is 'failed'.")
    privacy: DocumentPrivacy
    embedding_model: str | None
    chunk_count: int
    uploaded_at: datetime | None
    processed_at: datetime | None


class DocumentList(BaseModel):
    documents: list[DocumentOut]


class PrivacyDecisionRequest(BaseModel):
    decision: Literal["confirm", "redact"] = Field(
        description="confirm: send the text as-is (only allowed for warn-level findings). "
        "redact: replace every detected item with [CATEGORY] first; the original text is then discarded."
    )


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    document_ids: list[str] = Field(min_length=1, max_length=20)
    top_k: int | None = Field(default=None, ge=1, le=20, description="Capped by RAG_TOP_K.")
    provider: Literal["gemini", "openai"] | None = None
    model: str | None = Field(default=None, max_length=100)
    routing_policy: Literal["balanced", "cost", "quality", "latency", "manual"] | None = None
    privacy_ack: bool = False
    prompt_source: PromptSource | None = None


class DocumentCitation(BaseModel):
    ref: str = Field(description="Reference used in the answer, e.g. 'D1'.")
    document_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    page_number: int | None
    similarity: float = Field(description="Cosine similarity reported by pgvector for this passage.")
    excerpt: str | None = Field(description="Short excerpt of the passage; null if the response privacy policy withheld it.")
    flags: list[str] = []
    cited: bool


class RetrievalInfo(BaseModel):
    embedding_model: str
    top_k: int
    candidates: int
    below_threshold: int
    deduplicated: int
    used: int
    min_similarity: float
    best_similarity: float | None
    context_chars: int
    flagged_passages: int


class AskMetadata(ChatMetadata):
    retrieval: RetrievalInfo


class AskResponse(BaseModel):
    answer: str
    evidence_status: Literal["cited", "uncited", "insufficient_evidence"] = Field(
        description="'cited' means the answer references retrieved passages, not that every claim was verified."
    )
    passages: list[DocumentCitation] = Field(description="Every passage given to the model, with `cited` marking those the answer references.")
    invalid_citations_removed: int
    unverified_links_removed: int
    metadata: AskMetadata

