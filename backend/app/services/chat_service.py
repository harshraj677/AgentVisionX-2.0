import logging
import time

from app.core.config import Settings
from app.core.errors import AppError
from app.router.classifier import classify
from app.router.router import ModelRouter, RoutedFailure, RoutingError
from app.schemas.chat import ChatMessage, ChatMetadata, ChatRequest, ChatResponse
from app.services.privacy_service import PrivacyService
from app.services.response_privacy_service import ResponsePrivacyService

logger = logging.getLogger("agentvision.chat")


class ChatService:
    """validate -> privacy gate -> classify -> route -> provider (bounded fallback) -> response privacy scan -> respond."""

    def __init__(
        self, settings: Settings, router: ModelRouter, privacy: PrivacyService, response_privacy: ResponsePrivacyService
    ):
        self._settings = settings
        self._router = router
        self._privacy = privacy
        self._response_privacy = response_privacy

    def _validate(self, req: ChatRequest) -> None:
        s = self._settings
        if len(req.messages) > s.max_messages:
            raise AppError(422, "too_many_messages", f"At most {s.max_messages} messages are allowed.")
        if any(len(m.content) > s.max_message_chars for m in req.messages):
            raise AppError(422, "message_too_long", f"Messages are limited to {s.max_message_chars} characters.")
        if req.messages[-1].role != "user":
            raise AppError(422, "invalid_conversation", "The last message must be from the user.")
        if not req.messages[-1].content.strip():
            raise AppError(422, "empty_message", "The message is empty.")
        if req.model and req.routing_policy not in (None, "manual"):
            raise AppError(422, "conflicting_routing", "'model' can only be combined with routing_policy 'manual'.")

    async def chat(self, req: ChatRequest, request_id: str) -> ChatResponse:
        self._validate(req)
        # Privacy gate: runs locally, before routing or any provider is even looked at. Raises if sending is not permitted.
        privacy = await self._privacy.enforce(req.messages, req.privacy_ack, request_id)

        # Local, deterministic task classification of the latest user message (never sent anywhere).
        task = classify(req.messages[-1].content)
        policy = "manual" if (req.model and req.routing_policy is None) else req.routing_policy
        try:
            plan = self._router.plan(
                task, policy, req.provider, req.model, sensitive=privacy.decision == "warn"
            )
        except RoutingError as exc:
            logger.info("route rejected request_id=%s code=%s", request_id, exc.code)
            raise AppError(exc.status_code, exc.code, exc.message, exc.details) from None
        logger.info(
            "route planned request_id=%s task=%s confidence=%s policy=%s selected=%s/%s signals=%s",
            request_id, task.task_type, task.confidence, plan.policy,
            plan.ordered[0].provider, plan.ordered[0].model, list(task.signals),
        )

        start = time.perf_counter()
        try:
            outcome = await self._router.execute(plan, req.messages, request_id)
        except RoutedFailure as failure:
            # Log metadata only, never prompt or response text.
            logger.info(
                "chat failed request_id=%s code=%s attempts=%d latency_ms=%d",
                request_id, failure.error.code, len(failure.attempts), int((time.perf_counter() - start) * 1000),
            )
            raise AppError(
                failure.error.status_code,
                failure.error.code,
                failure.error.message,
                {"routing": {
                    "task_type": task.task_type,
                    "routing_policy": plan.policy,
                    "attempts": [a.model_dump() for a in failure.attempts],
                }},
            ) from None

        latency_ms = int((time.perf_counter() - start) * 1000)
        info = outcome.info()

        # Response privacy gate: runs on the final successful reply (including after a permitted fallback), before
        # anything is returned. It never triggers another provider call and never changes which model was chosen.
        try:
            reply, response_privacy = await self._response_privacy.process(outcome.result.text, request_id)
        except AppError as exc:
            exc.details = {
                **(exc.details if isinstance(exc.details, dict) else {}),
                "routing": {
                    "task_type": task.task_type,
                    "routing_policy": plan.policy,
                    "selected_provider": info.selected_provider,
                    "selected_model": info.selected_model,
                    "attempts": [a.model_dump() for a in info.attempts],
                },
            }
            raise
        logger.info(
            "chat ok request_id=%s provider=%s model=%s latency_ms=%d tokens=%s prompt_source=%s fallback=%s",
            request_id, info.selected_provider, outcome.result.model, latency_ms,
            outcome.result.usage.total_tokens, req.prompt_source, info.fallback_used,
        )
        return ChatResponse(
            message=ChatMessage(role="assistant", content=reply),
            metadata=ChatMetadata(
                request_id=request_id,
                provider=info.selected_provider,
                model=outcome.result.model,
                latency_ms=latency_ms,
                usage=outcome.result.usage,
                privacy=privacy,
                prompt_source=req.prompt_source,
                routing=info,
                response_privacy=response_privacy,
            ),
        )
