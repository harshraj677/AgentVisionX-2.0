"""Test doubles for routing tests. Nothing here touches the network or needs credentials."""
from app.core.config import Settings
from app.providers.base import LLMProvider, ProviderError, ProviderResult
from app.providers.registry import ProviderRegistry
from app.router.availability import AvailabilityTracker, LatencyStats
from app.router.router import ModelRouter
from app.schemas.chat import Usage


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ScriptedProvider(LLMProvider):
    """A provider whose behaviour per model is scripted: queue ProviderError / ProviderResult objects."""

    def __init__(self, name="gemini", models=("flash",), default=None, configured=True, generates=True, script=None):
        self.name = name
        self.supports_generation = generates
        self._models = list(models)
        self._default = default or self._models[0]
        self._configured = configured
        self.script: dict[str, list] = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[str] = []  # model of every generate() call
        self.seen_messages: list = []
        self.seen_system: list = []

    def is_configured(self) -> bool:
        return self._configured

    def list_models(self) -> list[str]:
        return self._models if self._configured and self.supports_generation else []

    def default_model(self) -> str | None:
        return self._default

    async def generate(self, messages, model, system=None):
        self.calls.append(model)
        self.seen_messages.append(messages)
        self.seen_system.append(system)
        queue = self.script.get(model)
        outcome = queue.pop(0) if queue else None
        if isinstance(outcome, ProviderError):
            raise outcome
        if isinstance(outcome, ProviderResult):
            return outcome
        return ProviderResult(
            text=f"reply from {self.name}/{model}", model=model,
            usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3, source="provider"),
        )


def timeout() -> ProviderError:
    return ProviderError(504, "provider_timeout", "timed out")


def down() -> ProviderError:
    return ProviderError(502, "provider_error", "provider failed")


def bad_request() -> ProviderError:
    return ProviderError(502, "provider_bad_request", "rejected")


PRICES = {
    "flash": {"input_cost_per_mtok": 1.0, "output_cost_per_mtok": 2.0},  # total 3
    "pro": {"input_cost_per_mtok": 5.0, "output_cost_per_mtok": 10.0},  # total 15
    "lite": {"input_cost_per_mtok": 0.5, "output_cost_per_mtok": 0.5},  # total 1
}


def make_settings(**kw) -> Settings:
    kw.setdefault("gemini_api_key", None)
    kw.setdefault("openai_api_key", None)
    return Settings(_env_file=None, **kw)


def make_router(providers: dict[str, LLMProvider], clock=None, **settings_kw) -> ModelRouter:
    settings = make_settings(**settings_kw)
    registry = ProviderRegistry(settings)
    registry._providers = dict(providers)
    clk = clock or Clock()
    tracker = AvailabilityTracker(settings.router_failure_threshold, settings.router_cooldown_seconds, clk)
    return ModelRouter(settings, registry, tracker, LatencyStats())


def reply(text: str, model: str = "flash") -> ProviderResult:
    """A scripted provider reply with the given text (the thing the response scanner must inspect)."""
    return ProviderResult(text=text, model=model, usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3, source="provider"))
