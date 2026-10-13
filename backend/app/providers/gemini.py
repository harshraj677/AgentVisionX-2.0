import asyncio
import logging

from google import genai
from google.genai import errors, types

from app.core.config import Settings
from app.providers.base import LLMProvider, ProviderError, ProviderResult
from app.schemas.chat import ChatMessage, Usage

logger = logging.getLogger("agentvision.gemini")


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: genai.Client | None = None

    def is_configured(self) -> bool:
        key = self._settings.gemini_api_key
        return key is not None and bool(key.get_secret_value())

    def list_models(self) -> list[str]:
        return self._settings.gemini_model_list if self.is_configured() else []

    def default_model(self) -> str | None:
        return self._settings.gemini_model

    def _get_client(self) -> genai.Client:
        if self._client is None:
            timeout_ms = int(self._settings.provider_timeout_seconds * 1000)
            self._client = genai.Client(
                api_key=self._settings.gemini_api_key.get_secret_value(),  # type: ignore[union-attr]
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
        return self._client

    async def generate(self, messages: list[ChatMessage], model: str | None, system: str | None = None) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(
                503, "provider_not_configured", "Gemini is not configured. Set GEMINI_API_KEY in backend/.env."
            )
        model_name = model or self._settings.gemini_model
        contents = [
            types.Content(role="user" if m.role == "user" else "model", parts=[types.Part(text=m.content)])
            for m in messages
        ]
        extra = {"config": types.GenerateContentConfig(system_instruction=system)} if system else {}
        try:
            response = await asyncio.wait_for(
                self._get_client().aio.models.generate_content(model=model_name, contents=contents, **extra),
                timeout=self._settings.provider_timeout_seconds + 5,
            )
        except (asyncio.TimeoutError, TimeoutError):
            raise ProviderError(504, "provider_timeout", "Gemini did not respond in time.") from None
        except errors.APIError as exc:
            raise self._translate(exc, model_name) from None
        except Exception as exc:  # network failures, SDK-internal errors, etc.
            logger.warning("Gemini call failed: %s", type(exc).__name__)
            raise ProviderError(502, "provider_error", "Could not reach Gemini.") from None

        try:
            text = response.text
        except Exception:
            text = None
        if not text:
            raise ProviderError(502, "empty_response", "Gemini returned no text (it may have been blocked).")

        meta = response.usage_metadata
        if meta is not None and meta.total_token_count is not None:
            usage = Usage(
                input_tokens=meta.prompt_token_count,
                output_tokens=meta.candidates_token_count,
                total_tokens=meta.total_token_count,
                source="provider",
            )
        else:
            usage = Usage()
        return ProviderResult(text=text, model=model_name, usage=usage)

    @staticmethod
    def _translate(exc: errors.APIError, model_name: str) -> ProviderError:
        code = exc.code or 0
        msg = (exc.message or "").lower()
        logger.warning("Gemini API error: code=%s status=%s", exc.code, exc.status)
        if code == 429:
            return ProviderError(429, "rate_limited", "Gemini rate limit or quota reached. Try again later.")
        if code in (401, 403) or "api key" in msg:
            return ProviderError(502, "provider_auth_error", "Gemini rejected the API key. Check GEMINI_API_KEY.")
        if code == 404:
            return ProviderError(
                502, "model_not_found", f"Model '{model_name}' was not found. Update GEMINI_MODEL in backend/.env."
            )
        if code == 400:
            return ProviderError(502, "provider_bad_request", "Gemini rejected the request.")
        return ProviderError(502, "provider_error", f"Gemini returned an error (HTTP {code or 'unknown'}).")
