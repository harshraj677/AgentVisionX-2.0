"""Tavily web-search adapter (official REST API: POST https://api.tavily.com/search, Bearer auth).

Sends ONLY the approved search query plus fixed search options. Never sends chat history, documents or other data.
Returns exactly what Tavily returned (normalized), never anything synthesized. The HTTP client is injectable so tests
run against httpx.MockTransport with no network access.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx

from app.core.config import Settings

logger = logging.getLogger("agentvision.research")

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class ResearchError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


@dataclass(frozen=True)
class SearchResult:
    """One raw result as Tavily returned it (still untrusted: validated later in sources.py)."""

    title: str
    url: str
    content: str
    published_date: str | None
    score: float | None


@dataclass(frozen=True)
class SearchResponse:
    results: list[SearchResult]
    retrieved_at: str  # when Tavily answered us (UTC, ISO 8601)
    latency_ms: int


class SearchClient(Protocol):
    def is_configured(self) -> bool: ...

    async def search(self, query: str, *, max_results: int, depth: str, time_range: str | None) -> SearchResponse: ...


class TavilyClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._settings = settings
        self._http = http  # injected in tests; created lazily otherwise

    def is_configured(self) -> bool:
        key = self._settings.tavily_api_key
        return key is not None and bool(key.get_secret_value())

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._settings.tavily_timeout_seconds)
        return self._http

    async def search(self, query: str, *, max_results: int, depth: str, time_range: str | None) -> SearchResponse:
        if not self.is_configured():
            raise ResearchError(
                503, "research_provider_not_configured",
                "Research is not configured on the server. Set TAVILY_API_KEY in backend/.env.",
            )
        payload: dict = {
            "query": query,
            "search_depth": depth,
            "max_results": max_results,
            "topic": "general",
            "include_answer": False,  # we never use Tavily's own generated answer
            "include_raw_content": False,
            "include_images": False,
            "include_published_date": True,
        }
        if time_range:
            payload["time_range"] = time_range
        headers = {"Authorization": f"Bearer {self._settings.tavily_api_key.get_secret_value()}"}  # type: ignore[union-attr]

        start = time.perf_counter()
        try:
            resp = await self._client().post(
                TAVILY_SEARCH_URL, json=payload, headers=headers, timeout=self._settings.tavily_timeout_seconds
            )
        except httpx.TimeoutException:
            raise ResearchError(504, "research_timeout", "The web search timed out. Please try again.") from None
        except httpx.HTTPError as exc:
            logger.warning("tavily transport error: %s", type(exc).__name__)
            raise ResearchError(502, "research_provider_error", "Could not reach the web search service.") from None
        latency_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code != 200:
            raise self._http_error(resp.status_code)
        try:
            data = resp.json()
            raw_results = data["results"]
            if not isinstance(raw_results, list):
                raise TypeError("results is not a list")
        except Exception:
            raise ResearchError(502, "research_malformed_response", "The web search service returned an unexpected response.") from None

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue  # skip malformed entries rather than guessing
            title, url, content = item.get("title"), item.get("url"), item.get("content")
            if not isinstance(url, str) or not isinstance(content, str):
                continue
            published = item.get("published_date")
            score = item.get("score")
            results.append(
                SearchResult(
                    title=title if isinstance(title, str) else "",
                    url=url,
                    content=content,
                    published_date=published if isinstance(published, str) and published.strip() else None,
                    score=float(score) if isinstance(score, (int, float)) else None,
                )
            )
        return SearchResponse(results, datetime.now(timezone.utc).isoformat(timespec="seconds"), latency_ms)

    @staticmethod
    def _http_error(status: int) -> ResearchError:
        logger.warning("tavily http error status=%s", status)
        if status == 401:
            return ResearchError(502, "research_auth_error", "The web search service rejected the API key. Check TAVILY_API_KEY.")
        if status == 429:
            return ResearchError(429, "research_rate_limited", "The web search rate limit was reached. Try again shortly.")
        if status in (432, 433):
            return ResearchError(429, "research_quota_exceeded", "The web search plan or usage limit was reached.")
        if status in (400, 422):
            return ResearchError(502, "research_bad_request", "The web search service rejected the request.")
        return ResearchError(502, "research_provider_error", f"The web search service returned an error (HTTP {status}).")
