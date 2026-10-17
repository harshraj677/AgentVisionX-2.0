// Run with: npm test   (node --test; Node 22.18+, no extra packages)
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { RETRYABLE_ASK_CODES, canRetryIndexing, describeDocument, selectAskable } from '../services/documentStatus.ts';
import { latestOnly } from '../services/latest.ts';
import { connectionFailureTitle, timeoutMessage } from '../services/validate.ts';

const doc = (over = {}) => ({
  id: 'd1', filename: 'a.txt', file_type: 'txt', size_bytes: 10, page_count: null, char_count: 10, status: 'ready',
  error_code: null, privacy: { required_action: 'none', decision: 'not_required', categories: {} }, embedding_model: 'm',
  chunk_count: 3, uploaded_at: null, processed_at: null, ...over,
});
const failed = (decision) => doc({ status: 'failed', error_code: 'indexing_interrupted', privacy: { required_action: 'none', decision, categories: {} } });

test('retry is offered only when the backend would accept it (failed AND privacy review complete)', () => {
  assert.equal(canRetryIndexing(failed('not_required')), true);
  assert.equal(canRetryIndexing(failed('confirmed')), true);
  assert.equal(canRetryIndexing(failed('redacted')), true);
  assert.equal(canRetryIndexing(failed('none')), false); // a retry would skip the privacy review
  for (const status of ['ready', 'processing', 'needs_review']) assert.equal(canRetryIndexing(doc({ status })), false);
});

test('an interrupted document with no privacy decision tells the user to delete and upload again', () => {
  const v = describeDocument(failed('none'), 'Indexing was interrupted.', '');
  assert.equal(v.action, 'reupload');
  assert.match(v.label, /upload again/i);
  assert.match(v.detail, /Delete it and upload it again/);
  assert.match(v.detail, /privacy review did not finish/);
});

test('a failed document with a recorded decision offers retry and says the decision is kept', () => {
  const v = describeDocument(failed('redacted'), 'Indexing was interrupted.', '');
  assert.equal(v.action, 'retry');
  assert.match(v.detail, /Retry indexing/);
  assert.match(v.detail, /decision is kept/);
});

test('every status has a label, tone, explanation and exactly one next action', () => {
  const cases = {
    ready: doc(),
    processing: doc({ status: 'processing' }),
    needs_review: doc({ status: 'needs_review', privacy: { required_action: 'redact', decision: 'none', categories: { EMAIL_ADDRESS: 1 } } }),
    failed: failed('confirmed'),
  };
  const actions = { ready: 'ask', processing: 'wait', needs_review: 'review', failed: 'retry' };
  for (const [status, d] of Object.entries(cases)) {
    const v = describeDocument(d, 'reason.', 'Email address');
    assert.ok(v.label && v.detail.length > 10, status);
    assert.equal(v.action, actions[status]);
  }
  assert.match(describeDocument(cases.needs_review, '', 'Email address').detail, /Nothing has been sent to the AI provider/);
  assert.match(describeDocument(cases.needs_review, '', 'Email address').detail, /requires redaction/);
  assert.match(describeDocument(cases.processing, '', '').detail, /Pull down to refresh/);
  assert.match(describeDocument(doc({ chunk_count: 1 }), '', '').detail, /1 passage indexed/);
});

test('asking uses only documents that exist and are ready, and reports the rest', () => {
  const docs = [doc({ id: 'a' }), doc({ id: 'b', status: 'processing' }), doc({ id: 'c', status: 'failed' })];
  assert.deepEqual(selectAskable(['a', 'b', 'c', 'gone', 'a'], docs), { readyIds: ['a'], missing: 1, notReady: 2 });
  assert.deepEqual(selectAskable([], docs), { readyIds: [], missing: 0, notReady: 0 });
  assert.deepEqual(selectAskable(['x'], []), { readyIds: [], missing: 1, notReady: 0 });
});

test('only transient network failures are offered a retry for a question', () => {
  assert.deepEqual([...RETRYABLE_ASK_CODES].sort(), ['network_error', 'timeout']);
  for (const code of ['document_not_found', 'documents_insufficient_evidence', 'response_privacy_blocked', 'privacy_blocked']) {
    assert.equal(RETRYABLE_ASK_CODES.includes(code), false, code);
  }
});

test('a slow older response is ignored once something newer happened (a deleted document must not reappear)', () => {
  const guard = latestOnly();
  const slowList = guard.next(); // reload starts
  const fastList = guard.next(); // another reload starts
  assert.equal(guard.isCurrent(slowList), false);
  assert.equal(guard.isCurrent(fastList), true);
  guard.next(); // local delete invalidates in-flight reloads
  assert.equal(guard.isCurrent(fastList), false);
});

test('connection failures get a specific headline and an actionable timeout message', () => {
  assert.equal(connectionFailureTitle('timeout'), 'Backend timed out');
  assert.equal(connectionFailureTitle('network_error'), 'Backend unreachable');
  assert.equal(connectionFailureTitle('invalid_response'), 'Not an AgentVision X backend');
  assert.equal(connectionFailureTitle('http_error'), 'Backend returned an error');
  assert.equal(connectionFailureTitle(undefined), 'Backend returned an error');
  const m = timeoutMessage('http://192.168.1.5:8000');
  assert.match(m, /http:\/\/192\.168\.1\.5:8000/);
  assert.match(m, /same Wi-Fi/);
});
