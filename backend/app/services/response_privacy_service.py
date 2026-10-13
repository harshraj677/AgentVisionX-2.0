import asyncio
import logging
from collections import Counter

from app.core.config import Settings
from app.core.errors import AppError
from app.privacy.detector import PrivacyDetector
from app.privacy.policy import SEVERITY
from app.privacy.response_policy import ResponsePolicy
from app.schemas.privacy import Finding, ResponsePrivacy
from app.services.privacy_service import PrivacyService

logger = logging.getLogger("agentvision.privacy")

_ACTION_FOR_FINDING = {"allow", "warn", "redact", "block"}


class ResponsePrivacyService:
    """Scans a provider's reply locally (same Presidio engine as input scanning, no external service), applies the
    response policy, and either returns text that is safe to send to the client or raises. It never calls a provider,
    and never logs or returns matched values.

    Fail-closed: if the reply cannot be scanned, redacted or verified, the original is NOT returned.
    """

    def __init__(self, settings: Settings, detector: PrivacyDetector):
        self._settings = settings
        self._detector = detector
        self.policy = ResponsePolicy(
            settings.response_privacy_mode, settings.response_privacy_policy, settings.response_privacy_default_action
        )

    # ---- synchronous core ---------------------------------------------------------------------------------

    def _findings(self, text: str) -> list[Finding]:
        found: list[Finding] = []
        for r in self._detector.detect(text):
            action = self.policy.action_for(r.category)
            if action in _ACTION_FOR_FINDING:  # "ignore" is dropped
                found.append(Finding(category=r.category, start=r.start, end=r.end, score=round(r.score, 2), action=action))  # type: ignore[arg-type]
        return PrivacyService.merge_findings(found)

    def _scan(self, text: str) -> tuple[str, ResponsePrivacy, str | None]:
        """Returns (text_to_return, summary, failure_code). failure_code is set when the reply must be withheld."""
        if not text.strip():
            return text, self._summary("clean", [], 0), None
        if len(text) > self._settings.response_privacy_max_chars:
            return text, self._summary("blocked", [], 0), "response_privacy_unavailable"

        findings = self._findings(text)
        if not findings:
            return text, self._summary("clean", [], 0), None
        decision = max((f.action for f in findings), key=lambda a: SEVERITY[a])

        if decision == "block":
            return text, self._summary("blocked", findings, 0), "response_privacy_blocked"
        if decision == "redact":
            to_redact = [f for f in findings if f.action == "redact"]
            redacted = PrivacyService.apply_redaction(text, to_redact)
            # Verify: no redact/block-level finding may survive in what we are about to return.
            if any(f.action in ("redact", "block") for f in self._findings(redacted)):
                return text, self._summary("blocked", findings, 0), "response_privacy_redaction_failed"
            return redacted, self._summary("redacted", findings, len(to_redact)), None
        return text, self._summary("warned" if decision == "warn" else "allowed", findings, 0), None

    def _summary(self, status: str, findings: list[Finding], redacted_spans: int) -> ResponsePrivacy:
        return ResponsePrivacy(
            status=status,  # type: ignore[arg-type]
            policy=self.policy.mode,
            findings_count=len(findings),
            categories=dict(Counter(f.category for f in findings)),
            redacted=redacted_spans > 0,
            redacted_spans=redacted_spans,
        )

    def scan_fragment(self, text: str) -> str | None:
        """Applies the response policy to a short third-party fragment (e.g. a source snippet). Returns the text
        to show (possibly redacted), or None if the policy says to withhold it or it cannot be checked."""
        try:
            out, _summary, failure = self._scan(text)
        except Exception:
            return None
        return None if failure else out

    # ---- public --------------------------------------------------------------------------------------------

    async def process(self, text: str, request_id: str) -> tuple[str, ResponsePrivacy]:
        """Returns the text that may be sent to the client plus a value-free summary, or raises AppError."""
        try:
            out_text, summary, failure = await asyncio.to_thread(self._scan, text)
        except Exception as exc:  # scanner crashed: fail closed, log only the exception type
            logger.error("response privacy scan failed request_id=%s: %s", request_id, type(exc).__name__)
            raise AppError(
                503, "response_privacy_unavailable",
                "The AI's reply could not be privacy-checked, so it was withheld. Please try again.",
            ) from None

        logger.info(
            "response privacy request_id=%s status=%s policy=%s findings=%d categories=%s redacted_spans=%d",
            request_id, summary.status, summary.policy, summary.findings_count, summary.categories, summary.redacted_spans,
        )
        if failure:
            messages = {
                "response_privacy_blocked": (
                    403,
                    "The AI's reply was withheld because it contained sensitive information that the response "
                    "privacy policy does not allow to be returned.",
                ),
                "response_privacy_redaction_failed": (
                    503, "The AI's reply could not be safely redacted, so it was withheld.",
                ),
                "response_privacy_unavailable": (
                    503, "The AI's reply could not be privacy-checked (it may be too large), so it was withheld.",
                ),
            }
            status, message = messages[failure]
            raise AppError(status, failure, message, {"response_privacy": summary.model_dump()})
        return out_text, summary
