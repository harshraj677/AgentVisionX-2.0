"""End-to-end RAG through the HTTP API and the real DocumentService, backed by a REAL PostgreSQL database.

What is REAL here:    FastAPI app + routing, upload validation and PDF/TXT extraction (pypdf), the Presidio privacy gate,
                      chunking, the PostgresDocumentStore (all SQL, transactions, ownership filters, cascades), the router,
                      response privacy scanning, citation validation, and logging.
What is MOCKED here: the embedding provider (tests.doc_fakes.FakeEmbedder: deterministic bag-of-words vectors, so the
                      reported `embedding_model` is "fake-embedding") and the answering model (a ScriptedProvider that
                      returns a fixed answer). NO real Gemini embedding or generation happens in this file.
What may be EMULATED: the pgvector type/operator/HNSW index, when the test database was prepared without the extension
                      (see README, "Verification status"). These tests pass identically against the real extension.

Skipped unless TEST_DATABASE_URL passes the safety guard in tests/pg_guard.py (local host, a "test" database name, and not the
application's own DATABASE_URL). Only rows created by these tests are deleted; nothing is ever dropped or truncated.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.documents.store import PostgresDocumentStore
from app.main import create_app
from tests.doc_fakes import FakeEmbedder, make_pdf
from tests.helpers import ScriptedProvider, make_settings, reply
from tests.pg_guard import safe_test_database

DSN, SKIP_REASON = safe_test_database()
pytestmark = pytest.mark.skipif(DSN is None, reason=SKIP_REASON)

PDF_PAGES = [
    "this handbook explains the onboarding steps for new staff members.",
    "Leave policy: employees receive 25 days of paid leave per year. Unused leave expires at year end.",
    "Equipment: laptops are issued by the IT department within three working days.",
]
ANSWER = "Employees receive 25 days of paid leave per year [D1]."
EMAIL = "priya.sharma@example.com"


def scope() -> str:
    return "e2e_" + uuid.uuid4().hex + uuid.uuid4().hex[:8]


@pytest.fixture
def env():
    import psycopg

    settings = make_settings(database_url=DSN, rag_min_similarity=0.1)
    app = create_app(settings)
    provider = ScriptedProvider("gemini", models=("flash",), script={"flash": [reply(ANSWER)] * 10})
    app.state.registry._providers = {"gemini": provider}
    svc = app.state.documents
    svc.store = PostgresDocumentStore(DSN)
    svc.embedder = FakeEmbedder(768)
    client = TestClient(app, raise_server_exceptions=False)
    created: list[str] = []
    yield client, svc, provider, created
    with psycopg.connect(DSN, autocommit=True) as conn:  # only rows this test created, by owner
        for owner in created:
            conn.execute("DELETE FROM rag_documents WHERE owner_scope = %s", (owner,))
            conn.execute("DELETE FROM rag_document_events WHERE owner_scope = %s", (owner,))


def hdr(key):
    return {"X-Document-Scope": key}


def owner_hash(key):
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()


def test_full_rag_lifecycle_real_postgres_mocked_embeddings_and_model(env):
    client, svc, provider, created = env
    a, b = scope(), scope()
    created += [owner_hash(a), owner_hash(b)]

    # upload + extraction + indexing (PDF, 3 pages)
    r = client.post("/api/documents", files={"file": ("handbook.pdf", make_pdf(PDF_PAGES), "application/pdf")}, headers=hdr(a))
    doc = r.json()
    assert r.status_code == 201 and doc["status"] == "ready" and doc["page_count"] == 3 and doc["chunk_count"] == 3
    assert doc["privacy"]["decision"] == "not_required" and doc["embedding_model"] == "fake-embedding"

    # listing and status
    assert [d["id"] for d in client.get("/api/documents", headers=hdr(a)).json()["documents"]] == [doc["id"]]
    assert client.get(f"/api/documents/{doc['id']}", headers=hdr(a)).json()["status"] == "ready"

    # retrieval of a known fact, with page/chunk citations
    r = client.post("/api/documents/ask", json={"question": "How many days of paid leave do employees receive per year?", "document_ids": [doc["id"]]}, headers=hdr(a))
    data = r.json()
    assert r.status_code == 200 and data["answer"] == ANSWER and data["evidence_status"] == "cited"
    top = data["passages"][0]
    assert top["ref"] == "D1" and top["page_number"] == 2 and "25 days" in top["excerpt"] and top["cited"] is True
    assert top["document_id"] == doc["id"] and top["filename"] == "handbook.pdf" and 0 < top["similarity"] <= 1
    assert data["metadata"]["routing"]["task_type"] == "documents" and data["metadata"]["response_privacy"]["status"] == "clean"
    assert data["metadata"]["request_id"] == r.headers["x-request-id"]
    assert "<<<PASSAGE D1>>>" in provider.seen_messages[-1][0].content

    # insufficient evidence: with a strict threshold an unrelated question retrieves nothing and the model is NOT called
    before = len(provider.calls)
    svc._s.rag_min_similarity = 0.95
    try:
        r = client.post("/api/documents/ask", json={"question": "zebra quantum submarine xylophone", "document_ids": [doc["id"]]}, headers=hdr(a))
    finally:
        svc._s.rag_min_similarity = 0.1
    assert r.status_code == 404 and r.json()["error"]["code"] == "documents_insufficient_evidence"
    assert r.json()["error"]["details"]["retrieval"]["used"] == 0 and len(provider.calls) == before

    # isolation: another scope sees nothing and cannot ask, delete, or decide
    assert client.get("/api/documents", headers=hdr(b)).json() == {"documents": []}
    assert client.get(f"/api/documents/{doc['id']}", headers=hdr(b)).status_code == 404
    r = client.post("/api/documents/ask", json={"question": "leave days?", "document_ids": [doc["id"]]}, headers=hdr(b))
    assert r.status_code == 404 and r.json()["error"]["code"] == "document_not_found"
    assert client.delete(f"/api/documents/{doc['id']}", headers=hdr(b)).status_code == 404
    assert client.post(f"/api/documents/{doc['id']}/privacy-decision", json={"decision": "confirm"}, headers=hdr(b)).status_code == 404

    # deletion removes everything and retrieval no longer returns the content
    import psycopg

    assert client.delete(f"/api/documents/{doc['id']}", headers=hdr(a)).status_code == 204
    with psycopg.connect(DSN) as conn:
        for table in ("rag_document_pages", "rag_chunks"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE document_id = %s", (uuid.UUID(doc["id"]),)).fetchone()[0] == 0
        events = [e[0] for e in conn.execute("SELECT event FROM rag_document_events WHERE document_id = %s ORDER BY id", (uuid.UUID(doc["id"]),)).fetchall()]
    assert events == ["uploaded", "indexed", "deleted"]  # audit trail survives, without content
    r = client.post("/api/documents/ask", json={"question": "How many days of paid leave?", "document_ids": [doc["id"]]}, headers=hdr(a))
    assert r.status_code == 404 and r.json()["error"]["code"] == "document_not_found"


def test_privacy_review_redaction_and_retrieval_real_postgres_mocked_embeddings(env):
    client, svc, provider, created = env
    key = scope()
    created.append(owner_hash(key))
    text = f"Support contact: {EMAIL}. Employees receive 25 days of paid leave per year."
    r = client.post("/api/documents", files={"file": ("contacts.txt", text.encode(), "text/plain")}, headers=hdr(key))
    doc = r.json()
    assert doc["status"] == "needs_review" and doc["privacy"]["required_action"] == "redact" and doc["chunk_count"] == 0
    assert EMAIL not in r.text and svc.embedder.calls == []  # nothing embedded before the decision

    # "confirm" is refused for redact-level content
    r = client.post(f"/api/documents/{doc['id']}/privacy-decision", json={"decision": "confirm"}, headers=hdr(key))
    assert r.status_code == 422 and svc.embedder.calls == []

    # redaction: exercises the real privacy_decided_at / jsonb / replace_pages SQL
    r = client.post(f"/api/documents/{doc['id']}/privacy-decision", json={"decision": "redact"}, headers=hdr(key))
    done = r.json()
    assert r.status_code == 200 and done["status"] == "ready" and done["privacy"]["decision"] == "redacted"
    assert done["privacy"]["categories"] == {"EMAIL_ADDRESS": 1}
    sent = " ".join(svc.embedder.texts("document"))
    assert EMAIL not in sent and "[EMAIL_ADDRESS]" in sent

    import psycopg

    with psycopg.connect(DSN) as conn:
        stored = " ".join(row[0] for row in conn.execute("SELECT text FROM rag_document_pages WHERE document_id = %s", (uuid.UUID(doc["id"]),)).fetchall())
        stored += " ".join(row[0] for row in conn.execute("SELECT text FROM rag_chunks WHERE document_id = %s", (uuid.UUID(doc["id"]),)).fetchall())
        decided = conn.execute("SELECT privacy_decided_at IS NOT NULL, privacy_decision FROM rag_documents WHERE id = %s", (uuid.UUID(doc["id"]),)).fetchone()
        events = [e[0] for e in conn.execute("SELECT event FROM rag_document_events WHERE document_id = %s ORDER BY id", (uuid.UUID(doc["id"]),)).fetchall()]
    assert EMAIL not in stored and decided == (True, "redacted")
    assert events == ["uploaded", "privacy_review_required", "privacy_redacted", "indexed"]

    r = client.post("/api/documents/ask", json={"question": "paid leave days per year for employees", "document_ids": [doc["id"]]}, headers=hdr(key))
    assert r.status_code == 200 and r.json()["passages"][0]["document_id"] == doc["id"]


def test_mocked_embedding_failure_leaves_failed_document_without_chunks_in_real_postgres(env):
    from app.documents.embeddings import EmbeddingError

    client, svc, provider, created = env
    key = scope()
    created.append(owner_hash(key))
    svc.embedder.error = EmbeddingError(429, "embedding_rate_limited", "slow")
    doc = client.post("/api/documents", files={"file": ("a.txt", b"Employees receive leave days each year. Plain text here.", "text/plain")}, headers=hdr(key)).json()
    assert doc["status"] == "failed" and doc["error_code"] == "embedding_rate_limited" and doc["chunk_count"] == 0

    import psycopg

    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT count(*) FROM rag_chunks WHERE document_id = %s", (uuid.UUID(doc["id"]),)).fetchone()[0] == 0
    r = client.post("/api/documents/ask", json={"question": "leave days", "document_ids": [doc["id"]]}, headers=hdr(key))
    assert r.status_code == 409 and r.json()["error"]["code"] == "document_not_ready" and provider.calls == []

    svc.embedder.error = None  # recovery: re-index succeeds and replaces nothing partial
    done = client.post(f"/api/documents/{doc['id']}/reindex", headers=hdr(key)).json()
    assert done["status"] == "ready" and done["chunk_count"] == 1 and done["error_code"] is None


def test_real_database_outage_is_reported_without_fabricated_answers(env):
    client, svc, provider, created = env
    key = scope()
    created.append(owner_hash(key))
    svc.store = PostgresDocumentStore("postgresql://nobody:nothing@127.0.0.1:9/avx_test")  # nothing listens on port 9
    r = client.get("/api/documents", headers=hdr(key))
    assert r.status_code == 503 and r.json()["error"]["code"] == "documents_database_unavailable"
    assert "nobody" not in r.text and "nothing@" not in r.text  # no connection details leak
    r = client.post("/api/documents/ask", json={"question": "anything", "document_ids": [str(uuid.uuid4())]}, headers=hdr(key))
    assert r.status_code == 503 and provider.calls == [] and set(r.json()) == {"error"}


def test_real_store_path_never_logs_document_text_questions_or_scope_keys(env, caplog):
    import logging

    client, svc, provider, created = env
    key = scope()
    created.append(owner_hash(key))
    caplog.set_level(logging.DEBUG)
    body = b"project kestrel budget is 4.2 million and kestrel launches in spring. employees receive leave days."
    doc = client.post("/api/documents", files={"file": ("kestrel-plan.txt", body, "text/plain")}, headers=hdr(key)).json()
    assert doc["status"] == "ready", doc
    r = client.post("/api/documents/ask", json={"question": "what is the kestrel budget?", "document_ids": [doc["id"]]}, headers=hdr(key))
    assert r.status_code == 200
    client.delete(f"/api/documents/{doc['id']}", headers=hdr(key))
    for leaked in ("kestrel", "4.2 million", "kestrel-plan", key, owner_hash(key), ANSWER):
        assert leaked not in caplog.text, leaked
    assert "document indexed" in caplog.text and "documents answer" in caplog.text
