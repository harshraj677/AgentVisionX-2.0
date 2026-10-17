"""Document storage. Every method takes the owner scope and filters on it: there is no way to read, search, change or
delete a document by ID alone. The PostgreSQL implementation uses synchronous psycopg in worker threads (async psycopg
cannot run on Windows' default event loop). Database errors become StoreUnavailable without exposing driver messages.
"""
import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.documents.chunking import Chunk
from app.documents.files import Page

logger = logging.getLogger("agentvision.documents")


class StoreUnavailable(Exception):
    pass


@dataclass
class DocumentRecord:
    id: str
    filename: str
    file_type: str
    size_bytes: int
    status: str
    page_count: int | None = None
    char_count: int | None = None
    error_code: str | None = None
    privacy_required_action: str = "none"
    privacy_decision: str = "none"
    privacy_categories: dict[str, int] = field(default_factory=dict)
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    chunk_count: int = 0
    index_version: int = 0
    uploaded_at: datetime | None = None
    processed_at: datetime | None = None


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    page_number: int | None
    text: str
    similarity: float  # cosine similarity computed by the database (1 - cosine distance)


class DocumentStore(Protocol):
    def is_configured(self) -> bool: ...
    async def embedding_dimensions(self) -> int | None: ...
    async def create_document(self, owner: str, *, filename: str, file_type: str, size_bytes: int, pages: list[Page]) -> DocumentRecord: ...
    async def get_document(self, owner: str, document_id: str) -> DocumentRecord | None: ...
    async def list_documents(self, owner: str) -> list[DocumentRecord]: ...
    async def get_pages(self, owner: str, document_id: str) -> list[Page]: ...
    async def replace_pages(self, owner: str, document_id: str, pages: list[Page]) -> None: ...
    async def update_document(self, owner: str, document_id: str, **fields) -> None: ...
    async def store_chunks(self, owner: str, document_id: str, chunks: list[Chunk], vectors: list[list[float]], model: str, dimensions: int) -> DocumentRecord: ...
    async def search(self, owner: str, document_ids: list[str], vector: list[float], limit: int) -> list[RetrievedChunk]: ...
    async def delete_document(self, owner: str, document_id: str) -> bool: ...
    async def add_event(self, owner: str, document_id: str, event: str, details: dict | None = None) -> None: ...
    async def claim_indexing(self, owner: str, document_id: str, stale_seconds: int) -> bool: ...
    async def recover_stale(self, owner: str, stale_seconds: int) -> list[str]: ...
    async def ping(self) -> str: ...


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


_COLUMNS = (
    "id, filename, file_type, size_bytes, status, page_count, char_count, error_code, privacy_required_action, "
    "privacy_decision, privacy_categories, embedding_model, embedding_dimensions, chunk_count, index_version, "
    "uploaded_at, processed_at"
)
_UPDATABLE = {
    "status", "error_code", "privacy_required_action", "privacy_decision", "privacy_categories", "privacy_decided_at",
    "processed_at", "page_count", "char_count", "indexing_started_at",
}


def _record(row) -> DocumentRecord:
    return DocumentRecord(
        id=str(row[0]), filename=row[1], file_type=row[2], size_bytes=row[3], status=row[4], page_count=row[5],
        char_count=row[6], error_code=row[7], privacy_required_action=row[8], privacy_decision=row[9],
        privacy_categories=dict(row[10] or {}), embedding_model=row[11], embedding_dimensions=row[12],
        chunk_count=row[13], index_version=row[14], uploaded_at=row[15], processed_at=row[16],
    )


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


