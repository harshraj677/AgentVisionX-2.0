# AgentVision X

A privacy-aware AI gateway (MVP): a React Native / Expo app talks to a FastAPI backend, which calls Gemini. API keys live only on the backend.

> **Security status (Phase 2):** every prompt is scanned locally on the backend (Microsoft Presidio + custom rules) *before* anything is sent to Gemini, and a policy can allow, warn, redact or block it. This is tested (see "Testing") but is **not a guarantee**: detection is pattern- and model-based and can miss things (unusual name formats, unlabeled passwords, non-English text). Don't rely on it as your only protection, and don't describe the app as "privacy-safe".

## What works now

- Mobile: Home, AI chat (bubbles, loading, error + retry, new conversation), local conversation history, Settings with live backend status and an editable backend URL, request-details sheet (provider, model, tokens, latency, request ID).
- Privacy engine: scan, review (highlight / redact / edit / re-scan / send / cancel) and server-side enforcement. See "Privacy engine".
- Prompt optimizer: local rule-based suggestions, side-by-side comparison, choose original / optimized / your own edit. See "Prompt optimizer".
- Documents (RAG): upload PDF/TXT, privacy review before indexing, PostgreSQL + pgvector retrieval, answers citing document passages. See "Documents (RAG)".
- Research: one Tavily web search per question, an answer that cites the returned sources, citation and link validation. See "Research".
- Response privacy: every AI reply is scanned locally before it reaches the app; secrets are redacted by default. See "Response privacy".
- Model router: local task classification, five routing policies, bounded fallback, routing explanations in every response. See "Model routing".
- Backend: `GET /health` (liveness), `GET /api/ready` (database readiness), `GET /api/router/models`, `POST /api/chat`, `POST /api/privacy/scan`, `POST /api/privacy/redact`, `POST /api/optimize`, `POST /api/research`, the `/api/documents` endpoints, request IDs, validation, size/timeout limits, provider abstraction, real Gemini adapter (`google-genai`).
- Reliability: the Settings screen separates "backend unreachable" from "backend up but documents database unavailable", validates and normalises the Backend URL, and documents whose indexing job crashed are recovered automatically (see "Stale indexing recovery").
- Token counts come from Gemini's `usage_metadata`. If missing, the app shows "Unavailable". Nothing is estimated.
- Voice is clearly marked **Coming soon**. The OpenAI adapter only returns a "not configured / not implemented" error.

## Layout

```
agentvision-x/
├── backend/   FastAPI (app/api, core, schemas, services, providers) + tests
└── mobile/    Expo Router app (app, components, constants, hooks, services, types)
```

## Architecture

```
 Phone (Expo Go)                      PC (Windows)
┌──────────────────┐   HTTP, LAN   ┌────────────────────────────────────────────┐
│ React Native app │ ────────────▶ │ FastAPI backend  (uvicorn, port 8000)      │
│ Expo Router      │  X-Document-  │  privacy engine ─ input + response scans   │
│ no API keys      │  Scope header │  prompt optimizer, model router            │      ┌────────────┐
└──────────────────┘               │  chat / research / documents services ─────┼────▶ │ Gemini API │
                                   │                                            │      │ Tavily API │
                                   │  document store (psycopg pool)             │      └────────────┘
                                   └───────────────┬────────────────────────────┘
                                                   ▼
                                    PostgreSQL 17 + pgvector (Docker, 127.0.0.1:5432)
                                    documents, pages, chunks + embeddings (HNSW), audit events
```

- **Mobile** (`mobile/`): Expo Router screens (`app/`), reusable `components/`, `hooks/` (conversations, health, prompt review), `services/` (API client with timeouts and response validation, config, storage, document scope) and `types/`. It holds no provider keys; the Backend URL is `EXPO_PUBLIC_API_URL` or the value saved in Settings.
- **Backend** (`backend/app/`): `api/routes.py` (HTTP), `services/` (chat, documents, research, privacy, optimizer), `privacy/` (Presidio + custom rules, input and response policies), `router/` (task classification, policies, fallback), `providers/` (Gemini adapter), `documents/` (file extraction, chunking, embeddings, the PostgreSQL store, grounding), `research/` (Tavily) and `db/` (SQL migrations and the runner).
- **Database**: PostgreSQL with the `vector` extension. Schema changes are numbered SQL files in `backend/app/db/migrations/`, applied once each by `python -m app.db.migrate`.
- **Secrets** live only in `backend/.env` (git-ignored). The mobile app never receives them.

## Quick start (Windows, PowerShell)

