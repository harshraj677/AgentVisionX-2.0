import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.errors import AppError
from app.documents.chunking import chunk_pages
from app.documents.embeddings import EmbeddingError, EmbeddingProvider
from app.documents.files import DocumentError, Page, detect_type, extract, sanitize_filename
from app.documents.grounding import SYSTEM_INSTRUCTION, build_user_message, check_citations, dedupe, make_passages
from app.documents.store import DocumentRecord, DocumentStore, StoreUnavailable
from app.privacy.policy import PolicyEngine
from app.router.classifier import TaskClassification
from app.router.router import ModelRouter, RoutedFailure, RoutingError
from app.schemas.chat import ChatMessage
from app.schemas.documents import (
    AskMetadata,
    AskRequest,
    AskResponse,
    DocumentCitation,
    DocumentOut,
    DocumentPrivacy,
    RetrievalInfo,
)
from app.services.privacy_service import PrivacyService
from app.services.response_privacy_service import ResponsePrivacyService

logger = logging.getLogger("agentvision.documents")

DOCUMENTS_TASK = TaskClassification("documents", "high", ("documents_endpoint",))
EXCERPT_CHARS = 300


def to_out(doc: DocumentRecord) -> DocumentOut:
    return DocumentOut(
        id=doc.id, filename=doc.filename, file_type=doc.file_type, size_bytes=doc.size_bytes,  # type: ignore[arg-type]
        page_count=doc.page_count, char_count=doc.char_count, status=doc.status, error_code=doc.error_code,  # type: ignore[arg-type]
        privacy=DocumentPrivacy(
            required_action=doc.privacy_required_action, decision=doc.privacy_decision, categories=doc.privacy_categories,  # type: ignore[arg-type]
        ),
        embedding_model=doc.embedding_model, chunk_count=doc.chunk_count,
        uploaded_at=doc.uploaded_at, processed_at=doc.processed_at,
    )


