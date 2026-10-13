import { ApiError } from '@/services/api';

const MESSAGES: Record<string, string> = {
  documents_database_not_configured: 'Documents are not set up on the server (no DATABASE_URL). See the README.',
  documents_database_not_migrated: 'The document tables are missing on the server. Run: python -m app.db.migrate',
  documents_database_unavailable: 'The document database is unavailable. Try again later.',
  embedding_not_configured: 'Indexing is not set up on the server (no embedding API key).',
  embedding_dimension_mismatch: 'Server misconfiguration: embedding size does not match the database.',
  embedding_rate_limited: 'The embedding service is rate limited. Retry indexing later.',
  embedding_timeout: 'The embedding service timed out. Retry indexing.',
  embedding_auth_error: 'The embedding service rejected the server key.',
  embedding_model_not_found: 'The configured embedding model was not found.',
  embedding_failed: 'Indexing failed at the embedding service. Retry indexing.',
  embedding_invalid: 'The embedding service returned invalid data.',
  privacy_engine_unavailable: 'The privacy check failed, so nothing was indexed.',
  redaction_incomplete: 'Redaction could not be verified, so nothing was indexed.',
  document_too_many_chunks: 'The document is too long to index.',
  document_no_text: 'No usable text found. Scanned PDFs are not supported (no OCR).',
  document_unsupported_type: 'Only PDF and UTF-8 TXT files are supported.',
  document_malformed: 'The file could not be read (damaged PDF or not UTF-8 text).',
  document_encrypted: 'Password-protected PDFs are not supported.',
  document_too_large: 'The file is larger than the server allows.',
  payload_too_large: 'The file is larger than the server allows.',
  document_too_many_pages: 'The PDF has more pages than the server allows.',
  document_text_too_long: 'The document has more text than the server allows.',
  document_extraction_timeout: 'Reading the file took too long.',
  document_not_found: 'Document not found. It may have been deleted.',
  document_not_ready: 'A selected document is not indexed yet.',
  documents_insufficient_evidence: 'The selected documents do not contain passages relevant enough to answer this.',
  cancelled: 'The app stopped waiting. The server may still finish what it started.',
};

export function documentErrorMessage(code: string | null | undefined, fallback?: string): string {
  return (code && MESSAGES[code]) || fallback || 'Something went wrong.';
}

export function describeError(e: unknown): { code: string; message: string } {
  if (e instanceof ApiError) return { code: e.code, message: documentErrorMessage(e.code, e.message) };
  return { code: 'unknown', message: e instanceof Error ? e.message : 'Something went wrong.' };
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
