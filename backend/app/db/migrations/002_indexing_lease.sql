-- Phase 8.9: a lease for indexing jobs. NULL = no job running. A job sets it when it starts (atomically, so two requests
-- cannot index the same document) and clears it when it finishes. A lease older than INDEXING_STALE_SECONDS belongs to a
-- crashed job: that is the only thing recovery acts on. Additive and nullable; existing rows are unaffected.
ALTER TABLE rag_documents ADD COLUMN indexing_started_at timestamptz;
