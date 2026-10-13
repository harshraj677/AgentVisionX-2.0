"""Tests the Gemini adapter against a mocked SDK client (no network, no API key needed)."""
import asyncio
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.core.config import Settings
from app.providers.base import ProviderError
from app.providers.gemini import GeminiProvider
from app.schemas.chat import ChatMessage

MESSAGES = [ChatMessage(role="user", content="hi"), ChatMessage(role="assistant", content="yo"), ChatMessage(role="user", content="again")]


def make_provider(generate):
    provider = GeminiProvider(Settings(_env_file=None, gemini_api_key="fake-key", gemini_model="default-model"))
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    return provider


def run(coro):
    return asyncio.run(coro)


def test_success_maps_text_roles_and_usage():
    seen = {}

    async def generate(model, contents):
        seen["model"], seen["roles"] = model, [c.role for c in contents]
        usage = SimpleNamespace(prompt_token_count=7, candidates_token_count=4, total_token_count=20)
        return SimpleNamespace(text="answer", usage_metadata=usage)

    result = run(make_provider(generate).generate(MESSAGES, None))
    assert seen == {"model": "default-model", "roles": ["user", "model", "user"]}
    assert result.text == "answer"
    # total (20) is reported by the provider as-is, even though 7 + 4 != 20 (thinking tokens).
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.total_tokens) == (7, 4, 20)
    assert result.usage.source == "provider"


def test_missing_usage_is_unavailable():
    async def generate(model, contents):
        return SimpleNamespace(text="answer", usage_metadata=None)

    assert run(make_provider(generate).generate(MESSAGES, "custom")).usage.source == "unavailable"


@pytest.mark.parametrize(
    "api_code, expected_status, expected_code",
    [(429, 429, "rate_limited"), (404, 502, "model_not_found"), (403, 502, "provider_auth_error"), (500, 502, "provider_error")],
)
def test_api_errors_are_translated(api_code, expected_status, expected_code):
    async def generate(model, contents):
        raise errors.ClientError(api_code, {"error": {"message": "boom", "status": "X"}})

    with pytest.raises(ProviderError) as info:
        run(make_provider(generate).generate(MESSAGES, None))
    assert (info.value.status_code, info.value.code) == (expected_status, expected_code)


def test_empty_response_is_an_error():
    async def generate(model, contents):
        return SimpleNamespace(text=None, usage_metadata=None)

    with pytest.raises(ProviderError) as info:
        run(make_provider(generate).generate(MESSAGES, None))
    assert info.value.code == "empty_response"
