import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import VERSION, router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.privacy.detector import PrivacyDetector
from app.providers.registry import ProviderRegistry
from app.router.router import ModelRouter
from app.services.chat_service import ChatService
from app.services.optimizer_service import OptimizerService
from app.services.privacy_service import PrivacyService
from app.services.response_privacy_service import ResponsePrivacyService
from app.research.tavily import TavilyClient
from app.documents.embeddings import GeminiEmbeddingProvider
from app.documents.store import PostgresDocumentStore
from app.services.document_service import DocumentService
from app.services.research_service import ResearchService


@lru_cache
def shared_detector(min_score: float) -> PrivacyDetector:
    """One Presidio engine per process (loading it is slow); app instances in tests share it."""
    return PrivacyDetector(min_score=min_score)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Third-party loggers that can emit analysed text or request details at DEBUG: keep them quiet whatever LOG_LEVEL is.
    for noisy in ("presidio-analyzer", "presidio_analyzer", "google_genai", "google.genai", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Load the NLP model at startup so the first request isn't slow.
        await asyncio.to_thread(app.state.privacy.detector.warm_up)
        yield
        close = getattr(app.state.documents.store, "close", None)
        if close is not None:  # release pooled database connections on shutdown
            await asyncio.to_thread(close)

    app = FastAPI(title="AgentVision X API", version=VERSION, lifespan=lifespan)
    registry = ProviderRegistry(settings)
    privacy = PrivacyService(settings, shared_detector(settings.privacy_min_score))
    app.state.registry = registry
    app.state.privacy = privacy
    app.state.optimizer = OptimizerService(settings, privacy)
    app.state.router = ModelRouter(settings, registry)
    app.state.response_privacy = ResponsePrivacyService(settings, privacy.detector)
    app.state.chat_service = ChatService(settings, app.state.router, privacy, app.state.response_privacy)
    app.state.settings = settings
    dsn = settings.database_url.get_secret_value() if settings.database_url else None
    app.state.documents = DocumentService(
        settings, privacy, app.state.router, app.state.response_privacy,
        PostgresDocumentStore(dsn or None), GeminiEmbeddingProvider(settings),
    )
    app.state.research = ResearchService(
        settings, privacy, app.state.router, app.state.response_privacy, TavilyClient(settings)
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        length = request.headers.get("content-length")
        # Uploads have their own (larger) limit; everything else keeps MAX_REQUEST_BYTES.
        is_upload = request.method == "POST" and request.url.path == "/api/documents"
        limit = settings.document_max_upload_bytes + 64 * 1024 if is_upload else settings.max_request_bytes
        if is_upload and not (length and length.isdigit()):
            # Checked BEFORE the multipart body is parsed, so an unbounded chunked upload is never spooled.
            response = JSONResponse(
                {"error": {"code": "length_required", "message": "Uploads must declare their size (Content-Length).", "request_id": request_id}},
                status_code=411,
            )
        elif length and length.isdigit() and int(length) > limit:
            response = JSONResponse(
                {"error": {"code": "payload_too_large", "message": "Request body is too large.", "request_id": request_id}},
                status_code=413,
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