class DocumentService:
    """Upload -> validate -> extract -> PRIVACY GATE -> (decision) -> chunk -> embed -> store.
    Ask -> input privacy -> authorise documents -> embed question -> owner-scoped vector search -> bounded, untrusted
    evidence -> routed generation -> response privacy -> citation checks.

    Document text never reaches the embedding provider or an LLM unless the ingestion privacy policy was satisfied
    (nothing found, explicit confirmation of warn-level findings, or redaction). There is no bypass."""

    def __init__(
        self,
        settings: Settings,
        privacy: PrivacyService,
        router: ModelRouter,
        response_privacy: ResponsePrivacyService,
        store: DocumentStore,
        embedder: EmbeddingProvider,
    ):
        self._s = settings
        self._privacy = privacy
        self._router = router
        self._response_privacy = response_privacy
        self.store = store
        self.embedder = embedder
        self._dims_ok = False

    # ---- guards ---------------------------------------------------------------------------------------------

    def _require_database(self) -> None:
        if not self.store.is_configured():
            raise AppError(503, "documents_database_not_configured", "Documents need PostgreSQL with pgvector. Set DATABASE_URL in backend/.env.")

    def _require_embeddings(self) -> None:
        if not self.embedder.is_configured():
            raise AppError(503, "embedding_not_configured", "Document indexing needs an embedding provider. Set GEMINI_API_KEY in backend/.env.")

    async def _db(self, coro):
        try:
            return await coro
        except StoreUnavailable:
            raise AppError(503, "documents_database_unavailable", "The document database is unavailable. Try again later.") from None

    async def _check_dimensions(self) -> None:
        if self._dims_ok:
            return
        dims = await self._db(self.store.embedding_dimensions())
        if dims is None:
            raise AppError(503, "documents_database_not_migrated", "The document tables are missing. Run: python -m app.db.migrate")
        if dims != self._s.embedding_dimensions or self.embedder.dimensions != dims:
            raise AppError(
                503, "embedding_dimension_mismatch",
                f"EMBEDDING_DIMENSIONS ({self._s.embedding_dimensions}) does not match the database vector size ({dims}).",
            )
        self._dims_ok = True

    async def _get_owned(self, owner: str, document_id: str) -> DocumentRecord:
        doc = await self._db(self.store.get_document(owner, document_id))
        if doc is None:  # also the answer for documents owned by another scope: existence is not revealed
            raise AppError(404, "document_not_found", "Document not found.")
        return doc

    async def _event(self, owner: str, doc_id: str, event: str, details: dict | None = None) -> None:
        await self._db(self.store.add_event(owner, doc_id, event, details))

    # ---- privacy scanning of document text --------------------------------------------------------------------

    def _scan_pages(self, pages: list[Page]):
        return [self._privacy.findings(p.text) for p in pages]

    async def _scan(self, pages: list[Page]):
        try:
            return await asyncio.to_thread(self._scan_pages, pages)
        except Exception as exc:
            logger.error("document privacy scan failed: %s", type(exc).__name__)
            return None

    # ---- upload / review / index ------------------------------------------------------------------------------

    async def upload(self, owner: str, raw_filename: str | None, data: bytes, request_id: str) -> DocumentOut:
        self._require_database()
        self._require_embeddings()  # never accept a document we cannot index
        await self._check_dimensions()
        if len(data) > self._s.document_max_upload_bytes:
            raise AppError(413, "document_too_large", f"Files are limited to {self._s.document_max_upload_bytes // (1024 * 1024)} MB.")
        try:
            file_type = detect_type(data, raw_filename)
            pages = await asyncio.wait_for(
                asyncio.to_thread(
                    extract, data, file_type, max_pages=self._s.document_max_pages,
                    max_chars=self._s.document_max_extracted_chars, min_chars=self._s.document_min_text_chars,
                ),
                timeout=self._s.document_extraction_timeout_seconds,
            )
        except DocumentError as exc:
            logger.info("document rejected request_id=%s code=%s", request_id, exc.code)
            raise AppError(exc.status_code, exc.code, exc.message) from None
        except (asyncio.TimeoutError, TimeoutError):
            # The worker thread cannot be killed; page/size caps bound how long it can keep running.
            raise AppError(422, "document_extraction_timeout", "Extracting text took too long.") from None

        filename = sanitize_filename(raw_filename, file_type)
        doc = await self._db(self.store.create_document(owner, filename=filename, file_type=file_type, size_bytes=len(data), pages=pages))
        await self._event(owner, doc.id, "uploaded", {"file_type": file_type, "size_bytes": len(data), "pages": len(pages)})
        logger.info("document uploaded request_id=%s doc=%s type=%s bytes=%d pages=%d", request_id, doc.id, file_type, len(data), len(pages))

        scans = await self._scan(pages)
        if scans is None:
            return await self._fail(owner, doc, "privacy_engine_unavailable")
        findings = [f for page in scans for f in page]
        if not findings:
            await self._db(self.store.update_document(owner, doc.id, privacy_decision="not_required"))
            return await self._index(owner, doc.id, pages, request_id)

        decision = PolicyEngine.strictest([f.action for f in findings])
        required = "confirm" if decision == "warn" else "redact"
        categories = dict(Counter(f.category for f in findings))
        await self._db(self.store.update_document(
            owner, doc.id, status="needs_review", privacy_required_action=required, privacy_categories=categories,
        ))
        await self._event(owner, doc.id, "privacy_review_required", {"decision": decision, "required_action": required, "categories": categories})
        logger.info("document needs privacy review request_id=%s doc=%s required=%s categories=%s", request_id, doc.id, required, categories)
        return to_out(await self._get_owned(owner, doc.id))

    async def decide(self, owner: str, document_id: str, decision: str, request_id: str) -> DocumentOut:
        self._require_database()
        self._require_embeddings()
        await self._check_dimensions()
        doc = await self._get_owned(owner, document_id)
        if doc.status != "needs_review":
            raise AppError(409, "document_not_awaiting_review", "This document is not waiting for a privacy decision.")
        if decision == "confirm" and doc.privacy_required_action != "confirm":
            raise AppError(422, "privacy_decision_not_allowed", "This document contains information that must be redacted before it can be indexed.")

        pages = await self._db(self.store.get_pages(owner, doc.id))
        now = datetime.now(timezone.utc)
        if decision == "redact":
            def redact_all() -> tuple[list[Page], bool]:
                out = [Page(p.index, p.number, self._privacy.apply_redaction(p.text, self._privacy.findings(p.text))) for p in pages]
                clean = all(not self._privacy.findings(p.text) for p in out)  # verify on the exact text to be indexed
                return out, clean

            try:
                pages, clean = await asyncio.to_thread(redact_all)
            except Exception as exc:
                logger.error("document redaction failed: %s", type(exc).__name__)
                return await self._fail(owner, doc, "privacy_engine_unavailable")
            if not clean:
                return await self._fail(owner, doc, "redaction_incomplete")
            await self._db(self.store.replace_pages(owner, doc.id, pages))  # the unredacted text is not kept
            await self._db(self.store.update_document(owner, doc.id, privacy_decision="redacted", privacy_decided_at=now))
        else:
            await self._db(self.store.update_document(owner, doc.id, privacy_decision="confirmed", privacy_decided_at=now))
        await self._event(owner, doc.id, f"privacy_{'redacted' if decision == 'redact' else 'confirmed'}", {"categories": doc.privacy_categories})
        logger.info("document privacy decision request_id=%s doc=%s decision=%s", request_id, doc.id, decision)
        return await self._index(owner, doc.id, pages, request_id)

    async def reindex(self, owner: str, document_id: str, request_id: str) -> DocumentOut:
        self._require_database()
        self._require_embeddings()
        await self._check_dimensions()
        doc = await self._get_owned(owner, document_id)
        if doc.status not in ("failed", "ready") or doc.privacy_decision == "none":
            raise AppError(409, "document_not_reindexable", "Only indexed or failed documents whose privacy review is complete can be re-indexed.")
        pages = await self._db(self.store.get_pages(owner, doc.id))
        return await self._index(owner, doc.id, pages, request_id)

    async def _fail(self, owner: str, doc: DocumentRecord, code: str) -> DocumentOut:
        await self._db(self.store.update_document(owner, doc.id, status="failed", error_code=code, processed_at=datetime.now(timezone.utc)))
        await self._event(owner, doc.id, "failed", {"code": code})
        return to_out(await self._get_owned(owner, doc.id))

    async def _index(self, owner: str, document_id: str, pages: list[Page], request_id: str) -> DocumentOut:
        doc = await self._get_owned(owner, document_id)
        chunks = chunk_pages(pages, self._s.chunk_size_chars, self._s.chunk_overlap_chars)
        if not chunks:
            return await self._fail(owner, doc, "document_no_text")
        if len(chunks) > self._s.max_chunks_per_document:
            return await self._fail(owner, doc, "document_too_many_chunks")
        start = time.perf_counter()
        try:
            vectors = await self.embedder.embed([c.text for c in chunks], "document")
        except EmbeddingError as exc:
            logger.info("document embedding failed request_id=%s doc=%s code=%s", request_id, doc.id, exc.code)
            return await self._fail(owner, doc, exc.code)
        if len(vectors) != len(chunks) or any(len(v) != self._s.embedding_dimensions for v in vectors):
            return await self._fail(owner, doc, "embedding_dimension_mismatch")
        stored = await self._db(self.store.store_chunks(owner, doc.id, chunks, vectors, self.embedder.model, self.embedder.dimensions))
        await self._event(owner, doc.id, "indexed", {"chunks": len(chunks), "embedding_model": self.embedder.model})
        logger.info("document indexed request_id=%s doc=%s chunks=%d embed_ms=%d", request_id, doc.id, len(chunks), int((time.perf_counter() - start) * 1000))
        return to_out(stored)

    # ---- read / delete -------------------------------------------------------------------------------------------

    async def list(self, owner: str) -> list[DocumentOut]:
        self._require_database()
        return [to_out(d) for d in await self._db(self.store.list_documents(owner))]

    async def get(self, owner: str, document_id: str) -> DocumentOut:
        self._require_database()
        return to_out(await self._get_owned(owner, document_id))

    async def delete(self, owner: str, document_id: str, request_id: str) -> None:
        self._require_database()
        if not await self._db(self.store.delete_document(owner, document_id)):
            raise AppError(404, "document_not_found", "Document not found.")
        await self._event(owner, document_id, "deleted")
        logger.info("document deleted request_id=%s doc=%s", request_id, document_id)

    # ---- ask ----------------------------------------------------------------------------------------------------

    async def ask(self, owner: str, req: AskRequest, request_id: str) -> AskResponse:
        question = req.question.strip()
        if not question:
            raise AppError(422, "empty_message", "The question is empty.")
        if len(question) > self._s.max_message_chars:
            raise AppError(422, "message_too_long", f"Questions are limited to {self._s.max_message_chars} characters.")
        if req.model and req.routing_policy not in (None, "manual"):
            raise AppError(422, "conflicting_routing", "'model' can only be combined with routing_policy 'manual'.")
        ids = list(dict.fromkeys(req.document_ids))
        self._require_database()
        self._require_embeddings()

        # 1. Input privacy gate on the question, before any embedding call or retrieval.
        privacy = await self._privacy.enforce([ChatMessage(role="user", content=question)], req.privacy_ack, request_id)
        await self._check_dimensions()

        # 2. Every requested document must belong to this scope and be indexed.
        docs = [await self._db(self.store.get_document(owner, d)) for d in ids]
        missing = [d for d, doc in zip(ids, docs) if doc is None]
        if missing:
            raise AppError(404, "document_not_found", "One or more documents were not found.", {"missing_ids": missing})
        not_ready = {d.id: d.status for d in docs if d is not None and d.status != "ready"}
        if not_ready:
            raise AppError(409, "document_not_ready", "One or more documents are not indexed yet.", {"statuses": not_ready})

        # 3. Route plan before spending an embedding call.
        policy = "manual" if (req.model and req.routing_policy is None) else req.routing_policy
        try:
            plan = self._router.plan(DOCUMENTS_TASK, policy, req.provider, req.model, sensitive=privacy.decision == "warn")
        except RoutingError as exc:
            raise AppError(exc.status_code, exc.code, exc.message, exc.details) from None

        # 4. Embed the (approved) question and search ONLY this owner's requested documents.
        try:
            [qvec] = await self.embedder.embed([question], "query")
        except EmbeddingError as exc:
            raise AppError(exc.status_code, exc.code, exc.message) from None
        top_k = min(req.top_k or self._s.rag_top_k, self._s.rag_top_k)
        candidates = await self._db(self.store.search(owner, ids, qvec, top_k * 3))
        relevant = [c for c in candidates if c.similarity >= self._s.rag_min_similarity]
        unique, dropped = dedupe(relevant)
        passages = make_passages(unique[:top_k], self._s.rag_max_context_chars)
        retrieval = RetrievalInfo(
            embedding_model=self.embedder.model, top_k=top_k, candidates=len(candidates),
            below_threshold=len(candidates) - len(relevant), deduplicated=dropped, used=len(passages),
            min_similarity=self._s.rag_min_similarity,
            best_similarity=round(max((c.similarity for c in candidates), default=0.0), 4) if candidates else None,
            context_chars=sum(len(p.text) for p in passages), flagged_passages=sum(1 for p in passages if p.flagged),
        )
        logger.info(
            "documents retrieval request_id=%s docs=%d candidates=%d below=%d dedup=%d used=%d chars=%d",
            request_id, len(ids), retrieval.candidates, retrieval.below_threshold, dropped, retrieval.used, retrieval.context_chars,
        )
        if not passages:
            # No relevant evidence: do NOT let the model answer from general knowledge.
            raise AppError(
                404, "documents_insufficient_evidence",
                "The selected documents do not contain passages relevant enough to answer this question.",
                {"retrieval": retrieval.model_dump()},
            )

        # 5. One routed generation with instructions separate from the untrusted passages.
        messages = [ChatMessage(role="user", content=build_user_message(question, passages))]
        start = time.perf_counter()
        try:
            outcome = await self._router.execute(plan, messages, request_id, system=SYSTEM_INSTRUCTION)
        except RoutedFailure as failure:
            raise AppError(
                failure.error.status_code, failure.error.code, failure.error.message,
                {"retrieval": retrieval.model_dump(),
                 "routing": {"task_type": "documents", "routing_policy": plan.policy,
                             "attempts": [a.model_dump() for a in failure.attempts]}},
            ) from None
        latency_ms = int((time.perf_counter() - start) * 1000)
        info = outcome.info()

        # 6. Response privacy on the answer.
        try:
            answer, response_privacy = await self._response_privacy.process(outcome.result.text, request_id)
        except AppError as exc:
            exc.details = {
                **(exc.details if isinstance(exc.details, dict) else {}),
                "retrieval": retrieval.model_dump(),
                "routing": {"task_type": "documents", "routing_policy": plan.policy, "selected_provider": info.selected_provider,
                            "selected_model": info.selected_model, "attempts": [a.model_dump() for a in info.attempts]},
            }
            raise

        # 7. Citations must point at retrieved passages; excerpts go through the response policy too.
        cites = check_citations(answer, passages)
        excerpts = await asyncio.to_thread(lambda: [self._response_privacy.scan_fragment(p.text[:EXCERPT_CHARS]) for p in passages])
        logger.info(
            "documents answer request_id=%s status=%s cited=%d invalid_removed=%d links_removed=%d response_privacy=%s",
            request_id, cites.evidence_status, len(cites.cited_refs), cites.invalid_citations_removed,
            cites.unverified_links_removed, response_privacy.status,
        )
        return AskResponse(
            answer=cites.text,
            evidence_status=cites.evidence_status,  # type: ignore[arg-type]
            passages=[
                DocumentCitation(
                    ref=p.ref, document_id=p.chunk.document_id, filename=p.chunk.filename, chunk_id=p.chunk.chunk_id,
                    chunk_index=p.chunk.chunk_index, page_number=p.chunk.page_number, similarity=round(p.chunk.similarity, 4),
                    excerpt=excerpt, flags=["possible_prompt_injection"] if p.flagged else [], cited=p.ref in cites.cited_refs,
                )
                for p, excerpt in zip(passages, excerpts)
            ],
            invalid_citations_removed=cites.invalid_citations_removed,
            unverified_links_removed=cites.unverified_links_removed,
            metadata=AskMetadata(
                request_id=request_id, provider=info.selected_provider, model=outcome.result.model, latency_ms=latency_ms,
                usage=outcome.result.usage, privacy=privacy, prompt_source=req.prompt_source, routing=info,
                response_privacy=response_privacy, retrieval=retrieval,
            ),
        )
