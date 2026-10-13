from app.core.config import Settings
from app.providers.base import LLMProvider
from app.providers.gemini import GeminiProvider
from app.providers.openai_provider import OpenAIProvider


class ProviderRegistry:
    def __init__(self, settings: Settings):
        self._providers: dict[str, LLMProvider] = {
            "gemini": GeminiProvider(settings),
            "openai": OpenAIProvider(settings),
        }

    def get(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return list(self._providers)

    def configured_status(self) -> dict[str, bool]:
        return {name: p.is_configured() for name, p in self._providers.items()}
