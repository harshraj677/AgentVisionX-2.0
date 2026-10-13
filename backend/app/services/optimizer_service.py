import asyncio
import logging

from app.core.config import Settings
from app.core.errors import AppError
from app.optimizer.optimizer import PromptOptimizer, RuleBasedOptimizer
from app.optimizer.tokens import TokenCounter, build_counter
from app.schemas.optimize import (
    OptimizationChange,
    OptimizePrivacy,
    OptimizeResponse,
    Preservation,
    PreservationCheck,
    TokenEstimate,
)
from app.services.privacy_service import PrivacyService

logger = logging.getLogger("agentvision.optimizer")


class OptimizerService:
    """Suggests a shorter prompt. It only ever suggests: it never sends anything to a provider, and the
    privacy gate in ChatService still applies to whatever prompt the user finally selects."""

    def __init__(
        self,
        settings: Settings,
        privacy: PrivacyService,
        optimizer: PromptOptimizer | None = None,
        counter: TokenCounter | None = None,
    ):
        self._settings = settings
        self._privacy = privacy
        self._optimizer = optimizer or RuleBasedOptimizer()
        self._counter = counter  # built lazily: the tokenizer loads on first use

    def _get_counter(self) -> TokenCounter:
        if self._counter is None:
            self._counter = build_counter()
        return self._counter

    def _work(self, text: str):
        result = self._optimizer.optimize(text)
        counter = self._get_counter()
        original_tokens, optimized_tokens = counter.count(text), counter.count(result.text)
        return result, counter, original_tokens, optimized_tokens

    async def optimize(self, text: str, request_id: str) -> OptimizeResponse:
        if len(text) > self._settings.max_message_chars:
            raise AppError(422, "message_too_long", f"Messages are limited to {self._settings.max_message_chars} characters.")
        try:
            result, counter, original_tokens, optimized_tokens = await asyncio.to_thread(self._work, text)
        except Exception as exc:
            logger.error("optimizer failure request_id=%s: %s", request_id, type(exc).__name__)
            raise AppError(500, "optimizer_error", "The prompt could not be optimized.") from None

        # Both candidates are scanned locally so the UI can show their status. The prompt the user finally
        # selects is scanned again before sending, and ChatService enforces the policy once more.
        original_scan = await self._privacy.scan(text, request_id)
        optimized_scan = original_scan if result.text == text else await self._privacy.scan(result.text, request_id)

        saved = original_tokens - optimized_tokens
        logger.info(
            "optimize request_id=%s changed=%s verified=%s rules=%s tokens_estimate=%d->%d method=%s",
            request_id, result.changed, result.verified, [c.rule for c in result.changes],
            original_tokens, optimized_tokens, counter.method,
        )
        return OptimizeResponse(
            optimized_text=result.text,
            changed=result.changed,
            changes=[OptimizationChange(rule=c.rule, description=c.description, count=c.count) for c in result.changes],
            preservation=Preservation(
                verified=result.verified,
                checks=[PreservationCheck(name=c.name, passed=c.passed) for c in result.checks],
                note=result.note,
            ),
            tokens=TokenEstimate(
                method=counter.method,
                note=counter.note,
                original=original_tokens,
                optimized=optimized_tokens,
                saved=saved,
                saved_percent=round(100 * saved / original_tokens, 1) if original_tokens else 0.0,
            ),
            privacy=OptimizePrivacy(original=original_scan, optimized=optimized_scan),
        )
