from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.schemas.chat import ChatMessage, Usage


class ProviderError(Exception):
    """A provider failure, already translated into an HTTP-friendly shape."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass
class ProviderResult:
    text: str
    model: str
    usage: Usage


class LLMProvider(ABC):
    """Every AI provider implements this interface, so the chat workflow never changes."""

    name: str
    # False for placeholder adapters that cannot generate yet; the router never selects them.
    supports_generation: bool = True

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def list_models(self) -> list[str]:
        """Models this deployment has CONFIGURED for the provider (no network call, no guessing)."""

    @abstractmethod
    def default_model(self) -> str | None: ...

    @abstractmethod
    async def generate(
        self, messages: list[ChatMessage], model: str | None, system: str | None = None
    ) -> ProviderResult:
        """`system` carries application instructions separately from user content. Callers only pass it when set."""
