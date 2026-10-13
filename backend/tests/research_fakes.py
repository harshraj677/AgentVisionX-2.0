"""Test doubles for research: a fake search client and a mock Tavily HTTP transport. No network, no real keys."""
import json

import httpx

from app.core.config import Settings
from app.research.tavily import ResearchError, SearchResponse, SearchResult, TavilyClient

TAVILY_KEY = "tvly-test-key-DO-NOT-LEAK-12345"


def result(n: int, content: str | None = None, url: str | None = None, title: str | None = None, published=None) -> SearchResult:
    return SearchResult(
        title=title if title is not None else f"Source title {n}",
        url=url or f"https://example{n}.org/article-{n}",
        content=content if content is not None else f"Evidence sentence number {n} about the topic.",
        published_date=published,
        score=0.9,
    )


class FakeSearch:
    def __init__(self, results=None, error: ResearchError | None = None, configured: bool = True):
        self.results = results if results is not None else [result(1), result(2)]
        self.error = error
        self.configured = configured
        self.calls: list[dict] = []

    def is_configured(self) -> bool:
        return self.configured

    async def search(self, query, *, max_results, depth, time_range):
        self.calls.append({"query": query, "max_results": max_results, "depth": depth, "time_range": time_range})
        if self.error:
            raise self.error
        return SearchResponse(list(self.results), "2026-10-12T12:00:00+00:00", 42)


class TavilyMock:
    """Records requests and answers with a scripted status/body, or raises a transport exception."""

    def __init__(self, status=200, body=None, raise_exc: Exception | None = None, raw: str | None = None):
        self.status, self.body, self.raise_exc, self.raw = status, body, raise_exc, raw
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc:
            raise self.raise_exc
        if self.raw is not None:
            return httpx.Response(self.status, text=self.raw)
        return httpx.Response(self.status, json=self.body if self.body is not None else {"query": "q", "results": []})

    def client(self, settings: Settings) -> TavilyClient:
        return TavilyClient(settings, http=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))

    def payload(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


def tavily_body(*items: dict) -> dict:
    return {"query": "q", "answer": None, "images": [], "results": list(items), "response_time": 0.5}
