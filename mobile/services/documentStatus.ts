/** Pure decisions for the Documents screens (no React Native imports, so they can be tested with plain Node).
 *  Nothing here decides what the BACKEND allows: it mirrors the backend's rules so the UI only offers actions that can
 *  work. The backend stays the authority and every call can still be refused. */
import type { DocumentInfo } from '../types/documents';

export type DocTone = 'success' | 'warning' | 'danger' | 'muted';
export type DocAction = 'ask' | 'review' | 'retry' | 'reupload' | 'wait';

export interface DocStatusView {
  label: string;
  tone: DocTone;
  /** One or two sentences telling the user what happened and what they can do next. */
  detail: string;
  /** The single next step the card should offer. */
  action: DocAction;
}

/** Backend rule (POST /reindex): only a failed document whose privacy review is complete can be retried. A document
 *  that failed before a decision was recorded must be uploaded again, because a retry would skip the review. */
export function canRetryIndexing(d: DocumentInfo): boolean {
  return d.status === 'failed' && d.privacy.decision !== 'none';
}

/** `reason` is the readable failure message for `d.error_code`; `categories` the readable detected categories. */
export function describeDocument(d: DocumentInfo, reason: string, categories: string): DocStatusView {
  switch (d.status) {
    case 'ready':
      return {
        label: 'Ready',
        tone: 'success',
        detail: `${d.chunk_count} passage${d.chunk_count === 1 ? '' : 's'} indexed. You can ask questions about it.`,
        action: 'ask',
      };
    case 'needs_review':
      return {
        label: 'Needs privacy review',
        tone: 'warning',
        detail:
          `Detected: ${categories || 'sensitive items'}. Nothing has been sent to the AI provider yet. ` +
          (d.privacy.required_action === 'redact'
            ? 'Policy requires redaction before indexing.'
            : 'You can redact it, or confirm indexing as-is.'),
        action: 'review',
      };
    case 'failed':
      return canRetryIndexing(d)
        ? { label: 'Failed', tone: 'danger', detail: `${reason} Tap "Retry indexing" to try again; your privacy decision is kept.`, action: 'retry' }
        : {
            label: 'Failed - upload again',
            tone: 'danger',
            detail: `${reason} The privacy review did not finish, so this file cannot be indexed. Delete it and upload it again.`,
            action: 'reupload',
          };
    default:
      return {
        label: 'Processing',
        tone: 'muted',
        detail:
          'Indexing is in progress. Pull down to refresh. If the server stopped, the document is marked as failed ' +
          'automatically after a while (15 minutes by default) and you can retry it.',
        action: 'wait',
      };
  }
}

export interface Askable {
  /** Selected documents that exist and are ready: the only ones a question should be sent for. */
  readyIds: string[];
  /** Selected documents the server no longer has (deleted elsewhere or never existed). */
  missing: number;
  /** Selected documents that exist but are not ready (processing, needs review, failed). */
  notReady: number;
}

export function selectAskable(selectedIds: string[], docs: DocumentInfo[]): Askable {
  const byId = new Map(docs.map((d) => [d.id, d]));
  const unique = [...new Set(selectedIds)];
  const readyIds: string[] = [];
  let missing = 0;
  let notReady = 0;
  for (const id of unique) {
    const d = byId.get(id);
    if (!d) missing += 1;
    else if (d.status !== 'ready') notReady += 1;
    else readyIds.push(id);
  }
  return { readyIds, missing, notReady };
}

/** Failures after which asking the same (already approved) question again is safe and likely to help. */
export const RETRYABLE_ASK_CODES = ['network_error', 'timeout'];