class PostgresDocumentStore:
    def __init__(self, dsn: str | None):
        self._dsn = dsn
        self._pool = None
        self._pool_lock = threading.Lock()  # _run executes in worker threads: create the pool exactly once

    def is_configured(self) -> bool:
        return bool(self._dsn)

    def _get_pool(self):
        with self._pool_lock:
            if self._pool is None:
                from psycopg_pool import ConnectionPool

                pool = ConnectionPool(
                    self._dsn, min_size=1, max_size=5, timeout=5, open=False, kwargs={"connect_timeout": 3}
                )
                # Do not wait for the first connection: a pool that failed its initial wait used to be cached closed,
                # so a database that was down at first use stayed unusable until the backend restarted. Opened without
                # waiting, the pool keeps retrying in the background and recovers when the database returns; each
                # request waits at most `timeout` seconds and then fails with StoreUnavailable.
                pool.open()
                self._pool = pool
            return self._pool

    def close(self) -> None:
        """Releases pooled connections (called on application shutdown). Safe to call repeatedly."""
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    async def _run(self, fn):
        def work():
            try:
                with self._get_pool().connection() as conn:
                    with conn.transaction():
                        return fn(conn)
            except StoreUnavailable:
                raise
            except Exception as exc:  # driver/pool errors: never surface their text (it can contain data)
                logger.error("document store error: %s", type(exc).__name__)
                raise StoreUnavailable() from None

        return await asyncio.to_thread(work)

    async def embedding_dimensions(self) -> int | None:
        def fn(conn):
            row = conn.execute("SELECT value FROM rag_meta WHERE key = 'embedding_dimensions'").fetchone()
            return int(row[0]) if row else None

        return await self._run(fn)

    async def create_document(self, owner, *, filename, file_type, size_bytes, pages):
        doc_id = uuid.uuid4()

        def fn(conn):
            row = conn.execute(
                f"INSERT INTO rag_documents (id, owner_scope, filename, file_type, size_bytes, page_count, char_count, status, indexing_started_at) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing', now()) RETURNING {_COLUMNS}",
                (doc_id, owner, filename, file_type, size_bytes, len(pages), sum(len(p.text) for p in pages)),
            ).fetchone()
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO rag_document_pages (document_id, page_index, page_number, text) VALUES (%s, %s, %s, %s)",
                    [(doc_id, p.index, p.number, p.text) for p in pages],
                )
            return _record(row)

        return await self._run(fn)

    async def get_document(self, owner, document_id):
        did = _uuid(document_id)
        if did is None:
            return None

        def fn(conn):
            row = conn.execute(f"SELECT {_COLUMNS} FROM rag_documents WHERE id = %s AND owner_scope = %s", (did, owner)).fetchone()
            return _record(row) if row else None

        return await self._run(fn)

    async def list_documents(self, owner):
        def fn(conn):
            rows = conn.execute(f"SELECT {_COLUMNS} FROM rag_documents WHERE owner_scope = %s ORDER BY uploaded_at DESC", (owner,)).fetchall()
            return [_record(r) for r in rows]

        return await self._run(fn)

    async def get_pages(self, owner, document_id):
        did = _uuid(document_id)

        def fn(conn):
            rows = conn.execute(
                "SELECT p.page_index, p.page_number, p.text FROM rag_document_pages p JOIN rag_documents d ON d.id = p.document_id "
                "WHERE p.document_id = %s AND d.owner_scope = %s ORDER BY p.page_index",
                (did, owner),
            ).fetchall()
            return [Page(r[0], r[1], r[2]) for r in rows]

        return await self._run(fn)

    async def replace_pages(self, owner, document_id, pages):
        did = _uuid(document_id)

        def fn(conn):
            if conn.execute("SELECT 1 FROM rag_documents WHERE id = %s AND owner_scope = %s", (did, owner)).fetchone() is None:
                return
            conn.execute("DELETE FROM rag_document_pages WHERE document_id = %s", (did,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO rag_document_pages (document_id, page_index, page_number, text) VALUES (%s, %s, %s, %s)",
                    [(did, p.index, p.number, p.text) for p in pages],
                )

        await self._run(fn)

    async def update_document(self, owner, document_id, **fields):
        bad = set(fields) - _UPDATABLE
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        did = _uuid(document_id)
        if "privacy_categories" in fields:
            fields["privacy_categories"] = json.dumps(fields["privacy_categories"])
        sets = ", ".join(f"{k} = %s" for k in fields)

        def fn(conn):
            conn.execute(f"UPDATE rag_documents SET {sets} WHERE id = %s AND owner_scope = %s", (*fields.values(), did, owner))

        await self._run(fn)

    async def store_chunks(self, owner, document_id, chunks, vectors, model, dimensions):
        did = _uuid(document_id)

        def fn(conn):
            row = conn.execute(
                "SELECT index_version FROM rag_documents WHERE id = %s AND owner_scope = %s FOR UPDATE", (did, owner)
            ).fetchone()
            if row is None:
                raise StoreUnavailable()
            version = row[0] + 1
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO rag_chunks (id, document_id, owner_scope, index_version, chunk_index, page_number, text, embedding, embedding_model) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)",
                    [(uuid.uuid4(), did, owner, version, c.index, c.page_number, c.text, _vector_literal(v), model) for c, v in zip(chunks, vectors)],
                )
            # Older index versions are removed in the same transaction, so retrieval never mixes versions.
            conn.execute("DELETE FROM rag_chunks WHERE document_id = %s AND index_version < %s", (did, version))
            updated = conn.execute(
                f"UPDATE rag_documents SET status = 'ready', error_code = NULL, embedding_model = %s, embedding_dimensions = %s, "
                f"chunk_count = %s, index_version = %s, processed_at = now(), indexing_started_at = NULL WHERE id = %s AND owner_scope = %s RETURNING {_COLUMNS}",
                (model, dimensions, len(chunks), version, did, owner),
            ).fetchone()
            return _record(updated)

        return await self._run(fn)

    async def search(self, owner, document_ids, vector, limit):
        ids = [u for u in (_uuid(d) for d in document_ids) if u is not None]
        literal = _vector_literal(vector)

        def fn(conn):
            rows = conn.execute(
                "SELECT c.id, c.document_id, d.filename, c.chunk_index, c.page_number, c.text, "
                "       1 - (c.embedding <=> %s::vector) AS similarity "
                "FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
                "WHERE c.owner_scope = %s AND d.owner_scope = %s AND c.document_id = ANY(%s) "
                "  AND d.status = 'ready' AND c.index_version = d.index_version "
                "ORDER BY c.embedding <=> %s::vector LIMIT %s",
                (literal, owner, owner, ids, literal, limit),
            ).fetchall()
            return [RetrievedChunk(str(r[0]), str(r[1]), r[2], r[3], r[4], r[5], float(r[6])) for r in rows]

        return await self._run(fn)

    async def delete_document(self, owner, document_id):
        did = _uuid(document_id)
        if did is None:
            return False

        def fn(conn):
            # Pages and chunks (incl. embeddings) go with it via ON DELETE CASCADE.
            row = conn.execute("DELETE FROM rag_documents WHERE id = %s AND owner_scope = %s RETURNING id", (did, owner)).fetchone()
            return row is not None

        return await self._run(fn)

    async def add_event(self, owner, document_id, event, details=None):
        did = _uuid(document_id)

        def fn(conn):
            conn.execute(
                "INSERT INTO rag_document_events (document_id, owner_scope, event, details) VALUES (%s, %s, %s, %s)",
                (did, owner, event, json.dumps(details or {})),
            )

        await self._run(fn)

    async def claim_indexing(self, owner, document_id, stale_seconds):
        """Atomically takes the indexing lease. False if a live (non-stale) job holds it or the document is not this owner's."""
        did = _uuid(document_id)
        if did is None:
            return False

        def fn(conn):
            row = conn.execute(
                "UPDATE rag_documents SET indexing_started_at = now() WHERE id = %s AND owner_scope = %s "
                "AND (indexing_started_at IS NULL OR indexing_started_at < now() - make_interval(secs => %s)) RETURNING id",
                (did, owner, stale_seconds),
            ).fetchone()
            return row is not None

        return await self._run(fn)

    async def recover_stale(self, owner, stale_seconds):
        """Marks this owner's 'processing' documents whose lease expired as failed (retryable). Deletes nothing."""

        def fn(conn):
            rows = conn.execute(
                "UPDATE rag_documents SET status = 'failed', error_code = 'indexing_interrupted', indexing_started_at = NULL, "
                "processed_at = now() WHERE owner_scope = %s AND status = 'processing' "
                "AND COALESCE(indexing_started_at, uploaded_at) < now() - make_interval(secs => %s) RETURNING id",
                (owner, stale_seconds),
            ).fetchall()
            return [str(r[0]) for r in rows]

        return await self._run(fn)

    async def ping(self):
        """Cheap readiness probe on its own short-timeout connection (never waits on the pool).
        Returns 'ok', 'not_migrated' or 'unavailable'. Never raises and never exposes driver text."""
        if not self._dsn:
            return "unavailable"

        def work():
            import psycopg

            try:
                with psycopg.connect(self._dsn, connect_timeout=3, autocommit=True) as conn:
                    conn.execute("SET statement_timeout = 3000")
                    row = conn.execute("SELECT to_regclass('public.rag_meta') IS NOT NULL").fetchone()
                    return "ok" if row and row[0] else "not_migrated"
            except Exception as exc:
                logger.warning("document store ping failed: %s", type(exc).__name__)
                return "unavailable"

        return await asyncio.to_thread(work)
