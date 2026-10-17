"""OPT-IN integration tests against a REAL PostgreSQL + pgvector database.

Skipped unless TEST_DATABASE_URL points at a LOCAL database whose name contains "test" as a separate word and that is not
the app's own DATABASE_URL (see tests/pg_guard.py), so production data is never touched. No DROP/TRUNCATE is ever issued.
Example (Docker):  docker run -d --name avx-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=avx_test -p 5433:5432 pgvector/pgvector:pg17
                   $env:TEST_DATABASE_URL = "postgresql://postgres:pw@localhost:5433/avx_test"
Rows created here use random owner scopes and are deleted afterwards.
"""
import asyncio
import hashlib
import uuid

import pytest

from app.db.migrate import migrate
from app.documents.chunking import Chunk
from app.documents.embeddings import normalize
from app.documents.files import Page
from app.documents.store import PostgresDocumentStore
from tests.pg_guard import safe_test_database

DSN, SKIP_REASON = safe_test_database()
DIMS = 768

pytestmark = pytest.mark.skipif(DSN is None, reason=SKIP_REASON)


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
    # The old version must be physically gone too (retention): search ignores it by version, so count the rows.
    state, rows = _doc_state(store, a, doc.id)
    assert rows == 2 and state == ("ready", 2, 2)


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


# ---- atomicity: a failed write must never leave a half-indexed or mis-marked document ----------------------------------

def _doc_state(store, o, doc_id):
    import psycopg

    with psycopg.connect(DSN) as conn:
        doc = conn.execute("SELECT status, index_version, chunk_count FROM rag_documents WHERE id = %s", (uuid.UUID(doc_id),)).fetchone()
        n = conn.execute("SELECT count(*) FROM rag_chunks WHERE document_id = %s", (uuid.UUID(doc_id),)).fetchone()[0]
    return doc, n


def test_failed_first_indexing_leaves_no_chunks_and_document_not_marked_ready(store):
    from app.documents.store import StoreUnavailable

    a = new_owner(store)
    doc, _ = make_doc(store, a)
    dup = [Chunk(0, 1, "one"), Chunk(0, 1, "same index again")]  # violates UNIQUE (document_id, index_version, chunk_index)
    with pytest.raises(StoreUnavailable):
        run(store.store_chunks(a, doc.id, dup, [vec(1), vec(2)], "m", DIMS))
    state, n = _doc_state(store, a, doc.id)
    assert n == 0 and state == ("processing", 0, 0)  # the transaction rolled back completely


def test_failed_reindex_keeps_the_previous_version_searchable(store):
    from app.documents.store import StoreUnavailable

    a = new_owner(store)
    doc, _ = make_doc(store, a)
    run(store.store_chunks(a, doc.id, [Chunk(0, 1, "good old passage")], [vec(7)], "m", DIMS))
    with pytest.raises(StoreUnavailable):
        run(store.store_chunks(a, doc.id, [Chunk(0, 1, "x"), Chunk(0, 1, "y")], [vec(8), vec(9)], "m", DIMS))
    state, n = _doc_state(store, a, doc.id)
    assert state == ("ready", 1, 1) and n == 1
    assert [h.text for h in run(store.search(a, [doc.id], vec(7), 5))] == ["good old passage"]


def test_search_with_no_matching_documents_or_invalid_ids_returns_nothing(store):
    a = new_owner(store)
    assert run(store.search(a, [], vec(1), 5)) == []
    assert run(store.search(a, ["not-a-uuid", str(uuid.uuid4())], vec(1), 5)) == []
    assert run(store.get_document(a, "not-a-uuid")) is None
    assert run(store.delete_document(a, "not-a-uuid")) is False


def test_search_is_restricted_to_the_selected_documents_of_the_same_owner(store):
    a = new_owner(store)
    d1, _ = make_doc(store, a)
    d2, _ = make_doc(store, a)
    run(store.store_chunks(a, d1.id, [Chunk(0, 1, "from document one")], [vec(10)], "m", DIMS))
    run(store.store_chunks(a, d2.id, [Chunk(0, 1, "from document two")], [vec(10)], "m", DIMS))  # identical vector: equally relevant

    assert [h.document_id for h in run(store.search(a, [d1.id], vec(10), 10))] == [d1.id]
    assert [h.document_id for h in run(store.search(a, [d2.id], vec(10), 10))] == [d2.id]
    both = run(store.search(a, [d1.id, d2.id], vec(10), 10))
    assert {h.document_id for h in both} == {d1.id, d2.id}


def test_real_pgvector_type_dimensions_operator_and_hnsw_index(store):
    """Guards against a stand-in: needs the real `vector` extension, fixed dimensions, `<=>` and the HNSW index."""
    import psycopg

    with psycopg.connect(DSN) as conn:
        ext = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        assert ext is not None, "pgvector extension is not installed"
        col = conn.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid='rag_chunks'::regclass AND attname='embedding'"
        ).fetchone()[0]
        assert col == f"vector({DIMS})"
        idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE indexname='rag_chunks_embedding_hnsw'").fetchone()[0]
        assert "USING hnsw" in idx and "vector_cosine_ops" in idx
        assert conn.execute("SELECT '[1,0]'::vector <=> '[0,1]'::vector").fetchone()[0] == pytest.approx(1.0)
        with pytest.raises(psycopg.errors.DataException):
            conn.execute("SELECT '[1,2,3]'::vector(%s)" % DIMS)
