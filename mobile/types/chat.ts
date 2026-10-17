export type Role = 'user' | 'assistant';

export interface Usage {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  /** "provider" = reported by the AI provider. "unavailable" = nothing was reported. */
  source: 'provider' | 'unavailable';
}

export type PrivacyAction = 'allow' | 'warn' | 'redact' | 'block';

export interface PrivacyFinding {
  category: string;
  /** Offsets in Unicode code points (not UTF-16 units). */
  start: number;
  end: number;
  score: number;
  action: PrivacyAction;
}

export interface PrivacyScan {
  decision: PrivacyAction;
  can_proceed: boolean;
  requires_confirmation: boolean;
  findings: PrivacyFinding[];
  categories: Record<string, number>;
}

export interface PrivacyRedactResult {
  redacted_text: string;
  scan: PrivacyScan;
}

/** Outcome of the server's scan of the AI's reply. Category names and counts only, never the matched text. */
export interface ResponsePrivacy {
  status: 'clean' | 'allowed' | 'warned' | 'redacted' | 'blocked';
  policy: string;
  findings_count: number;
  categories: Record<string, number>;
  redacted: boolean;
  redacted_spans: number;
}

export type RoutingPolicy = 'balanced' | 'cost' | 'quality' | 'latency' | 'manual';

export interface RoutingInfo {
  task_type: 'conversation' | 'coding' | 'summarization' | 'reasoning' | 'extraction' | 'research' | 'documents' | 'unknown';
  task_confidence: 'high' | 'medium' | 'low';
  routing_policy: RoutingPolicy;
  selected_provider: string;
  selected_model: string;
  routing_reason: string;
  fallback_used: boolean;
  fallback_reason: string | null;
  attempts: { provider: string; model: string; outcome: string }[];
}

export interface RouterModels {
  default_policy: RoutingPolicy;
  policies: RoutingPolicy[];
  fallback_mode: 'off' | 'cheaper_or_equal' | 'any_eligible';
  default_model: string | null;
  eligible_count: number;
  providers: {
    name: string;
    configured: boolean;
    implemented: boolean;
    allowed: boolean;
    models: {
      id: string;
      is_default: boolean;
      available: boolean;
      unavailable_reason: string | null;
      input_cost_per_mtok: number | null;
      output_cost_per_mtok: number | null;
      measured_latency_ms: number | null;
      latency_samples: number;
    }[];
  }[];
}

export interface RoutingPreference {
  policy?: RoutingPolicy;
  model?: string;
}

export type PromptSource = 'original' | 'optimized' | 'edited';

export interface OptimizeResult {
  optimized_text: string;
  changed: boolean;
  changes: { rule: string; description: string; count: number }[];
  preservation: { verified: boolean; checks: { name: string; passed: boolean }[]; note: string };
  /** Local ESTIMATES (not provider-reported usage). `method` says how they were produced. */
  tokens: {
    basis: 'estimate';
    method: string;
    note: string;
    original: number;
    optimized: number;
    saved: number;
    saved_percent: number;
  };
  privacy: { original: PrivacyScan; optimized: PrivacyScan };
}

export interface ResponseMetadata {
  request_id: string;
  provider: string;
  model: string;
  latency_ms: number;
  status: 'success';
  usage: Usage;
  privacy?: { checked: boolean; decision: PrivacyAction; categories: Record<string, number> };
  prompt_source?: PromptSource | null;
  routing?: RoutingInfo | null;
  response_privacy?: ResponsePrivacy | null;
  /** Present on document answers only. */
  retrieval?: {
    embedding_model: string;
    top_k: number;
    candidates: number;
    below_threshold: number;
    deduplicated: number;
    used: number;
    min_similarity: number;
    best_similarity: number | null;
    context_chars: number;
    flagged_passages: number;
  };
  /** Present on research answers only. */
  search?: {
    provider: 'tavily';
    depth: string;
    time_range: string | null;
    max_sources: number;
    results_returned: number;
    sources_used: number;
    rejected_invalid_url: number;
    rejected_duplicate: number;
    rejected_empty: number;
    truncated: number;
    flagged_sources: number;
    search_latency_ms: number;
  };
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  createdAt: number;
  /** Which version of the prompt the user chose to send (user messages only). */
  promptSource?: PromptSource;
  metadata?: ResponseMetadata;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
}

export interface ChatResponse {
  message: { role: 'assistant'; content: string };
  metadata: ResponseMetadata;
}

export type DatabaseState = 'not_configured' | 'ok' | 'not_migrated' | 'unavailable';

/** GET /api/ready: whether the backend can actually reach its documents database (separate from /health liveness). */
export interface ReadyResponse {
  status: 'ok' | 'degraded';
  database: DatabaseState;
}

export interface HealthResponse {
  status: 'ok';
  version: string;
  providers: Record<string, boolean>;
  privacy?: { engine: string; ready: boolean; response_policy?: string };
  research?: { provider: 'tavily'; configured: boolean };
  documents?: { database_configured: boolean; embedding_configured: boolean; embedding_model: string; embedding_dimensions: number };
}
