from typing import Literal

from pydantic import BaseModel, Field

from app.privacy.policy import Action


class Finding(BaseModel):
    """A detected span. Deliberately carries no copy of the sensitive value itself."""

    category: str
    start: int = Field(description="Start offset in Unicode code points.")
    end: int
    score: float
    action: Action


class PrivacyScanRequest(BaseModel):
    text: str = Field(min_length=1)


class PrivacyScanResponse(BaseModel):
    decision: Action
    can_proceed: bool = Field(description="True only for allow/warn. Warn also needs user confirmation.")
    requires_confirmation: bool
    findings: list[Finding]
    categories: dict[str, int]


class PrivacyRedactRequest(BaseModel):
    text: str = Field(min_length=1)


class PrivacyRedactResponse(BaseModel):
    redacted_text: str
    scan: PrivacyScanResponse = Field(description="Fresh scan of redacted_text.")


class PrivacySummary(BaseModel):
    """Privacy outcome attached to a chat response."""

    checked: bool = True
    decision: Action
    categories: dict[str, int] = {}


class ResponsePrivacy(BaseModel):
    """Outcome of scanning the AI's reply. Holds category names and counts only, never matched text."""

    status: Literal["clean", "allowed", "warned", "redacted", "blocked"]
    policy: str = Field(description="Active RESPONSE_PRIVACY_MODE: 'policy' (per-category table) or a uniform action.")
    findings_count: int = 0
    categories: dict[str, int] = {}
    redacted: bool = False
    redacted_spans: int = 0


class PrivacyStatus(BaseModel):
    engine: str = "presidio"
    ready: bool
    response_policy: str = Field(default="policy", description="Active RESPONSE_PRIVACY_MODE (read-only; set on the server).")
