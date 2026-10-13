from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.privacy import PrivacyScanResponse

PromptSource = Literal["original", "optimized", "edited"]


class OptimizeRequest(BaseModel):
    text: str = Field(min_length=1)


class OptimizationChange(BaseModel):
    rule: str
    description: str
    count: int


class PreservationCheck(BaseModel):
    name: str
    passed: bool


class Preservation(BaseModel):
    verified: bool = Field(description="False means a safety check failed and the original text was returned.")
    checks: list[PreservationCheck]
    note: str = ""


class TokenEstimate(BaseModel):
    """Local ESTIMATES for comparing the two prompts. They are not provider-reported usage."""

    basis: Literal["estimate"] = "estimate"
    method: str = Field(description="How the numbers were produced, e.g. tiktoken:o200k_base.")
    note: str
    original: int
    optimized: int
    saved: int
    saved_percent: float


class OptimizePrivacy(BaseModel):
    original: PrivacyScanResponse
    optimized: PrivacyScanResponse


class OptimizeResponse(BaseModel):
    optimized_text: str
    changed: bool
    changes: list[OptimizationChange]
    preservation: Preservation
    tokens: TokenEstimate
    privacy: OptimizePrivacy
