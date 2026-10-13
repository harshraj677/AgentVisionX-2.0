from fastapi import APIRouter, Depends, File, Request, Response, UploadFile

from app.core.errors import AppError
from app.documents.scope import owner_scope

from app.schemas.chat import ChatRequest, ChatResponse, DocumentsHealth, HealthResponse
from app.schemas.documents import AskRequest, AskResponse, DocumentList, DocumentOut, PrivacyDecisionRequest
from app.schemas.research import ResearchRequest, ResearchResponse, ResearchStatus
from app.schemas.routing import RouterModelsResponse
from app.schemas.optimize import OptimizeRequest, OptimizeResponse
from app.schemas.privacy import (
    PrivacyRedactRequest,
    PrivacyRedactResponse,
    PrivacyScanRequest,
    PrivacyScanResponse,
    PrivacyStatus,
)
from app.services.chat_service import ChatService
from app.services.optimizer_service import OptimizerService
from app.services.privacy_service import PrivacyService

router = APIRouter()
VERSION = "0.7.0"


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_privacy_service(request: Request) -> PrivacyService:
    return request.app.state.privacy


def get_optimizer_service(request: Request) -> OptimizerService:
    return request.app.state.optimizer


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    return HealthResponse(
        version=VERSION,
        providers=request.app.state.registry.configured_status(),
        privacy=PrivacyStatus(
            ready=request.app.state.privacy.detector.ready,
            response_policy=request.app.state.response_privacy.policy.mode,
        ),
        research=ResearchStatus(configured=request.app.state.research.search.is_configured()),
        documents=DocumentsHealth(
            database_configured=request.app.state.documents.store.is_configured(),
            embedding_configured=request.app.state.documents.embedder.is_configured(),
            embedding_model=request.app.state.documents.embedder.model,
            embedding_dimensions=request.app.state.documents.embedder.dimensions,
        ),
    )


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest, request: Request, service: ChatService = Depends(get_chat_service)
) -> ChatResponse:
    return await service.chat(req, request.state.request_id)


@router.post("/api/privacy/scan", response_model=PrivacyScanResponse)
async def privacy_scan(
    req: PrivacyScanRequest, request: Request, service: PrivacyService = Depends(get_privacy_service)
) -> PrivacyScanResponse:
    return await service.scan(req.text, request.state.request_id)


@router.post("/api/privacy/redact", response_model=PrivacyRedactResponse)
async def privacy_redact(
    req: PrivacyRedactRequest, request: Request, service: PrivacyService = Depends(get_privacy_service)
) -> PrivacyRedactResponse:
    return await service.redact(req.text, request.state.request_id)


@router.post("/api/optimize", response_model=OptimizeResponse)
async def optimize(
    req: OptimizeRequest, request: Request, service: OptimizerService = Depends(get_optimizer_service)
) -> OptimizeResponse:
    """Local, deterministic suggestion only. Calls no AI provider and sends nothing anywhere."""
    return await service.optimize(req.text, request.state.request_id)


@router.get("/api/router/models", response_model=RouterModelsResponse)
async def router_models(request: Request) -> RouterModelsResponse:
    """Configured models, their availability, and any price/latency data that is actually known (else null)."""
    return request.app.state.router.snapshot()


@router.post("/api/research", response_model=ResearchResponse)
async def research(req: ResearchRequest, request: Request) -> ResearchResponse:
    """Privacy-checked question -> one Tavily search -> bounded, cited synthesis -> response privacy scan."""
    return await request.app.state.research.research(req, request.state.request_id)


# ---- documents (Phase 7). Every route is scoped by the X-Document-Scope owner; IDs alone never grant access. ----

@router.post("/api/documents", response_model=DocumentOut, status_code=201)
async def upload_document(request: Request, file: UploadFile = File(...), owner: str = Depends(owner_scope)) -> DocumentOut:
    limit = request.app.state.settings.document_max_upload_bytes
    data = await file.read(limit + 1)  # never read more than the limit allows
    if len(data) > limit:
        raise AppError(413, "document_too_large", f"Files are limited to {limit // (1024 * 1024)} MB.")
    return await request.app.state.documents.upload(owner, file.filename, data, request.state.request_id)


@router.get("/api/documents", response_model=DocumentList)
async def list_documents(request: Request, owner: str = Depends(owner_scope)) -> DocumentList:
    return DocumentList(documents=await request.app.state.documents.list(owner))


@router.post("/api/documents/ask", response_model=AskResponse)
async def ask_documents(req: AskRequest, request: Request, owner: str = Depends(owner_scope)) -> AskResponse:
    return await request.app.state.documents.ask(owner, req, request.state.request_id)


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: str, request: Request, owner: str = Depends(owner_scope)) -> DocumentOut:
    return await request.app.state.documents.get(owner, document_id)


@router.post("/api/documents/{document_id}/privacy-decision", response_model=DocumentOut)
async def document_privacy_decision(
    document_id: str, req: PrivacyDecisionRequest, request: Request, owner: str = Depends(owner_scope)
) -> DocumentOut:
    return await request.app.state.documents.decide(owner, document_id, req.decision, request.state.request_id)


@router.post("/api/documents/{document_id}/reindex", response_model=DocumentOut)
async def reindex_document(document_id: str, request: Request, owner: str = Depends(owner_scope)) -> DocumentOut:
    return await request.app.state.documents.reindex(owner, document_id, request.state.request_id)


@router.delete("/api/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, request: Request, owner: str = Depends(owner_scope)) -> Response:
    await request.app.state.documents.delete(owner, document_id, request.state.request_id)
    return Response(status_code=204)