Prerequisites: Python 3.11+, Node.js 20+, VS Code, and Expo Go on your phone (or Android Studio's emulator).

### 1. Backend

```powershell
cd agentvision-x\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt      # includes Presidio and the small spaCy model (en_core_web_sm)
Copy-Item .env.example .env
notepad .env                         # paste your key after GEMINI_API_KEY=  (https://aistudio.google.com/apikey)
python -m pytest                     # should pass
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Test it (new terminal):

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/api/chat -Method Post -ContentType "application/json" -Body '{"messages":[{"role":"user","content":"Say hello in one sentence"}]}'
```

The first start takes a few seconds while Presidio's language model loads. `/health` then reports `"privacy": {"engine": "presidio", "ready": true}`.

Interactive docs: http://localhost:8000/docs

### 2. Mobile

```powershell
cd agentvision-x\mobile
npm install
Copy-Item .env.example .env
notepad .env                         # set EXPO_PUBLIC_API_URL (see table below)
npx expo start -c
```

Press `a` for the Android emulator, or scan the QR code with Expo Go.

### Which backend URL?

| Where the app runs | `EXPO_PUBLIC_API_URL` |
|---|---|
| Android emulator | `http://10.0.2.2:8000` |
| iOS simulator | `http://localhost:8000` |
| Physical phone (same Wi-Fi) | `http://<PC IP>:8000` - find it with `ipconfig` (IPv4 address of your Wi-Fi adapter) |

You can also change the URL inside the app: **Settings → Backend URL → Save & test**. It's remembered on the device. After editing `.env`, restart with `npx expo start -c`.

For a physical phone, the backend must be started with `--host 0.0.0.0` (as above), and Windows Firewall must allow Python on private networks (allow the prompt, or run once as admin: `New-NetFirewallRule -DisplayName "AgentVision API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private`). Test from the phone's browser: `http://<PC IP>:8000/health`.

### Starting everything safely (each time)

1. **Database first** (only needed for Documents): make sure Docker Desktop is running and `agentvision-pg` is started (`docker start agentvision-pg`; with `--restart unless-stopped` it starts with Docker). If the database comes up after the backend, the backend reconnects by itself within seconds.
2. **Backend**, from `backend\`, using the project's own interpreter (no activation script needed): `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000`. After pulling new code run `.\.venv\Scripts\python.exe -m app.db.migrate` once (see "Database migrations").
3. **Expo**, from `mobile\`: `npx expo start -c` (the `-c` is only needed after changing `.env`). To make sure Expo advertises the Wi-Fi address and not a virtual adapter (e.g. WSL), set `$env:REACT_NATIVE_PACKAGER_HOSTNAME = "<PC IP>"` first.
4. **Stop**: Ctrl+C in each terminal; `docker stop agentvision-pg` if you want the database off.

### Checking the phone-to-backend connection

Do these in order; the first one that fails shows where the problem is.

1. Phone on the **same Wi-Fi** as the PC, mobile data off.
2. In the phone's **browser**, open `http://<PC IP>:8000/health`. Expected: JSON with `"status":"ok"`. If it does not load, the problem is the network, not the app: Windows Firewall (rule above; Windows may classify the Wi-Fi as *Public*), or router/AP "client isolation" (common on guest and some ISP/mesh networks), or the wrong IP.
3. Open `http://<PC IP>:8000/api/ready`. Expected: `{"status":"ok","database":"ok"}` (see the next section for other values).
4. In the app: **Settings → Backend URL** (you may type `192.168.1.23:8000`; `http://` is added), **Save & test**, then **Check connection** at any time. Settings shows backend status and the documents database state.

### `/health` versus `/api/ready`

| Endpoint | Question it answers | Depends on |
|---|---|---|
| `GET /health` | Is the server process up? (liveness) Which providers have keys, is the privacy engine loaded, is a database *configured*? | Nothing external. Never touches the database or an AI provider. `database_configured: true` only means `DATABASE_URL` is set, **not** that the database is reachable. |
| `GET /api/ready` | Can the backend actually reach its documents database? (readiness) | One cheap check (`to_regclass` on `rag_meta`, 3-second connect timeout, on its own short connection). Never calls an AI provider. |

`/api/ready` always returns HTTP 200 while the server is up (chat, privacy and research work without a database), as `{ "status": "ok" | "degraded", "database": ... }`:

| `database` | Meaning | What to do |
|---|---|---|
| `ok` | Reachable and migrated | Nothing |
| `not_configured` | `DATABASE_URL` is empty (`status` is still `ok`) | Set it, see "Documents (RAG)" |
| `not_migrated` | Reachable, but the tables are missing | `python -m app.db.migrate` |
| `unavailable` | Cannot connect (down, wrong credentials, wrong port) | Start PostgreSQL / Docker; the backend reconnects by itself |

No connection string, credentials or driver error text are ever returned.

## Privacy engine

**Flow.** In the app, pressing Send first calls `POST /api/privacy/scan` (local: the text goes only to *your* backend). If nothing is found, the message is sent. Otherwise a **Privacy review** screen shows the detected categories with highlights and lets you **Redact**, **Edit**, **Re-scan**, **Send** (only if permitted) or **Cancel**. Edited or redacted text must be re-scanned before Send is enabled. Independently, `POST /api/chat` re-scans every *user* message on the server and refuses before any provider is called, so a modified client cannot bypass it.

**Decisions** (the strictest finding wins):

| Decision | Examples | Can the user send? |
|---|---|---|
| allow | nothing found, or category set to `allow` | yes |
| warn | person name, IP address | yes, after confirming ("Send anyway") |
| redact | email, phone, IBAN, PAN | no, until redacted or edited and re-scanned clean |
| block | API keys, private keys, passwords/secrets, credit cards, SSN, Aadhaar, passport | no, until redacted or edited and re-scanned clean |

Redaction replaces each value with its category, e.g. `[EMAIL_ADDRESS]`. Blocked content is never forwarded as-is; the redacted prompt is a new prompt and is re-scanned.

**Detection.** Presidio (spaCy `en_core_web_sm`) for names, emails, phones, cards, SSN, IBAN, IP and similar, plus custom rules in `backend/app/privacy/recognizers.py`: API keys (OpenAI-style, Google, AWS, GitHub, Slack, Stripe, JWT, Bearer), PEM private keys, `password/secret/token = value` assignments, email and phone formats (international, US, Indian mobile), grouped Aadhaar (`1234 5678 9012`) and PAN. DATE/LOCATION/ORGANIZATION/URL are deliberately not scanned (too noisy).

**Configuring the policy** in `backend/.env` (JSON; defaults live in `app/privacy/policy.py`):

```
PRIVACY_POLICY={"EMAIL_ADDRESS":"warn","PERSON":"allow","IP_ADDRESS":"block"}
PRIVACY_DEFAULT_ACTION=warn      # for categories without an entry
PRIVACY_MIN_SCORE=0.4            # detection confidence threshold
```

An invalid action fails at startup. Restart uvicorn after editing.

**Behaviour to know about**
- Fail closed: if the privacy engine errors, `/api/chat` returns `privacy_engine_unavailable` (503) and nothing is sent.
- Chat errors: `privacy_blocked` / `privacy_redaction_required` (403) and `privacy_confirmation_required` (409, needs `privacy_ack: true`). Error details list category and offsets only, never values.
- Logs and the detector cache hold category names, counts, offsets and request IDs only; the cache is keyed by a SHA-256 hash.
- Only *user* messages are scanned. Assistant replies are not checked yet (response-side checks are still to do).
- Known limits: English only; `PERSON` is the noisiest detector (so it only warns); unlabeled secrets (e.g. "my password is hunter2" without `:` or `=`) and Aadhaar without spaces are not detected. The mobile app sends `privacy_ack: true` after the user's own review, so the server-side warn gate mainly protects other clients.
- Prompt optimization is not implemented. When it is, it must run after this scan and never send raw prompts to an external service.

## Prompt optimizer

**What it does.** After your prompt passes the privacy check, the backend suggests a shorter version using deterministic rules (no AI model, no network): it removes greetings and closing thanks, filler politeness ("could you please", "I was wondering if you could"), filler words, accidental repeated words, repeated sentences and extra blank lines, and replaces a few wordy phrases ("in order to" → "to"). Code blocks, quoted text, URLs, emails, redaction placeholders and long opaque tokens (keys, hashes) are masked before any rule runs and are never modified.

**Flow in the app.** Send → privacy scan of your prompt (review if needed) → if the optimizer found something, a **Compare prompts** screen shows *Original* and *Optimized* (side by side on wide screens, stacked on phones) plus an *Edit it yourself* box, with the list of changes and per-version estimates. Pick one and tap **Check & send**: the exact chosen text is scanned again, and only if policy allows it (or you confirm a warning, or fix it in the privacy review) is it sent. **Cancel** sends nothing and keeps your draft. If no safe improvement exists, the prompt is sent as usual with no extra screen. Turn the optimizer off in Settings.

**Guarantees (all covered by tests)**
- `/api/optimize` calls no AI provider and makes no network request; it only suggests.
- Whatever version is finally sent goes through `/api/chat`, which enforces the privacy policy again on the exact text. Block / redact are never bypassed, whichever version you pick.
- Preservation check: if a rewrite would lose a number, a quoted/code/URL span, or any word that no rule is allowed to remove (e.g. "not", "only", "JSON", "under"), the original is returned unchanged with `preservation.verified = false`. This is a heuristic safety net, **not a proof of equivalent meaning**: always compare before sending (some rewrites turn a question into a command, e.g. "Could you kindly help?" becomes "Help?").
- Logs record rule names, counts and token estimates only, never prompt text.

**Token numbers are estimates.** `tokens.basis` is always `"estimate"`, with `method` saying how: `tiktoken:o200k_base` (OpenAI's tokenizer; Gemini counts differ) or `heuristic:chars/4` if the tokenizer can't load. The first use downloads tiktoken's public vocabulary file (no prompt text is sent); offline, the heuristic is used. Real token usage is only known after sending, from the provider (`metadata.usage`), and only for the version you sent. **Savings have not been measured** against the provider: the app shows "estimated" savings and never states a measured saving. Measuring it properly needs provider token-count calls on both versions, which would send text before approval, so it is deferred.

**Extending.** Add a rule in `backend/app/optimizer/rules.py` (declare which words it may delete) and a test. A future model-based optimizer must implement `PromptOptimizer` (`app/optimizer/optimizer.py`) and may only receive text that has already passed the privacy policy.

## Documents (RAG)

### Setup (Windows)

1. **PostgreSQL with pgvector.** Easiest is Docker Desktop. Use a strong password (generate one; do not reuse the example), a **named volume** so data survives container removal, and bind the port to `127.0.0.1` so the database is not reachable from your LAN:
   ```powershell
   $env:POSTGRES_PASSWORD = "<strong password>"      # only in this terminal session
   docker run -d --name agentvision-pg --restart unless-stopped -e POSTGRES_PASSWORD -e POSTGRES_DB=agentvision -p 127.0.0.1:5432:5432 -v agentvision-pg:/var/lib/postgresql/data pgvector/pgvector:pg17
   Remove-Item Env:POSTGRES_PASSWORD
   ```
   (`-e POSTGRES_PASSWORD` without a value passes the variable through, so the password does not appear in the command line.) Tested with PostgreSQL 17.11 and pgvector 0.8.7. You can also install PostgreSQL and pgvector natively; note that a Windows pgvector build compiled for a newer CPU than yours can crash the server on `CREATE EXTENSION vector`, which the official Docker image avoids. Docker Desktop keeps its data in a virtual disk (its location is a Docker setting); keep an eye on the free space of that drive.
   - **Never use the test database (`avx_test`) for real documents.**
2. In `backend/.env` (git-ignored; never put the real URL in a tracked file): `DATABASE_URL=postgresql://postgres:<password>@127.0.0.1:5432/agentvision` and `GEMINI_API_KEY=<your key>` (also used for embeddings).
3. Create the tables: `python -m app.db.migrate` (see "Database migrations"). The first migration fixes the vector size to `EMBEDDING_DIMENSIONS` (default 768); changing it later needs a new migration and re-indexing.
4. Restart uvicorn. `/health` shows `documents.database_configured` / `embedding_configured` (configured, not proven reachable); `/api/ready` shows whether the database is actually reachable.

### Database migrations

Migrations are the numbered files in `backend/app/db/migrations/`. The runner applies each pending file once, in order, inside a transaction together with its `schema_migrations` record, so a failing migration leaves **no partial schema and no record** and can simply be re-run after fixing the cause.

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.db.migrate --status   # lists pending migrations, changes nothing
.\.venv\Scripts\python.exe -m app.db.migrate            # applies them; running it again prints "nothing (up to date)"
```

| File | Adds |
|---|---|
| `001_documents.sql` | `vector` extension; `rag_documents`, `rag_document_pages`, `rag_chunks` (`vector(EMBEDDING_DIMENSIONS)` + HNSW cosine index), `rag_document_events`, `rag_meta` |
| `002_indexing_lease.sql` | nullable `rag_documents.indexing_started_at` (the indexing lease, see below). Additive; existing rows are untouched |

A new environment runs `001` then `002`. An existing database upgrades with a single `python -m app.db.migrate` after updating the code. **Run it before starting the updated backend**: without `002`, document operations fail with `documents_database_unavailable`. Both paths (fresh install and upgrade of a database that already holds documents) were checked, including repeat runs.

### Who can see a document (development-mode scope)

There are no user accounts yet. Each app install generates a random 256-bit **scope key** (stored in the app) and sends it as `X-Document-Scope`. The server stores only its SHA-256 and filters **every** read, search, privacy decision, re-index and delete by it, so a document ID alone never grants access (another scope gets `404 document_not_found`, as if it didn't exist). This is a bearer secret, **not authentication**: anyone with the key has access, and clearing app data loses access (documents remain on the server until deleted). It is not production-grade multi-user isolation.

### Upload and indexing

`POST /api/documents` (multipart `file`, with `X-Document-Scope`):

1. Size limit `DOCUMENT_MAX_UPLOAD_BYTES` (checked from `Content-Length` before the body is parsed; uploads without it get `411`).
2. Type from **content**: PDF must contain `%PDF-`; TXT must be UTF-8 without binary signatures/NUL bytes; the extension must agree (`.pdf`/`.txt`). Everything else: `415 document_unsupported_type`.
3. The client filename is sanitized to a display name (last path component, safe characters, 100 chars) and is never used as a path. Files are processed in memory; the app writes no files. (Starlette may spool large multipart bodies to an OS temp file, which it deletes after the request.)
4. Text extraction with `pypdf` (parsing only, nothing in the PDF is executed) with page (`DOCUMENT_MAX_PAGES`), text (`DOCUMENT_MAX_EXTRACTED_CHARS`) and time (`DOCUMENT_EXTRACTION_TIMEOUT_SECONDS`) limits. Encrypted, malformed and textless files are rejected (`document_encrypted`, `document_malformed`, `document_no_text`). **No OCR**: scanned PDFs have no text. Note: the timeout stops waiting, but a worker thread cannot be killed; the page/size caps bound it.
5. **Ingestion privacy gate** (input `PRIVACY_POLICY`, Presidio + custom rules) on all extracted text. If nothing is found, the document is indexed. Otherwise it stops at `status: "needs_review"` with only category names/counts, and **nothing has been sent to the embedding provider**. The user must decide via `POST /api/documents/{id}/privacy-decision`:
   - `{"decision":"redact"}`: every detected item becomes `[CATEGORY]`; the redacted text is re-scanned, must come back clean, and **replaces** the stored original.
   - `{"decision":"confirm"}`: index as-is. Only allowed when every finding is warn-level (e.g. names); redact/block-level content (emails, keys, cards…) can only be redacted.

   Opening the upload screen is not consent; there is no bypass flag. Decisions are recorded in `rag_document_events` (event, category counts, time; never text).
6. Chunking: page-aware, sentence/paragraph-preferring, `CHUNK_SIZE_CHARS` with `CHUNK_OVERLAP_CHARS` overlap of whole sentences, at most `MAX_CHUNKS_PER_DOCUMENT`. A chunk never spans pages, so each has one page number (TXT has none).
7. Embeddings: `EMBEDDING_MODEL` (default `gemini-embedding-001`, task type `RETRIEVAL_DOCUMENT`), vectors L2-normalised and their length checked against `EMBEDDING_DIMENSIONS`. Failures (`embedding_rate_limited`, `embedding_timeout`, …) mark the document `failed` with that code and store no chunks; `POST /api/documents/{id}/reindex` retries. Missing credentials reject the upload up front (`503 embedding_not_configured`).
8. Chunks, embeddings, model name and an `index_version` are stored in one transaction; re-indexing replaces the previous version atomically.

Other endpoints: `GET /api/documents`, `GET /api/documents/{id}`, `DELETE /api/documents/{id}` (deletes the document, its stored text, chunks and embeddings via `ON DELETE CASCADE`; only a content-free audit event remains; if only that audit write fails the delete still succeeds).

### Document states and stale indexing recovery

`processing` (indexing is running) → `ready` | `needs_review` (waiting for your privacy decision) | `failed` (with a safe `error_code`; retry with `POST /api/documents/{id}/reindex`, the app's *Retry indexing*).

**Lease.** Every indexing job holds a lease in `rag_documents.indexing_started_at`. It is taken atomically (one conditional `UPDATE`) when the document is uploaded, or when a privacy decision or re-index starts, and released when the job finishes or fails. A second request for a document whose lease is still live gets `409 document_indexing_in_progress`, so two requests (e.g. a double tap) cannot index the same document twice.

**Recovery.** If the backend crashes or is killed mid-indexing, the lease is never released. A lease older than the stale timeout is treated as dead: whenever documents are listed, fetched or retried, that owner's `processing` documents with an expired lease are marked `failed` with `error_code: "indexing_interrupted"` (and an audit event), so the app shows *Retry indexing*. Recovery only changes the status: **nothing is deleted** (text, pages and any existing chunks stay), it acts only on the requesting scope's documents, and it is idempotent. A job that is still running keeps a fresh lease and is never recovered.

**Timeout.** `INDEXING_STALE_SECONDS` (default `900`, minimum `120`). The backend never uses less than the worst-case duration of a healthy job, `ceil(MAX_CHUNKS_PER_DOCUMENT / EMBEDDING_BATCH_SIZE) × (EMBEDDING_TIMEOUT_SECONDS + 5) + 60` seconds (620 with the defaults), so raising the embedding timeout or chunk limit automatically lengthens the window.

Limits: a document that crashed *before its privacy review finished* has no recorded decision and cannot be retried (that would skip the review): delete it and upload again. If the backend crashes during a *re*-index, a retry is refused with `document_indexing_in_progress` until the stale timeout passes. A re-index that fails marks a previously `ready` document `failed` until the retry succeeds.

### Asking

`POST /api/documents/ask`:
```json
{ "question": "How many days of paid leave do employees get?", "document_ids": ["<uuid>"], "privacy_ack": true }
```
Pipeline: input privacy gate on the question (same as chat, before any embedding) → every document must be in your scope (`404 document_not_found`) and `ready` (`409 document_not_ready`) → route plan (task type `documents`, mappable in `ROUTER_TASK_MODELS`) → embed the question (`RETRIEVAL_QUERY`) → pgvector cosine search **filtered by owner and document IDs** (`3 × RAG_TOP_K` candidates) → drop passages below `RAG_MIN_SIMILARITY`, de-duplicate near-identical ones, keep `RAG_TOP_K`, cap at `RAG_MAX_CONTEXT_CHARS` → if nothing is left: **`404 documents_insufficient_evidence` and no model call** (no general-knowledge answers dressed up as document answers) → one routed generation (rules in the system instruction; passages as numbered, untrusted `<<<PASSAGE Dn>>>` blocks with instruction-like lines removed) → response privacy scan → citation checks.

Response (abridged):
```json
{
  "answer": "Employees receive 20 days of paid leave per year [D1].",
  "evidence_status": "cited",
  "passages": [{ "ref": "D1", "document_id": "…", "filename": "handbook.pdf", "chunk_id": "…", "chunk_index": 3,
                 "page_number": 2, "similarity": 0.71, "excerpt": "Annual leave policy…", "flags": [], "cited": true }],
  "invalid_citations_removed": 0, "unverified_links_removed": 0,
  "metadata": { "routing": { "task_type": "documents", "…": "…" }, "response_privacy": { "status": "clean" },
                "retrieval": { "embedding_model": "gemini-embedding-001", "top_k": 6, "candidates": 18, "below_threshold": 12,
                               "deduplicated": 1, "used": 5, "min_similarity": 0.45, "best_similarity": 0.71,
                               "context_chars": 4200, "flagged_passages": 0 }, "…": "…" }
}
```
`[Dn]` references that are not among the retrieved passages are **removed, never re-mapped**; links that do not appear in a passage become `[link removed]`. `evidence_status` is `cited` (references real retrieved passages, which is not proof each claim is right), `uncited`, or `insufficient_evidence` (the model said the passages were not enough). Excerpts go through the response privacy policy (`null` if withheld). Similarity values come from pgvector; `RAG_MIN_SIMILARITY` is an uncalibrated starting point, so tune it on your own documents.

### In the app

Home → Documents: upload (PDF/TXT picker), list with status (Ready / Needs privacy review / Failed with a readable reason), **Redact & index** or **Index as-is** (only when allowed, after a confirmation dialog), Retry indexing, Delete (with confirmation), **Ask** per document or across all ready ones. The Ask screen uses the same privacy review and optimizer comparison as chat, shows tappable `[Dn]` references, expandable passage excerpts with file name and page, response privacy notices and request details (including retrieval stats). Upload progress is shown as an indeterminate state (React Native `fetch` reports no upload progress). *Stop waiting* only stops the app from waiting. Answers are not saved to chat history, and the Ask screen re-reads the document list from the server, so deleted documents are not shown as sources.

### Security notes and limitations

- Stored document text and **embeddings are sensitive data**: embeddings can leak information about the source text. Protect the database like the documents themselves. Text awaiting a privacy decision is stored server-side until you decide or delete.
- With `confirm`, the text (e.g. names) is sent to the embedding provider and, as passages, to the answering model.
- Isolation is enforced in SQL (`owner_scope` on every query, plus the selected document IDs and `status = 'ready'` and the current `index_version` on search). It is covered by opt-in tests against a real database.
- The HNSW index needs no minimum data. With filtering by owner/document, an approximate index scan can return fewer than `top_k` rows for very selective filters; small per-user corpora are usually served by the `owner_scope` B-tree instead.
- Only UTF-8 TXT; no OCR, DOCX or images. Injection defenses are heuristic (see Research).

**Real-database tests (opt-in).** `tests/test_documents_postgres.py` (store: isolation, similarity ordering, selected-document filter, re-index replacement and physical cleanup of old versions, atomic rollback on failure, delete cascade, migration idempotency, the indexing lease and stale recovery in SQL, the readiness probe, and connection-pool recovery after a database outage) and `tests/test_documents_postgres_e2e.py` (upload -> privacy review -> redaction -> index -> ask -> citations -> isolation -> delete through the real HTTP API and real service; embeddings and the answering model are mocked, and the tests say so). They run only when `TEST_DATABASE_URL` passes `tests/pg_guard.py`: a **local** host, a database name containing `test` as a separate word (`avx_test`; `latest_data` is refused), and not equal to `DATABASE_URL`. The guard itself is tested on every run, with no database needed, by `tests/test_pg_guard.py`. They never DROP or TRUNCATE; they delete only rows they created. Without a valid test database they are skipped.

```powershell
# A SEPARATE, disposable container: never your real documents database. The throwaway password "pw" is only acceptable
# because the port is bound to localhost and the data is disposable.
docker run -d --name avx-pg-test -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=avx_test -p 127.0.0.1:5433:5432 pgvector/pgvector:pg17
$env:TEST_DATABASE_URL = "postgresql://postgres:pw@127.0.0.1:5433/avx_test"
python -m pytest tests/test_documents_postgres.py tests/test_documents_postgres_e2e.py -v   # the tests migrate this database themselves
```

**Verification status.** The real-database suites above were run against PostgreSQL 17.11 with the real pgvector 0.8.7 extension (official `pgvector/pgvector:pg17` image): the `vector(768)` type, the `<=>` cosine operator, the HNSW index, dimension enforcement, migrations (fresh install, upgrade and repeat runs) and the recovery logic. A live run with a real Gemini key and a synthetic document also passed on the development machine: chat, 768-dimension `gemini-embedding-001` embeddings, indexing, retrieval of the expected passage with a correct citation, scope isolation, deletion and cleanup. That live run is a manual check, not part of the automated suite, and the full document workflow **on a physical phone has not been verified**. Note for large corpora: with filters on owner/document an HNSW scan can return fewer than `top_k` rows; pgvector 0.8 offers `SET hnsw.iterative_scan = relaxed_order` to mitigate this (not enabled yet).

## Research

**Setup.** Create a key at https://app.tavily.com, put it in `backend/.env` as `TAVILY_API_KEY=...`, restart uvicorn. `/health` then shows `"research": {"provider": "tavily", "configured": true}`. The key stays on the backend; it is sent to Tavily only in the `Authorization` header and never logged or returned.

**Pipeline** (`POST /api/research`, `app/services/research_service.py`):

1. Validate the question (`RESEARCH_MAX_QUERY_CHARS`, default 400).
2. **Input privacy gate**, identical to chat: block / redact-required / confirmation-required stop here, before any search. In the app the question goes through the same privacy review and optimizer comparison as chat (shared `usePromptReview` hook).
3. Check that Tavily is configured and that a model route exists (manual model, provider credentials), so a bad request never spends a search.
4. **One Tavily search** with only the approved question (no chat history, documents or other data), `include_answer=false` (Tavily's own AI answer is never used).
5. **Source validation**: http(s) only; rejects malformed URLs, embedded credentials, localhost/private/link-local addresses (`169.254.169.254` included); de-duplicates equivalent URLs (`www.`, trailing `/`, `#fragment`, tracking params); drops empty results; bounds each source (`RESEARCH_MAX_SOURCE_CHARS`) and the total (`RESEARCH_MAX_TOTAL_SOURCE_CHARS`). URLs are **not fetched or verified**; they are what Tavily returned.
6. If no usable source remains: `404 research_no_sources`, **and no model is called** (no evidence-free answers).
7. **One routed generation** (task type `research`, mappable via `ROUTER_TASK_MODELS`; normal policies and bounded fallback apply; a fallback never repeats the search). App rules go in the provider's *system instruction*; sources go in the user message, wrapped as numbered, explicitly untrusted blocks.
8. **Response privacy scan** of the answer (Phase 5 rules: redact / warn / block). Source titles and snippets shown in the app go through the same policy (a withheld snippet becomes `null`).
9. **Citation checks**: `[n]` markers that match no returned source are removed (never re-mapped), and any URL in the answer that is not one of the sources becomes `[link removed]`.

**Response** (abridged):

```json
{
  "answer": "Loop unrolling reduces branch overhead [1]; vectorisation uses SIMD units [2].",
  "evidence_status": "cited",
  "sources": [
    { "id": 1, "title": "Loop optimization", "url": "https://example.org/loops", "domain": "example.org",
      "snippet": "Loop unrolling is…", "published_date": "2025-05-01", "retrieved_at": "2026-10-12T12:00:00+00:00",
      "flags": [], "cited": true }
  ],
  "citations": { "cited_source_ids": [1, 2], "invalid_citations_removed": 0, "unverified_links_removed": 0 },
  "metadata": {
    "request_id": "…", "provider": "gemini", "model": "gemini-2.5-flash", "latency_ms": 2100,
    "usage": { "input_tokens": 1800, "output_tokens": 120, "total_tokens": 1990, "source": "provider" },
    "privacy": { "checked": true, "decision": "allow", "categories": {} }, "prompt_source": "original",
    "routing": { "task_type": "research", "routing_policy": "balanced", "selected_model": "gemini-2.5-flash", "…": "…" },
    "response_privacy": { "status": "clean", "…": "…" },
    "search": { "provider": "tavily", "depth": "basic", "results_returned": 5, "sources_used": 4,
                "rejected_invalid_url": 0, "rejected_duplicate": 1, "rejected_empty": 0, "truncated": 2,
                "flagged_sources": 0, "search_latency_ms": 900 }
  }
}
```

`evidence_status`: `cited` (the answer references returned sources; **this is not verification that each claim is correct**), `uncited` (no valid citation: treat as unverified), `insufficient_evidence` (the model said the sources were not enough).

Request fields: `question` (required), `search_depth` (`basic`|`advanced`), `max_sources` (1-10, capped by `RESEARCH_MAX_RESULTS`), `time_range` (`day`|`week`|`month`|`year`), plus the same `routing_policy` / `model` / `provider` / `privacy_ack` / `prompt_source` as chat.

**Errors** (normal error format, no question/source/answer text): `research_provider_not_configured` (503), `research_timeout` (504), `research_rate_limited` / `research_quota_exceeded` (429), `research_auth_error`, `research_bad_request`, `research_malformed_response`, `research_provider_error` (502), `research_no_sources` (404), `query_too_long` (422), plus the existing privacy, routing, provider and response-privacy codes. Failures after the search include `error.details.search` (counts only).

**Prompt injection (defense in depth, not a guarantee).** Sources are treated as data: rules live in the system instruction; source text is wrapped in numbered blocks whose delimiters cannot be forged from inside (`<<<`/`>>>` are stripped); lines that look like instructions to an AI (e.g. "ignore previous instructions", "reveal your API key", "search again", "disable the privacy scanner") are replaced with `[instruction-like text removed]` and the source is flagged; titles that look like instructions are replaced by the domain. Structurally, a request performs **exactly one search and one generation (plus at most the router's bounded fallback)**; there are no tools, loops or browsing, so source text cannot trigger extra searches, change provider eligibility or disable privacy scanning, and the answer still passes response privacy and citation checks. A sufficiently clever injection can still bias the *content* of an answer; the keyword heuristic misses paraphrased or non-English instructions.

**In the app.** Home → Research: question box, depth and source-count options, privacy review and optimizer comparison as in chat, a loading state with **Stop waiting**, the answer with tappable `[n]` markers, a source list (title, domain, snippet, published/retrieved dates, injection flag) and an **Open source** button that asks for confirmation before opening the browser (http(s) only). Request details show model, routing, tokens, privacy outcomes and search stats. *Stop waiting only stops the app from waiting*: the server may still complete the search and generation it already started (and you may still be billed). Research results are not saved to chat history.

**Verify manually with real keys**
```powershell
# backend/.env: GEMINI_API_KEY=... and TAVILY_API_KEY=...
Invoke-RestMethod http://localhost:8000/health   # research.configured should be true
Invoke-RestMethod http://localhost:8000/api/research -Method Post -ContentType "application/json" -Body '{"question":"How do compilers optimize loops?","max_sources":3}' | ConvertTo-Json -Depth 6
```
Check that every `[n]` in `answer` exists in `sources`, that the URLs open the expected pages, and that `metadata.search` counts make sense. Questions naming people or capitalised products (e.g. "Rust", "Marie Curie") may first ask for privacy confirmation because the input engine tags them as names; that is the Phase 2 policy working as configured.

## Response privacy

**Pipeline.** prompt → input privacy (Phase 2) → optimizer review (Phase 3) → router → provider (Phase 4) → **response privacy scan → policy handling** → app. The scan runs on the final successful reply (including one from a permitted fallback model) *before* the HTTP response is built. It uses the same local Presidio engine and custom recognizers as input scanning, calls no external service, makes no extra provider call, and never changes which model was chosen.

**Response modes** (`RESPONSE_PRIVACY_MODE`, separate from the input `PRIVACY_POLICY`):

| Value | Behaviour |
|---|---|
| `policy` (**default**) | Per-category table below |
| `allow` | Return the reply unchanged; findings are recorded in metadata (observe only) |
| `warn` | Return unchanged with a warning in metadata |
| `redact` | Replace sensitive spans with `[CATEGORY]` |
| `block` | Withhold the reply: HTTP 403 `response_privacy_blocked`, no reply content |

**Default (`policy`), chosen so recognisable high-confidence secrets are never returned unmodified:**

| Action | Categories |
|---|---|
| redact | `API_KEY`, `PRIVATE_KEY`, `PASSWORD_OR_SECRET`, `CREDIT_CARD`, `US_SSN`, `US_PASSPORT`, `US_ITIN`, `IN_AADHAAR`, `US_BANK_NUMBER` |
| warn | `EMAIL_ADDRESS`, `PHONE_NUMBER`, `IBAN_CODE`, `IN_PAN`, `MEDICAL_LICENSE` |
| ignore | `PERSON`, `IP_ADDRESS`, `CRYPTO`, `US_DRIVER_LICENSE` (not reported; too common in ordinary text) |

Override per category with `RESPONSE_PRIVACY_POLICY={"EMAIL_ADDRESS":"redact"}` (actions: `ignore`, `allow`, `warn`, `redact`, `block`). Precedence: explicit override > uniform mode > built-in table > `RESPONSE_PRIVACY_DEFAULT_ACTION`. Categories the table marks `ignore` stay ignored under a uniform mode unless listed explicitly. When several actions apply, the strictest wins (block > redact > warn > allow). Invalid values stop the server at startup.

**Redaction.** Only spans of `redact`-level findings are replaced; warn-level spans, code fences, URLs and line breaks are left as they were, overlapping matches are merged into one replacement, and offsets are Unicode-safe (emoji/CJK tested). The redacted text is **re-scanned**; if any redact/block-level finding survives, or the scan itself fails, or the reply exceeds `RESPONSE_PRIVACY_MAX_CHARS`, the reply is **withheld** (fail closed), never returned unverified:

| Situation | Response |
|---|---|
| Policy says block | `403 response_privacy_blocked` |
| Redaction could not be verified | `503 response_privacy_redaction_failed` |
| Scanner crashed / reply too large | `503 response_privacy_unavailable` |
| Provider failed | the normal provider error (`provider_timeout`, …), never a `response_privacy_*` code |

These errors contain no reply text and no matched values. `error.details.response_privacy` has the category names and counts, and `error.details.routing` shows the model used. The app does not substitute any fallback text.

**Metadata** (`metadata.response_privacy`, on successful replies):

```json
{ "status": "redacted", "policy": "policy", "findings_count": 1,
  "categories": { "API_KEY": 1 }, "redacted": true, "redacted_spans": 1 }
```
`status` is `clean`, `allowed`, `warned` or `redacted` (`blocked` appears only inside error details). Only names and counts: never matched text, prompts or replies. `/health` reports the active mode as `privacy.response_policy`.

**In the app.** Replies show a notice when something was found (redacted / may contain sensitive information / allowed by policy) with human-readable category names, request details show the scan result, and a withheld reply appears as an error banner ("withheld by the response privacy policy (detected: …)") with Retry. The withheld reply never reaches the device, so it cannot be in chat history or local storage. The policy is read-only in Settings because it is a server setting; there is deliberately no toggle in the app. Replies that were *returned* (clean, warned, allowed, or redacted) are saved in local history as received.

**Logging.** Logs record request ID, status, policy, counts and category names only. Presidio's logger (which writes analysed text at DEBUG) plus the Google SDK, `httpx` and `httpcore` loggers are pinned to WARNING regardless of `LOG_LEVEL`.

**Limitations (defense in depth, not a guarantee).** Detection is pattern- and model-based: expect false negatives (e.g. credentials inside connection strings like `postgres://user:pass@host/db`, unlabelled passwords, paraphrased or split-up secrets, non-English text, names) and false positives (example e-mail addresses in documentation, 10-digit numbers read as phone numbers). Redaction may remove surrounding labels (`password = x` becomes `[PASSWORD_OR_SECRET]`). Whatever the model could have memorised is outside what this layer can see. A reply the policy lets through (`allow`/`warn`) may still contain sensitive data, and anything shown on screen could be copied by the user.

## Model routing

**Pipeline.** prompt → privacy scan and policy (Phase 2) → optimizer review (Phase 3) → **local task classification → routing decision → provider adapter** → response + metadata. Privacy always runs first, so a blocked prompt never reaches the classifier or any provider. Classification and routing are deterministic, run in-process, and call no model or external service.

**Task types** (`app/router/classifier.py`): `conversation`, `coding`, `summarization`, `reasoning`, `extraction`, or `unknown`. Keyword/pattern scoring on the latest user message; weak or tied evidence gives `unknown` (and low-confidence results are treated as unknown), which routes to the default model. There is no `research` type because research is not built yet. The classifier never changes the prompt, and only rule names (never text) are logged. It is a hint, not understanding: expect misclassifications.

**Policies** (set per device in Settings, or `ROUTER_DEFAULT_POLICY` for the server default):

| Policy | What it does | When data is missing |
|---|---|---|
| `balanced` | Model mapped to the task (`ROUTER_TASK_MODELS`) if eligible, else the default model; remaining models ordered by known price, then measured latency | Falls back to default/config order |
| `quality` | Model mapped to the task, else the default model | Default model |
| `cost` | Lowest `input + output` price per million tokens, **only if every eligible model has both prices** in `MODEL_PROFILES` | Default model, and the reason says which prices are missing |
| `latency` | Lowest median latency, **only if every eligible model has enough measurements** | Default model, and the reason says so |
| `manual` | Exactly the model you pick. It must be configured and currently available, otherwise `422 model_not_eligible` (never substituted). No fallback | n/a |

**Default model.** The default model of the first eligible provider (`GEMINI_MODEL` for Gemini). With a single configured model, every policy selects it ("only one eligible model").

**Where cost and latency data come from.** Nothing is built in. *Prices* are numbers you enter in `MODEL_PROFILES` from your provider's pricing page; unset means unknown. *Latency* is the median of this backend process's own recent successful calls (last 20, at least `ROUTER_MIN_LATENCY_SAMPLES`); it resets on restart and is unknown until measured. `GET /api/router/models` shows exactly what is known (`null` = unknown). The app makes no claim that any policy is cheaper, faster or better; it only follows the data you configured or that was measured.

**Eligibility.** A model is only routed to if its provider is registered, implemented, has credentials, passes `ROUTER_ALLOWED_PROVIDERS` (and `ROUTER_SENSITIVE_PROVIDERS` when the user confirmed a privacy warning), the model is listed in config (`GEMINI_MODEL` + `GEMINI_MODELS`), and it isn't cooling down after repeated failures. The OpenAI adapter is still a placeholder (no credentials were available to build and verify it), so it is never selected; asking for it explicitly returns `501 provider_not_implemented`.

**Fallback rules** (`ROUTER_FALLBACK`, `ROUTER_MAX_FALLBACKS`):
- Only after `provider_timeout`, `provider_error`, `rate_limited`, `provider_auth_error` or `model_not_found`. Never after request-level errors (bad request, blocked/empty output).
- At most `ROUTER_MAX_FALLBACKS` (default 1) extra attempt, always on a *different* eligible model. The same model is never retried, and no attempt starts if it could exceed `ROUTER_TOTAL_TIMEOUT_SECONDS`.
- Default `cheaper_or_equal`: the fallback must have a known price no higher than the model that failed. With no prices configured, fallback therefore does not happen. `any_eligible` is an explicit opt-in; `off` disables it. Manual never falls back.
- A timed-out request may still have been billed by the provider, so a fallback can mean paying twice for one prompt. Fallbacks are recorded in `routing.fallback_*` and `routing.attempts` and in logs as codes only.
- Models that fail repeatedly are skipped for `ROUTER_COOLDOWN_SECONDS`.

**Response metadata** (existing fields are unchanged; `metadata.routing` is new):

```json
{
  "metadata": {
    "request_id": "a1b2c3d4e5f6", "provider": "gemini", "model": "gemini-2.5-flash", "latency_ms": 840,
    "usage": { "input_tokens": 5, "output_tokens": 12, "total_tokens": 40, "source": "provider" },
    "privacy": { "checked": true, "decision": "allow", "categories": {} },
    "prompt_source": "optimized",
    "routing": {
      "task_type": "coding", "task_confidence": "high", "routing_policy": "quality",
      "selected_provider": "gemini", "selected_model": "gemini-2.5-flash",
      "routing_reason": "quality: task 'coding' is mapped to gemini-2.5-flash",
      "fallback_used": false, "fallback_reason": null,
      "attempts": [{ "provider": "gemini", "model": "gemini-2.5-flash", "outcome": "success" }]
    }
  }
}
```

Request fields: `routing_policy` (optional), `model` (manual choice), `provider` (optional restriction). Sending `model` alone means manual. `model` with another policy is `422 conflicting_routing`. Routing errors use the normal error format (`provider_not_configured`, `no_eligible_provider`, `model_not_eligible`, `model_required`, `model_ambiguous`); a failed request includes `error.details.routing.attempts`.

**Verifying locally**
```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q                   # routing is tested with scripted providers, offline
Invoke-RestMethod http://localhost:8000/api/router/models   # what is configured, available, priced, measured
Invoke-RestMethod http://localhost:8000/api/chat -Method Post -ContentType "application/json" -Body '{"messages":[{"role":"user","content":"Fix this bug:\n```py\nx=1\n```"}],"routing_policy":"quality"}'
```
Then open the request details sheet in the app (task type, policy, provider/model, fallback, reason).

**Limitations.** Latency stats and cooldowns live in memory and reset on restart. Only Gemini generates today. Classification is keyword-based. No model has been benchmarked here, so "quality" means "the model you mapped to that task", nothing more.

## Backend configuration (`backend/.env`)

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | - | Required for chat. Never commit. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model IDs change. If you get `model_not_found`, pick a current one from https://ai.google.dev/gemini-api/docs/models |
| `PROVIDER_TIMEOUT_SECONDS` | 30 | Per Gemini call |
| `MAX_MESSAGE_CHARS` / `MAX_MESSAGES` / `MAX_REQUEST_BYTES` | 8000 / 40 / 200000 | Request limits |
| `DATABASE_URL`, `EMBEDDING_*`, `DOCUMENT_*`, `CHUNK_*`, `MAX_CHUNKS_PER_DOCUMENT`, `RAG_*` | see `.env.example` | Documents (see above) |
| `INDEXING_STALE_SECONDS` | 900 (min 120) | Age after which an indexing lease is treated as crashed (see "Document states and stale indexing recovery") |
| `TAVILY_API_KEY`, `TAVILY_TIMEOUT_SECONDS`, `RESEARCH_MAX_RESULTS`, `RESEARCH_MAX_SOURCE_CHARS`, `RESEARCH_MAX_TOTAL_SOURCE_CHARS`, `RESEARCH_MAX_QUERY_CHARS`, `RESEARCH_DEFAULT_DEPTH` | none, 15, 5, 1500, 8000, 400, `basic` | Research (see above) |
| `RESPONSE_PRIVACY_MODE`, `RESPONSE_PRIVACY_POLICY`, `RESPONSE_PRIVACY_DEFAULT_ACTION`, `RESPONSE_PRIVACY_MAX_CHARS` | `policy`, `{}`, `warn`, 200000 | Reply scanning (see above) |
| `GEMINI_MODELS`, `ROUTER_*`, `MODEL_PROFILES` | see above | Router settings; full list with comments in `.env.example` |
| `CORS_ORIGINS` | localhost web ports | Only matters for browsers |

## API contract

`POST /api/chat`

```json
{ "messages": [{ "role": "user", "content": "Hi" }], "provider": "gemini", "model": null }
```

Response:

```json
{
  "message": { "role": "assistant", "content": "..." },
  "metadata": {
    "request_id": "a1b2c3d4e5f6", "provider": "gemini", "model": "gemini-2.5-flash",
    "latency_ms": 840, "status": "success",
    "usage": { "input_tokens": 5, "output_tokens": 12, "total_tokens": 40, "source": "provider" }
  }
}
```

(`total_tokens` can exceed input + output because Gemini counts internal "thinking" tokens in the total.)

Privacy endpoints: `POST /api/privacy/scan` with `{ "text": "..." }` returns `{ decision, can_proceed, requires_confirmation, findings: [{category, start, end, score, action}], categories }`; `POST /api/privacy/redact` returns `{ redacted_text, scan }` where `scan` is a fresh scan of the redacted text. Offsets are Unicode code points. Chat responses also include `metadata.privacy`.

Optimizer: `POST /api/optimize` with `{ "text": "..." }` returns `{ optimized_text, changed, changes: [{rule, description, count}], preservation: {verified, checks, note}, tokens: {basis: "estimate", method, note, original, optimized, saved, saved_percent}, privacy: {original, optimized} }` where `privacy` holds a local scan of each candidate. `POST /api/chat` accepts an optional `prompt_source` (`original` | `optimized` | `edited`) that is echoed in `metadata.prompt_source`.

Health: `GET /health` returns `{ status: "ok", version, providers, privacy, research, documents }` and `GET /api/ready` returns `{ status: "ok" | "degraded", database: "ok" | "not_configured" | "not_migrated" | "unavailable" }` (see "`/health` versus `/api/ready`").

Errors always look like `{ "error": { "code", "message", "request_id" } }`. Codes include `provider_not_configured` (503), `rate_limited` (429), `provider_auth_error`, `model_not_found`, `provider_timeout` (504), `validation_error` (422), `payload_too_large` (413).

## Testing

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q      # offline; no API key needed; real-database tests are skipped
# with the real pgvector tests (needs the avx-pg-test container, see "Documents (RAG)"):
$env:TEST_DATABASE_URL = "postgresql://postgres:pw@127.0.0.1:5433/avx_test"; .\.venv\Scripts\python.exe -m pytest -q
cd ..\mobile
npm run typecheck                               # tsc --noEmit
npm test                                        # node --test tests/*.test.mjs (needs Node 22.18+; no extra packages)
```

Last full run on the development machine: backend 506 passed with the real pgvector database (without `TEST_DATABASE_URL` the real-database tests are skipped, so the count of passed tests is lower), mobile 9 passed, `tsc --noEmit` clean. There are no lint or formatter configurations in the repository. No test touches the network or needs an API key; the live Gemini calls and the mobile UI on a physical device are manual checks.

The document tests cover upload validation (types by content, disguised and malformed files, encrypted/textless PDFs, size and Content-Length limits, filename sanitization), page-aware chunking, the embedding adapter (mocked SDK), ingestion privacy before any embedding call (redact/confirm rules, audit events, fail closed), owner isolation for every operation, deletion, retrieval thresholds/limits, insufficient evidence without a model call, citation and link validation, injection inside documents, response privacy on answers and excerpts, routing/fallback, and no content in logs. `tests/test_documents_postgres.py` runs the same isolation/search/cascade checks against a real PostgreSQL + pgvector database when `TEST_DATABASE_URL` is set (otherwise skipped). The research tests cover the Tavily adapter over a mock HTTP transport (request shape, normalization, every error class, malformed bodies, missing key with no request made), URL validation and de-duplication, result/text bounds, injection neutralisation, prompt construction, citation and link validation, and the endpoint end to end: privacy before search, no search on blocked/unconfirmed questions, no generation without evidence, bounded evidence, response privacy on answers and snippets, routing/fallback without repeated searches, no secrets or content in logs, and no network access. The response-privacy tests cover per-category detection and handling, false-positive regressions (placeholders, names, code, URLs, versions), all modes and overrides, redaction integrity (formatting, multiple spans, Unicode), fail-closed behaviour, block mode never returning the original, fallback replies being scanned without extra provider calls, input behaviour unchanged, and no secrets or reply text in logs/errors (including at DEBUG). The routing tests cover the classifier (every category plus unknown/ambiguous input), every policy, single-model and multi-model setups, missing credentials, ineligible/cooling-down models, manual validation, fallback allow/deny/bounds, privacy-before-routing for original/optimized/edited prompts, metadata and request IDs, backward-compatible response fields, no secrets or prompts in logs/errors, and no network access. The optimizer tests cover exact rewrites, idempotence, preservation of constraints/numbers/formats/quotes/code/secrets, the safety guard rejecting bad rewrites, token-estimate labelling, privacy enforcement on optimized and edited prompts, provider-call prevention (including with network access blocked), cancellation, and log hygiene. The privacy tests run the real Presidio engine: detection per category, benign text, redaction and re-scan, overlapping spans, emoji offsets, policy and overrides, edited and redacted prompts, blocked requests with a provider-call counter proving the provider is never reached, fail-closed behaviour, and checks that logs and error bodies contain no sensitive values. The live Gemini call and the mobile UI are not covered by automated tests.

## Troubleshooting

| Symptom | Fix |
|---|---|
| App says "Cannot reach the backend" | Backend running? URL right for your device (table above)? Phone: same Wi-Fi, `--host 0.0.0.0`, firewall rule, `/health` opens in the phone browser. |
| `privacy_engine_unavailable` | Presidio or the spaCy model is missing: re-run `pip install -r requirements.txt`, or `python -m spacy download en_core_web_sm`. |
| Send is disabled in Privacy review | The prompt still has blocked/redact items, or you edited it and haven't re-scanned. Tap Redact, or Edit then Re-scan. |
| `provider_not_configured` | `GEMINI_API_KEY` empty or `.env` not in `backend/`. Restart uvicorn after editing. `/health` shows which providers have keys. |
| `provider_auth_error` | Key invalid or restricted. Create a new one in AI Studio. |
| `model_not_found` | Change `GEMINI_MODEL`. |
| `rate_limited` | Free-tier quota. Wait, or enable billing. |
| Settings says the documents database is unavailable | `docker ps` shows `agentvision-pg`? `docker start agentvision-pg`, wait a few seconds, tap **Check connection**. The backend reconnects by itself; no restart needed. |
| Settings says the documents database is not set up | `python -m app.db.migrate` (also needed after pulling new code). |
| `documents_database_unavailable` on every document action right after an update | Migration `002` has not been applied: `python -m app.db.migrate`. |
| A document shows *Processing* for a long time | An indexing job is running or crashed. After `INDEXING_STALE_SECONDS` (default 15 minutes) it becomes *Failed: indexing interrupted* on the next refresh; then tap *Retry indexing* (or delete and re-upload if there is no retry button). |
| `409 document_indexing_in_progress` | Another request is indexing that document (or a crashed re-index lease has not expired yet). Wait and refresh. |
| `/health` works on the PC but not from the phone | Wi-Fi marked *Public* in Windows, firewall, or router client isolation: see "Checking the phone-to-backend connection". |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Android dev build blocks `http://` | Expo Go allows it. Standalone/dev builds need cleartext traffic enabled or an HTTPS URL. |
| Expo shows stale env values | `npx expo start -c` |

## Known limitations

- Phone-to-backend connectivity and the full document workflow on a physical phone are unverified; only the laptop and automated tests were used.
- There are no user accounts: documents are protected by a per-install bearer "scope key", which is not authentication.
- No OCR (scanned PDFs are rejected), only PDF and UTF-8 TXT.
- Stale-document recovery runs when documents are listed, fetched or retried (there is no background sweeper), and a document that crashed before its privacy review cannot be retried (delete and re-upload).
- The privacy engine is heuristic and can miss sensitive data; `RAG_MIN_SIMILARITY` is not calibrated for your documents.
- A single backend process with a small connection pool; HNSW with very selective filters can return fewer than `top_k` rows (see above).
- Voice is not implemented; OpenAI and other providers are placeholders.

## Security notes

- Keys are read only by the backend (`pydantic SecretStr`) and are never sent to the app or logged. `.env` is git-ignored; only `.env.example` files are committed.
- The backend logs request ID, provider, model, latency, token total and error codes - **not** prompts or responses.
- Do not call this "privacy-safe": Phase 2 reduces risk but cannot guarantee detection. Response-side checks and a larger evaluation set are still to do.

## Roadmap

Phase 2 Privacy engine (done) → Phase 3 Prompt optimizer (done, rule-based; measured savings still to do) → Phase 4 Router (done for Gemini; OpenAI/Claude adapters still to do) → Phase 5 Response privacy (done) → Phase 6 Research with Tavily (done) → Phase 7 Documents/RAG (done; real auth still to do) → 4 Router (OpenAI, Claude) → 5 Monitoring (WebSockets, Langfuse) → later: Exa, Crossref, OpenAlex → 7 Voice & documents → later: OCR, real user authentication. The provider interface (`backend/app/providers/base.py`) and the single chat workflow (`backend/app/services/chat_service.py`) are where Phases 2-4 plug in.
