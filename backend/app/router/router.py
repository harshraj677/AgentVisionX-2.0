"""Model router: decides which configured model handles a request, and what happens if it fails.

Everything is deterministic and local. The router never calls a model to make its decision, never ranks on data it
doesn't have (unknown price/latency means "unknown", with a documented fallback to the default model), and only
ever routes to providers/models that are configured, implemented, allowed for this data, and currently healthy.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Sequence

from app.core.config import Settings
from app.providers.base import LLMProvider, ProviderError, ProviderResult
from app.providers.registry import ProviderRegistry
from app.router.availability import AvailabilityTracker, LatencyStats, ModelKey
from app.router.classifier import TaskClassification
from app.schemas.chat import ChatMessage
from app.schemas.routing import ModelStatus, Policy, ProviderModels, RouterModelsResponse, RoutingAttempt, RoutingInfo

logger = logging.getLogger("agentvision.router")

# Provider failures after which trying a DIFFERENT model can help. Never used to retry the same model.
FALLBACK_CODES = {"provider_timeout", "provider_error", "rate_limited", "provider_auth_error", "model_not_found"}

_SETUP_HINT = {
    "gemini": ("Gemini", "GEMINI_API_KEY"),
    "openai": ("OpenAI", "OPENAI_API_KEY"),
}


class RoutingError(Exception):
    """Raised before any provider call when no valid route exists."""

    def __init__(self, status_code: int, code: str, message: str, details: object | None = None):
        super().__init__(message)
        self.status_code, self.code, self.message, self.details = status_code, code, message, details


class RoutedFailure(Exception):
    """Every permitted attempt failed. Carries the last provider error and what was tried."""

    def __init__(self, error: ProviderError, attempts: list[RoutingAttempt]):
        super().__init__(error.message)
        self.error, self.attempts = error, attempts


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    input_cost: float | None
    output_cost: float | None
    latency_ms: float | None
    latency_samples: int
    is_default: bool

    @property
    def key(self) -> ModelKey:
        return (self.provider, self.model)

    @property
    def cost(self) -> float | None:
        """Input + output price per million tokens: a simple, comparable proxy. None unless BOTH are configured."""
        if self.input_cost is None or self.output_cost is None:
            return None
        return self.input_cost + self.output_cost


@dataclass
class RoutePlan:
    task: TaskClassification
    policy: Policy
    ordered: list[Candidate]  # ordered[0] is the primary; the rest are potential fallbacks, best first
    reason: str


@dataclass
class RouteOutcome:
    result: ProviderResult
    selected: Candidate
    plan: RoutePlan
    attempts: list[RoutingAttempt] = field(default_factory=list)
    fallback_reason: str | None = None

    def info(self) -> RoutingInfo:
        return RoutingInfo(
            task_type=self.plan.task.task_type,
            task_confidence=self.plan.task.confidence,
            routing_policy=self.plan.policy,
            selected_provider=self.selected.provider,
            selected_model=self.selected.model,
            routing_reason=self.plan.reason,
            fallback_used=self.fallback_reason is not None,
            fallback_reason=self.fallback_reason,
            attempts=self.attempts,
        )


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        registry: ProviderRegistry,
        tracker: AvailabilityTracker | None = None,
        stats: LatencyStats | None = None,
    ):
        self._settings = settings
        self._registry = registry
        self.tracker = tracker or AvailabilityTracker(settings.router_failure_threshold, settings.router_cooldown_seconds)
        self.stats = stats or LatencyStats()

    # ---- eligibility ---------------------------------------------------------------------------

    def _ordered_providers(self) -> list[LLMProvider]:
        providers = [p for n in self._registry.names() if (p := self._registry.get(n))]
        allowed = self._settings.allowed_providers
        if allowed:  # honour the order the operator listed
            providers.sort(key=lambda p: allowed.index(p.name) if p.name in allowed else len(allowed))
        return providers

    def _provider_block_reason(self, p: LLMProvider, sensitive: bool) -> str | None:
        s = self._settings
        if s.allowed_providers and p.name not in s.allowed_providers:
            return "not in ROUTER_ALLOWED_PROVIDERS"
        if sensitive and s.sensitive_providers and p.name not in s.sensitive_providers:
            return "not allowed for requests containing confirmed personal data (ROUTER_SENSITIVE_PROVIDERS)"
        if not p.supports_generation:
            return "adapter not implemented yet"
        if not p.is_configured():
            return "not configured"
        return None

    def _candidate(self, p: LLMProvider, model: str) -> Candidate:
        profile = self._settings.model_profiles.get(model)
        latency, samples = self.stats.median((p.name, model), self._settings.router_min_latency_samples)
        return Candidate(
            provider=p.name,
            model=model,
            input_cost=profile.input_cost_per_mtok if profile else None,
            output_cost=profile.output_cost_per_mtok if profile else None,
            latency_ms=latency,
            latency_samples=samples,
            is_default=model == p.default_model(),
        )

    def eligible(self, provider: str | None = None, sensitive: bool = False) -> list[Candidate]:
        out: list[Candidate] = []
        for p in self._ordered_providers():
            if provider and p.name != provider:
                continue
            if self._provider_block_reason(p, sensitive):
                continue
            for model in p.list_models():
                if self.tracker.is_available((p.name, model)):
                    out.append(self._candidate(p, model))
        return out

    def snapshot(self) -> RouterModelsResponse:
        """Everything the settings screen needs, with unknowns left as null."""
        providers: list[ProviderModels] = []
        for p in self._ordered_providers():
            block = self._provider_block_reason(p, sensitive=False)
            statuses = []
            for model in p.list_models():
                c = self._candidate(p, model)
                up = self.tracker.is_available(c.key)
                reason = block or (None if up else "temporarily unavailable after repeated failures")
                statuses.append(
                    ModelStatus(
                        id=model, is_default=c.is_default, available=reason is None, unavailable_reason=reason,
                        input_cost_per_mtok=c.input_cost, output_cost_per_mtok=c.output_cost,
                        measured_latency_ms=c.latency_ms, latency_samples=c.latency_samples,
                    )
                )
            providers.append(
                ProviderModels(
                    name=p.name, configured=p.is_configured(), implemented=p.supports_generation,
                    allowed=not (self._settings.allowed_providers and p.name not in self._settings.allowed_providers),
                    models=statuses,
                )
            )
        eligible = self.eligible()
        default = self._global_default(eligible)
        return RouterModelsResponse(
            default_policy=self._settings.router_default_policy,
            policies=["balanced", "cost", "quality", "latency", "manual"],
            fallback_mode=self._settings.router_fallback,
            default_model=default.model if default else None,
            eligible_count=len(eligible),
            providers=providers,
        )

    @staticmethod
    def _global_default(cands: Sequence[Candidate]) -> Candidate | None:
        """Default model of the first eligible provider (providers keep their configured order)."""
        if not cands:
            return None
        first_provider = cands[0].provider
        return next((c for c in cands if c.provider == first_provider and c.is_default), cands[0])

    # ---- planning --------------------------------------------------------------------------------

    def plan(
        self,
        task: TaskClassification,
        policy: Policy | None,
        provider: str | None,
        model: str | None,
        sensitive: bool,
    ) -> RoutePlan:
        policy = policy or self._settings.router_default_policy
        if provider:
            self._require_usable_provider(provider)
        cands = self.eligible(provider, sensitive)

        if policy == "manual":
            return self._plan_manual(task, model, cands)
        if not cands:
            self._raise_no_candidates(provider)
        ordered, reason = self._rank(policy, task, cands)
        return RoutePlan(task, policy, ordered, reason)

    def _require_usable_provider(self, name: str) -> None:
        p = self._registry.get(name)
        if p is None:
            raise RoutingError(400, "unknown_provider", f"Unknown provider '{name}'.")
        label, env = _SETUP_HINT.get(name, (name, f"{name.upper()}_API_KEY"))
        if not p.is_configured():
            raise RoutingError(503, "provider_not_configured", f"{label} is not configured. Set {env} in backend/.env.")
        if not p.supports_generation:
            raise RoutingError(501, "provider_not_implemented", f"The {label} adapter is not implemented yet.")

    def _raise_no_candidates(self, provider: str | None) -> None:
        providers = self._ordered_providers()
        if not any(p.is_configured() and p.supports_generation for p in providers):
            raise RoutingError(
                503, "provider_not_configured", "No AI provider is configured. Set GEMINI_API_KEY in backend/.env."
            )
        raise RoutingError(
            503,
            "no_eligible_provider",
            "No configured model is eligible right now (models may be cooling down after failures, or excluded by "
            "ROUTER_ALLOWED_PROVIDERS / ROUTER_SENSITIVE_PROVIDERS). Try again shortly or adjust the configuration.",
        )

    def _plan_manual(self, task: TaskClassification, model: str | None, cands: list[Candidate]) -> RoutePlan:
        listing = {"eligible_models": [f"{c.provider}/{c.model}" for c in cands]}
        if not model:
            raise RoutingError(422, "model_required", "Manual routing needs a model. Choose one in Settings.", listing)
        matches = [c for c in cands if c.model == model]
        if not matches:
            raise RoutingError(
                422, "model_not_eligible",
                f"Model '{model}' is not a configured, available model, so it was not used. Pick another in Settings.", listing,
            )
        if len(matches) > 1:
            raise RoutingError(422, "model_ambiguous", f"Model '{model}' is offered by several providers; also set 'provider'.", listing)
        return RoutePlan(task, "manual", [matches[0]], f"manual: you selected {model}")

    def _rank(self, policy: Policy, task: TaskClassification, cands: list[Candidate]) -> tuple[list[Candidate], str]:
        default = self._global_default(cands)
        assert default is not None
        index = {c.key: i for i, c in enumerate(cands)}
        if len(cands) == 1:
            return list(cands), f"{policy}: only one eligible model ({default.model})"

        def default_first(items: list[Candidate]) -> list[Candidate]:
            return [c for c in items if c.key == default.key] + [c for c in items if c.key != default.key]

        if policy in ("quality", "balanced"):
            wanted = self._settings.router_task_models.get(task.task_type) if task.is_known else None  # type: ignore[call-overload]
            mapped = next((c for c in cands if wanted and c.model == wanted), None)
            if mapped:
                head, reason = [mapped], f"{policy}: task '{task.task_type}' is mapped to {mapped.model}"
            elif wanted:
                head, reason = [], f"{policy}: model mapped to task '{task.task_type}' ({wanted}) is unavailable; using default {default.model}"
            else:
                label = task.task_type if task.is_known else "unknown"
                head, reason = [], f"{policy}: no model mapped to task '{label}'; using default {default.model}"
            tail = [c for c in cands if c not in head]
            if policy == "balanced":
                inf = float("inf")
                tail.sort(key=lambda c: (c.key != default.key, c.cost if c.cost is not None else inf,
                                         c.latency_ms if c.latency_ms is not None else inf, index[c.key]))
            else:
                tail = default_first(tail)
            return head + tail, reason

        if policy == "cost":
            missing = [c.model for c in cands if c.cost is None]
            if missing:
                return default_first(cands), f"cost: price not configured for {', '.join(missing)} (see MODEL_PROFILES); using default {default.model}"
            ordered = sorted(cands, key=lambda c: (c.cost, index[c.key]))
            return ordered, f"cost: {ordered[0].model} has the lowest configured price"

        # latency
        missing = [c.model for c in cands if c.latency_ms is None]
        if missing:
            return default_first(cands), f"latency: not enough measurements for {', '.join(missing)}; using default {default.model}"
        ordered = sorted(cands, key=lambda c: (c.latency_ms, index[c.key]))
        return ordered, f"latency: {ordered[0].model} has the lowest measured median latency"

    # ---- execution ------------------------------------------------------------------------------

    def _next_fallback(
        self, plan: RoutePlan, failed: Candidate, exc: ProviderError, remaining: list[Candidate], used: int, started: float
    ) -> Candidate | None:
        s = self._settings
        if plan.policy == "manual" or s.router_fallback == "off" or used >= s.router_max_fallbacks:
            return None
        if exc.code not in FALLBACK_CODES:
            return None
        if time.monotonic() - started + s.provider_timeout_seconds > s.router_total_timeout_seconds:
            return None  # no time budget left for another attempt
        for cand in remaining:
            if not self.tracker.is_available(cand.key):
                continue
            if s.router_fallback == "cheaper_or_equal":
                # Never silently move to a pricier model: both prices must be known and the new one no higher.
                if failed.cost is None or cand.cost is None or cand.cost > failed.cost:
                    continue
            return cand
        return None

    async def execute(
        self, plan: RoutePlan, messages: list[ChatMessage], request_id: str, system: str | None = None
    ) -> RouteOutcome:
        started = time.monotonic()
        remaining = list(plan.ordered)
        current = remaining.pop(0)
        attempts: list[RoutingAttempt] = []
        fallback_reason: str | None = None
        used = 0
        extra = {"system": system} if system else {}  # only passed when set, so existing adapters are unaffected
        while True:
            provider = self._registry.get(current.provider)
            assert provider is not None
            t0 = time.perf_counter()
            try:
                result = await provider.generate(messages, current.model, **extra)
            except ProviderError as exc:
                self.tracker.record_failure(current.key, exc.code)
                attempts.append(RoutingAttempt(provider=current.provider, model=current.model, outcome=exc.code))
                nxt = self._next_fallback(plan, current, exc, remaining, used, started)
                logger.info(
                    "route attempt failed request_id=%s provider=%s model=%s code=%s fallback_to=%s",
                    request_id, current.provider, current.model, exc.code, nxt.model if nxt else None,
                )
                if nxt is None:
                    raise RoutedFailure(exc, attempts) from None
                fallback_reason = f"{current.provider}/{current.model} failed ({exc.code})"
                used += 1
                remaining.remove(nxt)
                current = nxt
                continue
            self.tracker.record_success(current.key)
            self.stats.record(current.key, (time.perf_counter() - t0) * 1000)
            attempts.append(RoutingAttempt(provider=current.provider, model=current.model, outcome="success"))
            return RouteOutcome(result, current, plan, attempts, fallback_reason)
