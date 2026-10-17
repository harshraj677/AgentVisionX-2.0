"""Phase 8.9: readiness (/api/ready) and crash recovery of documents left in 'processing'.
In-memory store with a movable clock; the same SQL semantics are checked against real PostgreSQL in
tests/test_documents_postgres.py. No network, no provider calls."""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from app.core.errors import AppError
from tests.doc_fakes import SCOPE_A, SCOPE_B
from tests.test_documents_api import POLICY_TXT, build, ready_doc, upload

OWNER_A = hashlib.sha256(SCOPE_A.encode("ascii")).hexdigest()  # what the API stores as the owner
OWNER_B = hashlib.sha256(SCOPE_B.encode("ascii")).hexdigest()
H_A = {"X-Document-Scope": SCOPE_A}
H_B = {"X-Document-Scope": SCOPE_B}
STALE = 900


class Clock:
    def __init__(self):
        self.t = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


def with_clock(store):
    store.clock = Clock()
    return store.clock


async def crashed_doc(store, owner=OWNER_A):
    """A document as a crashed process leaves it: status 'processing' with a lease that is never released."""
    from app.documents.files import Page

    return await store.create_document(owner, filename="crashed.txt", file_type="txt", size_bytes=10, pages=[Page(0, None, POLICY_TXT)])


# ---- readiness --------------------------------------------------------------------------------------------------

def test_ready_reports_each_database_state_without_details():
    client, store, _, provider = build()
    assert client.get("/api/ready").json() == {"status": "ok", "database": "ok"}
    store.ping_result = "not_migrated"
    assert client.get("/api/ready").json() == {"status": "degraded", "database": "not_migrated"}
    store.fail = True
    r = client.get("/api/ready")
    assert r.status_code == 200 and r.json() == {"status": "degraded", "database": "unavailable"}
    store.fail, store.configured = False, False
    assert client.get("/api/ready").json() == {"status": "ok", "database": "not_configured"}
    assert provider.calls == []  # readiness never touches an AI provider


def test_liveness_does_not_depend_on_the_database():
    client, store, *_ = build()
    store.fail = True
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# ---- stale recovery -----------------------------------------------------------------------------------------------

def test_a_stale_processing_document_becomes_failed_and_retryable_without_deleting_anything():
    client, store, *_ = build()
    clock = with_clock(store)
    doc = asyncio.run(crashed_doc(store))
    clock.advance(STALE + 1)
    docs = client.get("/api/documents", headers=H_A).json()["documents"]
    assert [(d["id"], d["status"], d["error_code"]) for d in docs] == [(doc.id, "failed", "indexing_interrupted")]
    assert store.pages[doc.id], "extracted pages must be kept"
    assert [e[2] for e in store.events if e[1] == doc.id].count("indexing_interrupted") == 1
    # idempotent: a second list does not repeat the transition or the audit event
    client.get("/api/documents", headers=H_A)
    assert [e[2] for e in store.events if e[1] == doc.id].count("indexing_interrupted") == 1


def test_an_active_indexing_job_is_never_recovered():
    client, store, *_ = build()
    clock = with_clock(store)
    doc = asyncio.run(crashed_doc(store))
    clock.advance(STALE - 60)  # still within the lease
    assert client.get("/api/documents", headers=H_A).json()["documents"][0]["status"] == "processing"
    assert client.get(f"/api/documents/{doc.id}", headers=H_A).json()["status"] == "processing"
    clock.advance(30)  # slow but still inside the window
    assert client.get("/api/documents", headers=H_A).json()["documents"][0]["status"] == "processing"


def test_recovery_only_touches_the_requesting_scope():
    client, store, *_ = build()
    clock = with_clock(store)
    doc = asyncio.run(crashed_doc(store, OWNER_A))
    clock.advance(STALE + 1)
    assert client.get("/api/documents", headers=H_B).json()["documents"] == []
    assert store.docs[doc.id][1].status == "processing"  # B's request did not recover A's document
    assert client.get(f"/api/documents/{doc.id}", headers=H_B).status_code == 404
    assert client.get(f"/api/documents/{doc.id}", headers=H_A).json()["status"] == "failed"


