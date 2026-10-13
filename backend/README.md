# AgentVision X - Backend

FastAPI service that validates chat requests and forwards them to an LLM provider.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env       # then set GEMINI_API_KEY
python -m pytest
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Structure

| Path | Role |
|---|---|
| `app/main.py` | App factory, CORS, request-ID + body-size middleware |
| `app/api/routes.py` | `/health`, `/api/chat` |
| `app/services/chat_service.py` | The chat workflow: validate → pick provider → call → build metadata. Future privacy/optimizer/router steps go here. |
| `app/providers/base.py` | `LLMProvider` interface + `ProviderError` |
| `app/providers/gemini.py` | Real Gemini adapter (`google-genai`) |
| `app/providers/openai_provider.py` | Honest placeholder: reports "not configured" / "not implemented" |
| `app/services/privacy_service.py` | Scan, policy decision, redaction, and the `/api/chat` privacy gate (fails closed) |
| `app/privacy/` | `detector.py` (Presidio), `recognizers.py` (custom rules), `policy.py` (allow/warn/redact/block) |
| `app/documents/` | `files.py` (upload validation, extraction), `chunking.py`, `embeddings.py` (Gemini embeddings), `store.py` (PostgreSQL/pgvector, owner-scoped), `grounding.py` (passages, `[Dn]` citation checks), `scope.py` (dev-mode document scope) |
| `app/db/` | `migrate.py` + `migrations/*.sql` (run `python -m app.db.migrate`) |
| `app/services/document_service.py` | Upload -> privacy gate -> index; ask -> retrieval -> routed answer -> response privacy -> citations |
| `app/research/` | `tavily.py` (Tavily REST adapter), `sources.py` (URL validation, de-duplication, bounds, injection neutralising), `grounding.py` (system instruction, evidence message, citation/link checks) |
| `app/services/research_service.py` | `/api/research` pipeline: privacy -> one search -> one routed generation -> response privacy -> citations |
| `app/services/response_privacy_service.py`, `app/privacy/response_policy.py` | Scans the provider's reply, applies the response policy (redact / warn / block / allow), fails closed |
| `app/optimizer/` | `rules.py` (deterministic rules + protected-span masking), `optimizer.py` (`PromptOptimizer`, preservation check), `tokens.py` (labelled token estimates) |
| `app/services/optimizer_service.py` | `/api/optimize`: optimize, estimate tokens, scan both candidates. Never calls a provider. |
| `app/router/` | `classifier.py` (local task classification), `router.py` (eligibility, policies, bounded fallback), `availability.py` (cooldowns, measured latency) |
| `app/core/` | Settings (env vars) and error handlers |
| `app/schemas/` | Pydantic request/response models |
| `tests/` | API tests (fake provider) and Gemini adapter tests (mocked SDK) |

To add a provider: implement `LLMProvider` (`is_configured`, `list_models`, `default_model`, `generate`), register it in `providers/registry.py`, and add its name to the `provider` Literal in `schemas/chat.py`. The router picks it up automatically once it is configured, implemented and lists models.

Tests never call the network. A live Gemini call needs your own key; see the root README for the PowerShell smoke test.
