-- Phase 7: documents, extracted pages, chunks + embeddings (pgvector), and a content-free audit trail.
-- {EMBEDDING_DIMENSIONS} is substituted by app.db.migrate from EMBEDDING_DIMENSIONS (validated integer).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE rag_documents (
    id                       uuid PRIMARY KEY,
    owner_scope              text        NOT NULL,           -- SHA-256 of the client's document scope key
    filename                 text        NOT NULL,           -- sanitized display name; never used as a path
    file_type                text        NOT NULL CHECK (file_type IN ('pdf', 'txt')),
    size_bytes               integer     NOT NULL CHECK (size_bytes >= 0),
    page_count               integer,
    char_count               integer,
    status                   text        NOT NULL CHECK (status IN ('processing', 'needs_review', 'ready', 'failed')),
    error_code               text,                            -- safe code only, never content
    privacy_required_action  text        NOT NULL DEFAULT 'none' CHECK (privacy_required_action IN ('none', 'confirm', 'redact')),
    privacy_decision         text        NOT NULL DEFAULT 'none' CHECK (privacy_decision IN ('none', 'not_required', 'confirmed', 'redacted')),
    privacy_categories       jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- category -> count, no values
    privacy_decided_at       timestamptz,
    embedding_model          text,
    embedding_dimensions     integer,
    chunk_count              integer     NOT NULL DEFAULT 0,
    index_version            integer     NOT NULL DEFAULT 0,
    uploaded_at              timestamptz NOT NULL DEFAULT now(),
    processed_at             timestamptz
);
CREATE INDEX rag_documents_owner_idx ON rag_documents (owner_scope, uploaded_at DESC);

-- Extracted text, kept server-side so a privacy decision can be made before anything is embedded.
CREATE TABLE rag_document_pages (
    document_id  uuid    NOT NULL REFERENCES rag_documents (id) ON DELETE CASCADE,
    page_index   integer NOT NULL,
    page_number  integer,                                    -- 1-based PDF page; NULL for TXT
    text         text    NOT NULL,
    PRIMARY KEY (document_id, page_index)
);

CREATE TABLE rag_chunks (
    id              uuid    PRIMARY KEY,
    document_id     uuid    NOT NULL REFERENCES rag_documents (id) ON DELETE CASCADE,
    owner_scope     text    NOT NULL,                        -- duplicated so retrieval filters on the owner directly
    index_version   integer NOT NULL,
    chunk_index     integer NOT NULL,
    page_number     integer,
    text            text    NOT NULL,
    embedding       vector({EMBEDDING_DIMENSIONS}) NOT NULL,
    embedding_model text    NOT NULL,
    UNIQUE (document_id, index_version, chunk_index)
);
CREATE INDEX rag_chunks_owner_doc_idx ON rag_chunks (owner_scope, document_id);
-- HNSW needs no training data (unlike IVFFlat), so it works from the first row. Cosine distance matches our
-- normalised vectors.
CREATE INDEX rag_chunks_embedding_hnsw ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- Audit trail of privacy decisions and lifecycle events. Holds codes and category counts, never document text.
-- No foreign key, so the record of a deletion survives the deletion.
CREATE TABLE rag_document_events (
    id           bigserial   PRIMARY KEY,
    document_id  uuid        NOT NULL,
    owner_scope  text        NOT NULL,
    event        text        NOT NULL,
    details      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rag_document_events_doc_idx ON rag_document_events (document_id, created_at);

CREATE TABLE rag_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO rag_meta (key, value) VALUES ('embedding_dimensions', '{EMBEDDING_DIMENSIONS}');
