"""OPT-IN integration tests against a REAL PostgreSQL + pgvector database.

Skipped unless TEST_DATABASE_URL is set AND its database name contains "test" (so production data is never touched).
Example (Docker):  docker run -d --name avx-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=avx_test -p 5433:5432 pgvector/pgvector:pg17
                   $env:TEST_DATABASE_URL = "postgresql://postgres:pw@localhost:5433/avx_test"
Rows created here use random owner scopes and are deleted afterwards.
"""
import asyncio
import hashlib
import os
import uuid
from urllib.parse import urlsplit

import pytest

from app.db.migrate import migrate
from app.documents.chunking import Chunk
from app.documents.embeddings import normalize
from app.documents.files import Page
from app.documents.store import PostgresDocumentStore

DSN = os.environ.get("TEST_DATABASE_URL", "")
DIMS = 768

pytestmark = [
    pytest.mark.skipif(not DSN, reason="set TEST_DATABASE_URL to a disposable PostgreSQL+pgvector database to run"),
    pytest.mark.skipif(bool(DSN) and "test" not in urlsplit(DSN).path.lower(), reason="refusing: database name must contain 'test'"),
]


def owner() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def vec(hot: int) -> list[float]:
    v = [0.01] * DIMS
    v[hot] = 1.0
    return normalize(v)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def store():
    import psycopg

    migrate(DSN, DIMS)
    with psycopg.connect(DSN) as conn:
        dims = conn.execute("SELECT value FROM rag_meta WHERE key='embedding_dimensions'").fetchone()
    if int(dims[0]) != DIMS:
        pytest.skip(f"test database was migrated with {dims[0]} dimensions")
    s = PostgresDocumentStore(DSN)
    owners: list[str] = []
    s.test_owners = owners  # type: ignore[attr-defined]
    yield s
    with psycopg.connect(DSN, autocommit=True) as conn:
        for o in owners:
            conn.execute("DELETE FROM rag_documents WHERE owner_scope = %s", (o,))
            conn.execute("DELETE FROM rag_document_events WHERE owner_scope = %s", (o,))


def new_owner(store) -> str:
    o = owner()
    store.test_owners.append(o)
    return o


def make_doc(store, o, texts=("alpha text", "beta text")):
    pages = [Page(i, i + 1, t) for i, t in enumerate(texts)]
    return run(store.create_document(o, filename="t.pdf", file_type="pdf", size_bytes=10, pages=pages)), pages


def test_migration_is_idempotent_and_dimensions_recorded(store):
    assert migrate(DSN, DIMS) == []  # already applied
    assert run(store.embedding_dimensions()) == DIMS


def test_documents_are_isolated_by_owner_in_every_operation(store):
    a, b = new_owner(store), new_owner(store)
    doc, _ = make_doc(store, a)
    assert run(store.get_document(b, doc.id)) is None
    assert run(store.list_documents(b)) == []
    assert run(store.get_pages(b, doc.id)) == []
    run(store.update_document(b, doc.id, status="failed"))  # silently affects nothing
    assert run(store.get_document(a, doc.id)).status == "processing"
    assert run(store.delete_document(b, doc.id)) is False
    assert run(store.get_document(a, doc.id)) is not None


def test_vector_search_orders_by_similarity_and_filters_owner_and_status(store):
    a, b = new_owner(store), new_owner(store)
    doc, _ = make_doc(store, a)
    other, _ = make_doc(store, b)
    chunks = [Chunk(0, 1, "about cats"), Chunk(1, 2, "about dogs")]
    run(store.store_chunks(a, doc.id, chunks, [vec(1), vec(2)], "test-model", DIMS))
    run(store.store_chunks(b, other.id, chunks, [vec(1), vec(2)], "test-model", DIMS))

    hits = run(store.search(a, [doc.id, other.id], vec(2), 10))
    assert [h.text for h in hits] == ["about dogs", "about cats"]
    assert {h.document_id for h in hits} == {doc.id}  # B's identical chunks are never returned to A
    assert hits[0].similarity > hits[1].similarity and -1 <= hits[1].similarity <= 1
    assert hits[0].page_number == 2 and hits[0].filename == "t.pdf"

    run(store.update_document(a, doc.id, status="needs_review"))
    assert run(store.search(a, [doc.id], vec(2), 10)) == []  # only ready documents are searchable


def test_reindex_replaces_the_previous_version(store):
    a = new_owner(store)
    doc, _ = make_doc(store, a)
    run(store.store_chunks(a, doc.id, [Chunk(0, 1, "old")], [vec(3)], "m", DIMS))
    rec = run(store.store_chunks(a, doc.id, [Chunk(0, 1, "new"), Chunk(1, 1, "newer")], [vec(3), vec(4)], "m", DIMS))
    assert rec.index_version == 2 and rec.chunk_count == 2 and rec.status == "ready"
    assert sorted(h.text for h in run(store.search(a, [doc.id], vec(3), 10))) == ["new", "newer"]


def test_delete_cascades_to_pages_and_chunks_but_keeps_the_audit_event(store):
    import psycopg

    a = new_owner(store)
    doc, _ = make_doc(store, a)
    run(store.store_chunks(a, doc.id, [Chunk(0, 1, "x")], [vec(5)], "m", DIMS))
    run(store.add_event(a, doc.id, "deleted"))
    assert run(store.delete_document(a, doc.id)) is True
    with psycopg.connect(DSN) as conn:
        for table in ("rag_document_pages", "rag_chunks"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE document_id = %s", (uuid.UUID(doc.id),)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM rag_document_events WHERE document_id = %s", (uuid.UUID(doc.id),)).fetchone()[0] == 1


def test_updates_are_limited_to_whitelisted_columns(store):
    with pytest.raises(ValueError):
        run(store.update_document(new_owner(store), str(uuid.uuid4()), owner_scope="someone-else"))
