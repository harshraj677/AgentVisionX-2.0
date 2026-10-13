"""Embedding provider abstraction + the Gemini implementation (google-genai `embed_content`).

Vectors are L2-normalised (Google requires this for gemini-embedding-001 at reduced dimensions; harmless otherwise) and
their length is checked against EMBEDDING_DIMENSIONS. Text is only ever sent here AFTER the ingestion privacy policy
was satisfied (documents) or the input privacy gate passed (questions).
"""
import asyncio
import logging
import math
from typing import Literal, Protocol

from app.core.config import Settings

logger = logging.getLogger("agentvision.documents")

Purpose = Literal["document", "query"]


class EmbeddingError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


class EmbeddingProvider(Protocol):
    model: str
    dimensions: int

    def is_configured(self) -> bool: ...

    async def embed(self, texts: list[str], purpose: Purpose) -> list[list[float]]: ...


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0 or not math.isfinite(norm):
        raise EmbeddingError(502, "embedding_invalid", "The embedding service returned an invalid vector.")
    return [x / norm for x in vector]


class GeminiEmbeddingProvider:
    def __init__(self, settings: Settings):
        self._settings = settings
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self._client = None

    def is_configured(self) -> bool:
        key = self._settings.gemini_api_key
        return key is not None and bool(key.get_secret_value())

    def _get_client(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self._settings.gemini_api_key.get_secret_value(),  # type: ignore[union-attr]
                http_options=types.HttpOptions(timeout=int(self._settings.embedding_timeout_seconds * 1000)),
            )
        return self._client

    def _request(self, texts: list[str], purpose: Purpose):
        from google.genai import types

        if self.model.startswith("gemini-embedding-2"):
            # This model takes the task in the text instead of task_type (per Google's embeddings docs).
            prefix = "task: search result | query: " if purpose == "query" else "title: none | text: "
            return [prefix + t for t in texts], types.EmbedContentConfig(output_dimensionality=self.dimensions)
        task = "RETRIEVAL_QUERY" if purpose == "query" else "RETRIEVAL_DOCUMENT"
        return texts, types.EmbedContentConfig(output_dimensionality=self.dimensions, task_type=task)

    async def embed(self, texts: list[str], purpose: Purpose) -> list[list[float]]:
        if not self.is_configured():
            raise EmbeddingError(503, "embedding_not_configured", "Embeddings are not configured. Set GEMINI_API_KEY in backend/.env.")
        from google.genai import errors

        out: list[list[float]] = []
        size = self._settings.embedding_batch_size
        for start in range(0, len(texts), size):
            contents, config = self._request(texts[start:start + size], purpose)
            try:
                resp = await asyncio.wait_for(
                    self._get_client().aio.models.embed_content(model=self.model, contents=contents, config=config),
                    timeout=self._settings.embedding_timeout_seconds + 5,
                )
            except (asyncio.TimeoutError, TimeoutError):
                raise EmbeddingError(504, "embedding_timeout", "The embedding service timed out.") from None
            except errors.APIError as exc:
                logger.warning("embedding api error code=%s", exc.code)
                if exc.code == 429:
                    raise EmbeddingError(429, "embedding_rate_limited", "The embedding service rate limit was reached. Try again later.") from None
                if exc.code in (401, 403):
                    raise EmbeddingError(502, "embedding_auth_error", "The embedding service rejected the API key.") from None
                if exc.code == 404:
                    raise EmbeddingError(502, "embedding_model_not_found", f"Embedding model '{self.model}' was not found. Check EMBEDDING_MODEL.") from None
                raise EmbeddingError(502, "embedding_failed", "The embedding service returned an error.") from None
            except Exception as exc:
                logger.warning("embedding call failed: %s", type(exc).__name__)
                raise EmbeddingError(502, "embedding_failed", "Could not reach the embedding service.") from None
            embeddings = getattr(resp, "embeddings", None) or []
            if len(embeddings) != len(contents):
                raise EmbeddingError(502, "embedding_invalid", "The embedding service returned the wrong number of vectors.")
            for e in embeddings:
                values = list(e.values or [])
                if len(values) != self.dimensions:
                    raise EmbeddingError(
                        502, "embedding_dimension_mismatch",
                        f"Expected {self.dimensions}-dimensional embeddings, got {len(values)}. Check EMBEDDING_DIMENSIONS.",
                    )
                out.append(normalize(values))
        return out
