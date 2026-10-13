import type { PromptSource, ResponseMetadata, RoutingPolicy } from '@/types/chat';

export interface ResearchRequest {
  question: string;
  search_depth?: 'basic' | 'advanced';
  max_sources?: number;
  time_range?: 'day' | 'week' | 'month' | 'year';
  routing_policy?: RoutingPolicy;
  model?: string;
  privacy_ack: boolean;
  prompt_source?: PromptSource;
}

export interface ResearchSource {
  id: number;
  title: string;
  /** Validated http(s) URL as returned by the search service. Not fetched or verified by the app. */
  url: string;
  domain: string;
  /** Null when the response privacy policy withheld it. */
  snippet: string | null;
  published_date: string | null;
  retrieved_at: string;
  flags: string[];
  cited: boolean;
}

export interface ResearchResponse {
  answer: string;
  /** 'cited' = the answer references returned sources. It does NOT mean every claim was verified. */
  evidence_status: 'cited' | 'uncited' | 'insufficient_evidence';
  sources: ResearchSource[];
  citations: { cited_source_ids: number[]; invalid_citations_removed: number; unverified_links_removed: number };
  metadata: ResponseMetadata;
}
