import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.providers.base import LLMProvider, ProviderError, ProviderResult
from app.schemas.chat import Usage


class FakeProvider(LLMProvider):
    """Test double. Lives only in tests; the real app never uses it."""

    name = "gemini"

    def __init__(self, result: ProviderResult | None = None, error: ProviderError | None = None):
        self.result, self.error = result, error
        self.calls = 0  # how many times the (fake) external provider was reached

    def is_configured(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["test-model"]

    def default_model(self) -> str | None:
        return "test-model"

    async def generate(self, messages, model, system=None):
        self.calls += 1
        self.last_messages = messages  # what the provider actually received
        if self.error:
            raise self.error
        return self.result


OK_RESULT = ProviderResult(
    text="Hello!", model="test-model", usage=Usage(input_tokens=3, output_tokens=2, total_tokens=5, source="provider")
)


@pytest.fixture
def make_client():
    def _make(provider: LLMProvider | None = None, **overrides) -> TestClient:
        settings = Settings(_env_file=None, gemini_api_key=None, openai_api_key=None, **overrides)
        app = create_app(settings)
        if provider is not None:
            app.state.registry._providers["gemini"] = provider
        return TestClient(app, raise_server_exceptions=False)

    return _make
