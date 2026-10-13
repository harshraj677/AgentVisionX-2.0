import asyncio
import logging
from collections import Counter

from app.core.config import Settings
from app.core.errors import AppError
from app.privacy.detector import PrivacyDetector, RawFinding
from app.privacy.policy import SEVERITY, PolicyEngine
from app.schemas.chat import ChatMessage
from app.schemas.privacy import Finding, PrivacyRedactResponse, PrivacyScanResponse, PrivacySummary

logger = logging.getLogger("agentvision.privacy")


class PrivacyService:
    """Scans text locally, applies the policy, and redacts. Never logs or returns sensitive values."""

    def __init__(self, settings: Settings, detector: PrivacyDetector | None = None):
        self._settings = settings
        self.detector = detector or PrivacyDetector(min_score=settings.privacy_min_score)
        self.policy = PolicyEngine(settings.privacy_policy, settings.privacy_default_action)

    # ---- core (synchronous, CPU-bound) -------------------------------------------------

    def _findings(self, text: str) -> list[Finding]:
        raw = self.detector.detect(text)
        return self.merge_findings(self._with_actions(raw))

    def _with_actions(self, raw: list[RawFinding]) -> list[Finding]:
        out = []
        for r in raw:
            action = self.policy.action_for(r.category)
            if action != "allow":
                out.append(Finding(category=r.category, start=r.start, end=r.end, score=round(r.score, 2), action=action))
        return out

    def findings(self, text: str) -> list[Finding]:
        """Policy-tagged findings for arbitrary text (used for documents). Raises if the engine fails."""
        return self._findings(text)

    @staticmethod
    def merge_findings(findings: list[Finding]) -> list[Finding]:
        """Collapse overlapping spans into one. The strictest (then highest-scoring) finding names the span."""
        merged: list[Finding] = []
        for f in sorted(findings, key=lambda x: (x.start, -x.end)):
            if merged and f.start < merged[-1].end:
                prev = merged[-1]
                winner = max((prev, f), key=lambda x: (SEVERITY[x.action], x.score))
                merged[-1] = Finding(
                    category=winner.category,
                    start=prev.start,
                    end=max(prev.end, f.end),
                    score=winner.score,
                    action=winner.action,
                )
            else:
                merged.append(f)
        return merged

    @staticmethod
    def _response(findings: list[Finding]) -> PrivacyScanResponse:
        decision = PolicyEngine.strictest([f.action for f in findings])
        return PrivacyScanResponse(
            decision=decision,
            can_proceed=decision in ("allow", "warn"),
            requires_confirmation=decision == "warn",
            findings=findings,
            categories=dict(Counter(f.category for f in findings)),
        )

    @staticmethod
    def apply_redaction(text: str, findings: list[Finding]) -> str:
        for f in sorted(findings, key=lambda x: x.start, reverse=True):
            text = f"{text[: f.start]}[{f.category}]{text[f.end :]}"
        return text

    def _check_length(self, text: str) -> None:
        if len(text) > self._settings.max_message_chars:
            raise AppError(422, "message_too_long", f"Messages are limited to {self._settings.max_message_chars} characters.")

    def _guarded(self, fn, *args):
        """Fail closed: if scanning breaks, nothing is sent anywhere."""
        try:
            return fn(*args)
        except AppError:
            raise
        except Exception as exc:
            logger.error("privacy engine failure: %s", type(exc).__name__)
            raise AppError(503, "privacy_engine_unavailable", "The privacy check failed, so the request was not processed.") from None

    # ---- public async API --------------------------------------------------------------

    async def scan(self, text: str, request_id: str | None = None) -> PrivacyScanResponse:
        self._check_length(text)
        findings = await asyncio.to_thread(self._guarded, self._findings, text)
        result = self._response(findings)
        logger.info("privacy scan request_id=%s decision=%s categories=%s", request_id, result.decision, result.categories)
        return result

    async def redact(self, text: str, request_id: str | None = None) -> PrivacyRedactResponse:
        self._check_length(text)

        def work() -> tuple[str, list[Finding]]:
            redacted = self.apply_redaction(text, self._findings(text))
            return redacted, self._findings(redacted)  # always re-scan the final text

        redacted, findings = await asyncio.to_thread(self._guarded, work)
        result = self._response(findings)
        logger.info("privacy redact request_id=%s remaining_decision=%s", request_id, result.decision)
        return PrivacyRedactResponse(redacted_text=redacted, scan=result)

    async def enforce(self, messages: list[ChatMessage], acknowledged: bool, request_id: str) -> PrivacySummary:
        """Gate for /api/chat: scans every user message. Raises AppError unless sending is permitted."""

        def work() -> list[tuple[int, Finding]]:
            return [(i, f) for i, m in enumerate(messages) if m.role == "user" for f in self._findings(m.content)]

        located = await asyncio.to_thread(self._guarded, work)
        findings = [f for _, f in located]
        decision = PolicyEngine.strictest([f.action for f in findings])
        categories = dict(Counter(f.category for f in findings))
        logger.info("privacy enforce request_id=%s decision=%s categories=%s", request_id, decision, categories)

        details = {
            "decision": decision,
            "categories": categories,
            "findings": [
                {"message_index": i, "category": f.category, "start": f.start, "end": f.end, "action": f.action}
                for i, f in located
            ],
        }
        if decision == "block":
            raise AppError(403, "privacy_blocked", "This message contains sensitive information that policy does not allow to be sent. Redact or edit it first.", details)
        if decision == "redact":
            raise AppError(403, "privacy_redaction_required", "This message contains information that must be redacted before sending.", details)
        if decision == "warn" and not acknowledged:
            raise AppError(409, "privacy_confirmation_required", "This message may contain personal information. Confirm to send it.", details)
        return PrivacySummary(decision=decision, categories=categories)
