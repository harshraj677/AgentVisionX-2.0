import type { PromptSource, ResponseMetadata, RoutingPolicy } from '@/types/chat';

export interface DocumentInfo {
  id: string;
  filename: string;
  file_type: 'pdf' | 'txt';
  size_bytes: number;
  page_count: number | null;
  char_count: number | null;
  status: 'processing' | 'needs_review' | 'ready' | 'failed';
  error_code: string | null;
  /** Category names and counts only; never document text. */
  privacy: {
    required_action: 'none' | 'confirm' | 'redact';
    decision: 'none' | 'not_required' | 'confirmed' | 'redacted';
    categories: Record<string, number>;
  };
  embedding_model: string | null;
  chunk_count: number;
  uploaded_at: string | null;
  processed_at: string | null;
}

export interface AskDocumentsRequest {
  question: string;
  document_ids: string[];
  top_k?: number;
  routing_policy?: RoutingPolicy;
  model?: string;
  privacy_ack: boolean;
  prompt_source?: PromptSource;
}

export interface DocumentPassage {
  ref: string; // "D1"
  document_id: string;
  filename: string;
  chunk_id: string;
  chunk_index: number;
  page_number: number | null;
  similarity: number;
  /** Null if the response privacy policy withheld it. */
  excerpt: string | null;
  flags: string[];
  cited: boolean;
}

export interface AskDocumentsResponse {
  answer: string;
  /** 'cited' = references retrieved passages. It does NOT mean every claim was verified. */
  evidence_status: 'cited' | 'uncited' | 'insufficient_evidence';
  passages: DocumentPassage[];
  invalid_citations_removed: number;
  unverified_links_removed: number;
  metadata: ResponseMetadata;
}