def test_retry_after_a_crash_works_for_a_reviewed_document_and_never_skips_privacy_review():
    client, store, *_ = build()
    clock = with_clock(store)
    doc = asyncio.run(crashed_doc(store))  # privacy decision recorded, then indexing crashed
    asyncio.run(store.update_document(OWNER_A, doc.id, privacy_decision="not_required"))
    clock.advance(STALE + 1)
    r = client.post(f"/api/documents/{doc.id}/reindex", headers=H_A)  # recovers first, then re-indexes
    assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    # a document that crashed before its privacy review finished must NOT be indexable by retry
    unreviewed = asyncio.run(crashed_doc(store))
    clock.advance(STALE + 1)
    r = client.post(f"/api/documents/{unreviewed.id}/reindex", headers=H_A)
    assert r.status_code == 409 and r.json()["error"]["code"] == "document_not_reindexable"
    assert store.chunks.get(unreviewed.id) is None


def test_stale_window_never_drops_below_the_worst_case_duration_of_a_healthy_job():
    client, *_ = build(embedding_timeout_seconds=120, max_chunks_per_document=500, embedding_batch_size=10, indexing_stale_seconds=300)
    assert client.app.state.documents.stale_after_seconds >= 50 * 125  # 50 batches x (timeout + 5s)


# ---- lease / concurrency --------------------------------------------------------------------------------------------

def test_reindex_is_refused_while_a_live_job_holds_the_lease_and_allowed_once_it_is_stale():
    client, store, *_ = build()
    clock = with_clock(store)
    doc = ready_doc(client)
    store.leases[doc["id"]] = clock()  # another worker is indexing it right now
    r = client.post(f"/api/documents/{doc['id']}/reindex", headers=H_A)
    assert r.status_code == 409 and r.json()["error"]["code"] == "document_indexing_in_progress"
    clock.advance(STALE + 1)
    assert client.post(f"/api/documents/{doc['id']}/reindex", headers=H_A).status_code == 200


def test_two_simultaneous_reindex_requests_index_the_document_once():
    client, store, embedder, _ = build()
    doc = ready_doc(client)
    before = len(embedder.calls)
    svc = client.app.state.documents
    original = embedder.embed

    async def slow(texts, purpose):
        await asyncio.sleep(0.05)  # keeps the first job running while the second arrives
        return await original(texts, purpose)

    embedder.embed = slow

    async def both():
        return await asyncio.gather(
            svc.reindex(OWNER_A, doc["id"], "r1"), svc.reindex(OWNER_A, doc["id"], "r2"), return_exceptions=True
        )

    results = asyncio.run(both())
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1 and errors[0].code == "document_indexing_in_progress", results
    assert len(embedder.calls) - before == 1
    assert doc["id"] not in store.leases  # lease released after the successful job


def test_an_unexpected_error_after_upload_leaves_a_failed_retryable_document_and_no_lease():
    client, store, embedder, _ = build()

    async def boom(texts, purpose):
        raise RuntimeError("unexpected")

    embedder.embed = boom
    r = upload(client, POLICY_TXT.encode())
    assert r.status_code == 500
    docs = client.get("/api/documents", headers=H_A).json()["documents"]
    assert len(docs) == 1 and docs[0]["status"] == "failed" and docs[0]["error_code"] == "indexing_interrupted"
    assert docs[0]["id"] not in store.leases


# ---- Phase 8.10 reliability ---------------------------------------------------------------------------------------

def test_a_failed_audit_event_does_not_turn_a_successful_delete_into_an_error():
    from app.documents.store import StoreUnavailable

    client, store, *_ = build()
    doc = ready_doc(client)

    async def broken_event(*args, **kwargs):
        raise StoreUnavailable()

    store.add_event = broken_event
    r = client.delete(f"/api/documents/{doc['id']}", headers=H_A)
    assert r.status_code == 204
    assert client.get(f"/api/documents/{doc['id']}", headers=H_A).status_code == 404  # really deleted


def test_the_document_store_is_closed_on_application_shutdown():
    client, store, *_ = build()
    assert store.closed is False
    with client:  # runs the lifespan: startup, then shutdown
        pass
    assert store.closed is True


def test_the_connection_pool_is_created_once_even_under_concurrent_first_use():
    import threading

    from app.documents.store import PostgresDocumentStore

    s = PostgresDocumentStore("postgresql://nobody:x@127.0.0.1:1/avx_test")  # unreachable; the pool opens in the background
    pools = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        pools.append(s._get_pool())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    try:
        assert len({id(p) for p in pools}) == 1
    finally:
        s.close()
