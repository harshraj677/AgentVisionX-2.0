import asyncio
import logging
import time

from app.core.config import Settings
from app.core.errors import AppError
from app.research.grounding import SYSTEM_INSTRUCTION, build_user_message, check_citations
from app.research.sources import SelectionReport, select_sources
from app.research.tavily import ResearchError, SearchClient
from app.router.classifier import TaskClassification
from app.router.router import ModelRouter, RoutedFailure, RoutingError
from app.schemas.chat import ChatMessage
from app.schemas.research import (
    CitationInfo,
    ResearchMetadata,
    ResearchRequest,
    ResearchResponse,
    ResearchSource,
    SearchInfo,
)
from app.services.privacy_service import PrivacyService
from app.services.response_privacy_service import ResponsePrivacyService

logger = logging.getLogger("agentvision.research")

RESEARCH_TASK = TaskClassification("research", "high", ("research_endpoint",))


class ResearchService:
    """question -> input privacy gate -> route plan -> ONE Tavily search -> validate/bound sources ->
    ONE routed generation (with bounded fallback) -> response privacy scan -> citation checks -> respond.

    There is no loop and no tool use: nothing in a source can trigger another search or any other action.
    """

    def __init__(
        self,
        settings: Settings,
        privacy: PrivacyService,
        router: ModelRouter,
        response_privacy: ResponsePrivacyService,
        search: SearchClient,
    ):
        self._settings = settings
        self._privacy = privacy
        self._router = router
        self._response_privacy = response_privacy
        self.search = search

    def _validate(self, req: ResearchRequest) -> str:
        question = req.question.strip()
        if not question:
            raise AppError(422, "empty_message", "The research question is empty.")
        if len(question) > self._settings.research_max_query_chars:
            raise AppError(
                422, "query_too_long",
                f"Research questions are limited to {self._settings.research_max_query_chars} characters.",
            )
        if req.model and req.routing_policy not in (None, "manual"):
            raise AppError(422, "conflicting_routing", "'model' can only be combined with routing_policy 'manual'.")
        return question

    def _search_info(self, report: SelectionReport | None, depth: str, time_range: str | None, max_sources: int, ms: int) -> SearchInfo:
        r = report or SelectionReport(sources=[], returned=0)
        return SearchInfo(
            depth=depth, time_range=time_range, max_sources=max_sources, results_returned=r.returned,
            sources_used=len(r.sources), rejected_invalid_url=r.rejected_invalid_url,
            rejected_duplicate=r.rejected_duplicate, rejected_empty=r.rejected_empty, truncated=r.truncated,
            flagged_sources=sum(1 for s in r.sources if s.flags), search_latency_ms=ms,
        )

    async def research(self, req: ResearchRequest, request_id: str) -> ResearchResponse:
        question = self._validate(req)

        # 1. Input privacy: exactly the same gate as /api/chat. Nothing leaves the backend unless this passes.
        privacy = await self._privacy.enforce([ChatMessage(role="user", content=question)], req.privacy_ack, request_id)

        # 2. Configuration and routing are checked BEFORE searching, so a bad request never spends a search call.
        if not self.search.is_configured():
            raise AppError(
                503, "research_provider_not_configured",
                "Research is not configured on the server. Set TAVILY_API_KEY in backend/.env.",
            )
        policy = "manual" if (req.model and req.routing_policy is None) else req.routing_policy
        try:
            plan = self._router.plan(RESEARCH_TASK, policy, req.provider, req.model, sensitive=privacy.decision == "warn")
        except RoutingError as exc:
            raise AppError(exc.status_code, exc.code, exc.message, exc.details) from None

        # 3. One web search with only the approved question.
        depth = req.search_depth or self._settings.research_default_depth
        max_sources = min(req.max_sources or self._settings.research_max_results, self._settings.research_max_results)
        try:
            found = await self.search.search(question, max_results=max_sources, depth=depth, time_range=req.time_range)
        except ResearchError as exc:
            logger.info("research search failed request_id=%s code=%s", request_id, exc.code)
            raise AppError(exc.status_code, exc.code, exc.message) from None

        # 4. Validate, de-duplicate, neutralise and bound the evidence.
        report = select_sources(
            found.results, retrieved_at=found.retrieved_at, max_sources=max_sources,
            per_source_chars=self._settings.research_max_source_chars,
            total_chars=self._settings.research_max_total_source_chars,
        )
        search_info = self._search_info(report, depth, req.time_range, max_sources, found.latency_ms)
        logger.info(
            "research search request_id=%s query_chars=%d depth=%s returned=%d used=%d invalid=%d dup=%d empty=%d flagged=%d ms=%d",
            request_id, len(question), depth, report.returned, len(report.sources), report.rejected_invalid_url,
            report.rejected_duplicate, report.rejected_empty, search_info.flagged_sources, found.latency_ms,
        )
        if not report.sources:
            # No usable evidence: do NOT ask a model to answer from nothing.
            raise AppError(
                404, "research_no_sources",
                "The web search returned no usable sources, so no answer was generated. Try rephrasing the question.",
                {"search": search_info.model_dump()},
            )

        # 5. One routed generation. Instructions travel separately (system), evidence is marked untrusted.
        messages = [ChatMessage(role="user", content=build_user_message(question, report.sources))]
        start = time.perf_counter()
        try:
            outcome = await self._router.execute(plan, messages, request_id, system=SYSTEM_INSTRUCTION)
        except RoutedFailure as failure:
            raise AppError(
                failure.error.status_code, failure.error.code, failure.error.message,
                {"search": search_info.model_dump(),
                 "routing": {"task_type": "research", "routing_policy": plan.policy,
                             "attempts": [a.model_dump() for a in failure.attempts]}},
            ) from None
        latency_ms = int((time.perf_counter() - start) * 1000)
        info = outcome.info()

        # 6. Response privacy on the generated answer (may redact or withhold, as in /api/chat).
        try:
            answer, response_privacy = await self._response_privacy.process(outcome.result.text, request_id)
        except AppError as exc:
            exc.details = {
                **(exc.details if isinstance(exc.details, dict) else {}),
                "search": search_info.model_dump(),
                "routing": {"task_type": "research", "routing_policy": plan.policy,
                            "selected_provider": info.selected_provider, "selected_model": info.selected_model,
                            "attempts": [a.model_dump() for a in info.attempts]},
            }
            raise

        # Snippets and titles shown in the app are third-party text: same response policy, per fragment.
        shown = await asyncio.to_thread(
            lambda: [(self._response_privacy.scan_fragment(s.title), self._response_privacy.scan_fragment(s.snippet)) for s in report.sources]
        )

        # 7. Citations may only point at returned sources; links that are not sources are removed.
        cites = check_citations(answer, report.sources)
        logger.info(
            "research done request_id=%s status=%s cited=%d invalid_removed=%d links_removed=%d response_privacy=%s",
            request_id, cites.evidence_status, len(cites.cited_source_ids), cites.invalid_citations_removed,
            cites.unverified_links_removed, response_privacy.status,
        )
        return ResearchResponse(
            answer=cites.text,
            evidence_status=cites.evidence_status,  # type: ignore[arg-type]
            sources=[
                ResearchSource(
                    id=s.id, title=title or s.domain, url=s.url, domain=s.domain, snippet=snippet,
                    published_date=s.published_date, retrieved_at=s.retrieved_at, flags=s.flags,
                    cited=s.id in cites.cited_source_ids,
                )
                for s, (title, snippet) in zip(report.sources, shown)
            ],
            citations=CitationInfo(
                cited_source_ids=cites.cited_source_ids,
                invalid_citations_removed=cites.invalid_citations_removed,
                unverified_links_removed=cites.unverified_links_removed,
            ),
            metadata=ResearchMetadata(
                request_id=request_id,
                provider=info.selected_provider,
                model=outcome.result.model,
                latency_ms=latency_ms,
                usage=outcome.result.usage,
                privacy=privacy,
                prompt_source=req.prompt_source,
                routing=info,
                response_privacy=response_privacy,
                search=search_info,
            ),
        )
