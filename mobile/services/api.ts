import type {
  ChatResponse,
  HealthResponse,
  OptimizeResult,
  PrivacyRedactResult,
  PrivacyScan,
  PromptSource,
  RouterModels,
  Role,
  RoutingPreference,
} from '@/types/chat';
import type { AskDocumentsRequest, AskDocumentsResponse, DocumentInfo } from '@/types/documents';
import type { ResearchRequest, ResearchResponse } from '@/types/research';

import { getApiUrl } from './config';
import { getDocumentScope } from './documentScope';
import { INVALID_RESPONSE_MESSAGE, expectObject, validateChat, validateDocumentList, validateHealth } from './validate';

export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public status?: number,
    public requestId?: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
  external?: AbortSignal,
  validate: (body: unknown) => void = expectObject,
): Promise<T> {
  const url = `${getApiUrl()}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const onExternalAbort = () => controller.abort();
  external?.addEventListener('abort', onExternalAbort);
  let response: Response;
  try {
    response = await fetch(url, { ...init, signal: controller.signal });
  } catch (e) {
    if (external?.aborted) {
      // Only the app stops waiting. The server may still finish (and be billed for) work already started.
      throw new ApiError('Stopped waiting for the server.', 'cancelled');
    }
    if (controller.signal.aborted) {
      throw new ApiError('The request timed out. Please try again.', 'timeout');
    }
    throw new ApiError(
      `Cannot reach the backend at ${getApiUrl()}. Check that it is running and the URL in Settings is correct.`,
      'network_error',
    );
  } finally {
    clearTimeout(timer);
    external?.removeEventListener('abort', onExternalAbort);
  }

  let body: any = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body; handled below.
  }
  if (!response.ok) {
    const err = body?.error;
    throw new ApiError(
      err?.message ?? `Server returned an error (HTTP ${response.status}).`,
      err?.code ?? 'http_error',
      response.status,
      err?.request_id,
      err?.details,
    );
  }
  if (response.status === 204) return null as T;
  try {
    validate(body); // a 200 that is not our JSON (captive portal, proxy, wrong server) must not reach the UI
  } catch {
    throw new ApiError(INVALID_RESPONSE_MESSAGE, 'invalid_response', response.status);
  }
  return body as T;
}

export function checkHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { method: 'GET' }, 6000, undefined, validateHealth);
}

export function sendChat(
  messages: { role: Role; content: string }[],
  promptSource?: PromptSource,
  routing?: RoutingPreference,
): Promise<ChatResponse> {
  return request<ChatResponse>(
    '/api/chat',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // privacy_ack: the user has already been through the privacy review for this conversation.
      body: JSON.stringify({
        messages,
        privacy_ack: true,
        prompt_source: promptSource,
        // Omitted = the server's default policy. The server picks the provider; keys never leave it.
        routing_policy: routing?.policy,
        model: routing?.policy === 'manual' ? routing.model : undefined,
      }),
    },
    100000, // the server may try one fallback model (see ROUTER_TOTAL_TIMEOUT_SECONDS)
    undefined,
    validateChat,
  );
}

const jsonPost = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/** Local check on the backend. The text is not sent to any AI provider. */
export function scanPrompt(text: string): Promise<PrivacyScan> {
  return request<PrivacyScan>('/api/privacy/scan', jsonPost({ text }), 20000);
}

export function redactPrompt(text: string): Promise<PrivacyRedactResult> {
  return request<PrivacyRedactResult>('/api/privacy/redact', jsonPost({ text }), 20000);
}

/** Local, deterministic suggestion. Calls no AI provider; nothing is sent until the user approves a version. */
export function optimizePrompt(text: string): Promise<OptimizeResult> {
  return request<OptimizeResult>('/api/optimize', jsonPost({ text }), 20000);
}

export function getRouterModels(): Promise<RouterModels> {
  return request<RouterModels>('/api/router/models', { method: 'GET' }, 8000);
}

/** One web search + one grounded answer. Only `question` is sent to the search service (after privacy checks). */
export function runResearch(body: ResearchRequest, signal?: AbortSignal): Promise<ResearchResponse> {
  return request<ResearchResponse>('/api/research', jsonPost(body), 120000, signal);
}

// ---- documents: every call carries this device's document scope (see services/documentScope.ts) ----

async function scoped(init: RequestInit = {}): Promise<RequestInit> {
  return { ...init, headers: { ...(init.headers as Record<string, string> | undefined), 'X-Document-Scope': await getDocumentScope() } };
}

export async function listDocuments(): Promise<DocumentInfo[]> {
  const res = await request<{ documents: DocumentInfo[] }>('/api/documents', await scoped({ method: 'GET' }), 15000, undefined, validateDocumentList);
  return res.documents;
}

/** Uploads one PDF/TXT. The server extracts text and privacy-scans it before anything is sent for indexing. */
export async function uploadDocument(file: { uri: string; name: string; mimeType?: string | null }, signal?: AbortSignal): Promise<DocumentInfo> {
  const form = new FormData();
  // React Native's FormData accepts a { uri, name, type } descriptor for files.
  form.append('file', { uri: file.uri, name: file.name, type: file.mimeType || 'application/octet-stream' } as unknown as Blob);
  return request<DocumentInfo>('/api/documents', await scoped({ method: 'POST', body: form }), 180000, signal);
}

export async function decideDocumentPrivacy(id: string, decision: 'confirm' | 'redact'): Promise<DocumentInfo> {
  return request<DocumentInfo>(`/api/documents/${encodeURIComponent(id)}/privacy-decision`, await scoped(jsonPost({ decision })), 180000);
}

export async function reindexDocument(id: string): Promise<DocumentInfo> {
  return request<DocumentInfo>(`/api/documents/${encodeURIComponent(id)}/reindex`, await scoped({ method: 'POST' }), 180000);
}

export async function deleteDocument(id: string): Promise<void> {
  await request<null>(`/api/documents/${encodeURIComponent(id)}`, await scoped({ method: 'DELETE' }), 15000);
}

export async function askDocuments(body: AskDocumentsRequest, signal?: AbortSignal): Promise<AskDocumentsResponse> {
  return request<AskDocumentsResponse>('/api/documents/ask', await scoped(jsonPost(body)), 120000, signal);
}
