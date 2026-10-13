from app.core.config import Settings
from app.providers.base import LLMProvider, ProviderError, ProviderResult
from app.schemas.chat import ChatMessage


class OpenAIProvider(LLMProvider):
    """Placeholder adapter. It never fabricates output: it only reports why it cannot answer."""

    name = "openai"
    supports_generation = False  # adapter planned; never routed to

    def __init__(self, settings: Settings):
        self._settings = settings

    def is_configured(self) -> bool:
        key = self._settings.openai_api_key
        return key is not None and bool(key.get_secret_value())

    def list_models(self) -> list[str]:
        return []  # nothing can be generated yet, so nothing is offered

    def default_model(self) -> str | None:
        return None

    async def generate(self, messages: list[ChatMessage], model: str | None, system: str | None = None) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(
                503, "provider_not_configured", "OpenAI is not configured. Set OPENAI_API_KEY in backend/.env."
            )
        raise ProviderError(501, "provider_not_implemented", "The OpenAI adapter is not implemented yet.")
