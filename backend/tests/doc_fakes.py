"""Test doubles for documents: an in-memory store with the SAME owner-scoping rules as PostgresDocumentStore, a
deterministic embedder, and a tiny PDF builder. TEST-ONLY: production never uses these (no fake retrieval in prod)."""
import hashlib
import math
import re
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from app.documents.chunking import Chunk
from app.documents.embeddings import EmbeddingError, normalize
from app.documents.files import Page
from app.documents.store import DocumentRecord, RetrievedChunk, StoreUnavailable

SCOPE_A = "scopeA_" + "a1B2c3D4" * 5
SCOPE_B = "scopeB_" + "z9Y8x7W6" * 5


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder: texts sharing words have higher cosine similarity."""

    def __init__(self, dimensions: int = 768, model: str = "fake-embedding", configured: bool = True, error: EmbeddingError | None = None):
        self.dimensions, self.model, self.configured, self.error = dimensions, model, configured, error
        self.calls: list[tuple[str, list[str]]] = []  # (purpose, texts)

    def is_configured(self) -> bool:
        return self.configured

    async def embed(self, texts, purpose):
        self.calls.append((purpose, list(texts)))
        if self.error:
            raise self.error
        out = []
        for t in texts:
            v = [0.0] * self.dimensions
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                h = int(hashlib.sha256(w.encode()).hexdigest(), 16)
                v[h % self.dimensions] += 1.0
            if not any(v):
                v[0] = 1.0
            out.append(normalize(v))
        return out

    def texts(self, purpose: str) -> list[str]:
        return [t for p, ts in self.calls if p == purpose for t in ts]


class InMemoryDocumentStore:
    def __init__(self, dimensions: int | None = 768):
        self.dimensions = dimensions
        self.configured = True
        self.fail = False
        self.docs: dict[str, tuple[str, DocumentRecord]] = {}  # id -> (owner, record)
        self.pages: dict[str, list[Page]] = {}
        self.chunks: dict[str, list[tuple[Chunk, list[float], str, int]]] = {}  # doc -> (chunk, vector, chunk_id, version)
        self.events: list[tuple[str, str, str, dict]] = []
        self.search_calls: list[tuple[str, list[str], int]] = []
        self.leases: dict[str, datetime] = {}  # doc id -> indexing_started_at (None/absent = no job running)
        self.clock = lambda: datetime.now(timezone.utc)  # tests move time by replacing this
        self.ping_result = "ok"
        self.closed = False

    def _check(self):
        if self.fail:
            raise StoreUnavailable()

    def _owned(self, owner, doc_id):
        entry = self.docs.get(doc_id)
        return entry[1] if entry and entry[0] == owner else None

    def is_configured(self):
        return self.configured

    async def embedding_dimensions(self):
        self._check()
        return self.dimensions

    async def create_document(self, owner, *, filename, file_type, size_bytes, pages):
        self._check()
        doc = DocumentRecord(
            id=str(uuid.uuid4()), filename=filename, file_type=file_type, size_bytes=size_bytes, status="processing",
            page_count=len(pages), char_count=sum(len(p.text) for p in pages), uploaded_at=datetime.now(timezone.utc),
        )
        self.docs[doc.id] = (owner, doc)
        self.pages[doc.id] = list(pages)
        self.leases[doc.id] = self.clock()
        return replace(doc)

    async def get_document(self, owner, document_id):
        self._check()
        doc = self._owned(owner, document_id)
        return replace(doc) if doc else None

    async def list_documents(self, owner):
        self._check()
        return [replace(d) for o, d in self.docs.values() if o == owner]

    async def get_pages(self, owner, document_id):
        self._check()
        return list(self.pages.get(document_id, [])) if self._owned(owner, document_id) else []

    async def replace_pages(self, owner, document_id, pages):
        self._check()
        if self._owned(owner, document_id):
            self.pages[document_id] = list(pages)

    async def update_document(self, owner, document_id, **fields):
        self._check()
        doc = self._owned(owner, document_id)
        if doc:
            if "indexing_started_at" in fields:
                started = fields.pop("indexing_started_at")
                if started is None:
                    self.leases.pop(document_id, None)
                else:
                    self.leases[document_id] = started
            fields.pop("privacy_decided_at", None)
            self.docs[document_id] = (owner, replace(doc, **fields))

    async def store_chunks(self, owner, document_id, chunks, vectors, model, dimensions):
        self._check()
        doc = self._owned(owner, document_id)
        version = doc.index_version + 1
        self.leases.pop(document_id, None)
        self.chunks[document_id] = [(c, v, str(uuid.uuid4()), version) for c, v in zip(chunks, vectors)]
        new = replace(doc, status="ready", error_code=None, embedding_model=model, embedding_dimensions=dimensions,
                      chunk_count=len(chunks), index_version=version, processed_at=datetime.now(timezone.utc))
        self.docs[document_id] = (owner, new)
        return replace(new)

    async def search(self, owner, document_ids, vector, limit):
        self._check()
        self.search_calls.append((owner, list(document_ids), limit))
        hits = []
        for doc_id in document_ids:
            doc = self._owned(owner, doc_id)
            if not doc or doc.status != "ready":
                continue
            for chunk, v, cid, version in self.chunks.get(doc_id, []):
                if version != doc.index_version:
                    continue
                sim = sum(a * b for a, b in zip(v, vector))
                hits.append(RetrievedChunk(cid, doc_id, doc.filename, chunk.index, chunk.page_number, chunk.text, sim))
        hits.sort(key=lambda h: -h.similarity)
        return hits[:limit]

    async def delete_document(self, owner, document_id):
        self._check()
        if not self._owned(owner, document_id):
            return False
        del self.docs[document_id]
        self.pages.pop(document_id, None)
        self.chunks.pop(document_id, None)
        return True

    async def add_event(self, owner, document_id, event, details=None):
        self._check()
        self.events.append((owner, document_id, event, dict(details or {})))

    async def claim_indexing(self, owner, document_id, stale_seconds):
        self._check()
        if not self._owned(owner, document_id):
            return False
        started = self.leases.get(document_id)
        if started is not None and (self.clock() - started).total_seconds() < stale_seconds:
            return False
        self.leases[document_id] = self.clock()
        return True

    async def recover_stale(self, owner, stale_seconds):
        self._check()
        recovered = []
        for doc_id, (o, doc) in list(self.docs.items()):
            if o != owner or doc.status != "processing":
                continue
            since = self.leases.get(doc_id) or doc.uploaded_at
            if (self.clock() - since).total_seconds() >= stale_seconds:
                self.docs[doc_id] = (o, replace(doc, status="failed", error_code="indexing_interrupted", processed_at=self.clock()))
                self.leases.pop(doc_id, None)
                recovered.append(doc_id)
        return recovered

    def close(self):
        self.closed = True

    async def ping(self):
        return "unavailable" if self.fail else self.ping_result


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    """A small, valid PDF with one text page per entry (Helvetica, WinAnsi)."""
    objects: dict[int, bytes] = {3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    n = 4
    for text in pages:
        ops = ["BT /F1 11 Tf 50 750 Td 14 TL"] + [f"({_escape(line)}) Tj T*" for line in text.split("\n")] + ["ET"]
        stream = "\n".join(ops).encode("latin-1", "replace")
        objects[n] = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {n + 1} 0 R >>".encode()
        objects[n + 1] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        kids.append(f"{n} 0 R")
        n += 2
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = b"%PDF-1.4\n"
    offsets = {}
    for i in sorted(objects):
        offsets[i] = len(out)
        out += f"{i} 0 obj\n".encode() + objects[i] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for i in range(1, size):
        out += f"{offsets[i]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def make_encrypted_pdf() -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret-password")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
