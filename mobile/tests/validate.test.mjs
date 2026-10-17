// Run with: node --test tests/validate.test.mjs   (Node 22.18+; no extra packages)
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { expectObject, validateChat, validateDocumentList, validateHealth } from '../services/validate.ts';

const goodChat = { message: { role: 'assistant', content: 'Hi' }, metadata: { request_id: 'x' } };

test('a well-formed chat reply passes', () => assert.doesNotThrow(() => validateChat(goodChat)));

test('chat replies that are null, HTML text, arrays or wrongly shaped are rejected', () => {
  for (const bad of [null, undefined, '<html>login</html>', [], {}, { message: 'Hi' }, { message: { role: 'user', content: 'x' }, metadata: {} },
    { message: { role: 'assistant', content: 42 }, metadata: {} }, { message: { role: 'assistant', content: 'x' } }]) {
    assert.throws(() => validateChat(bad), TypeError);
  }
});

test('health requires status ok and a providers object', () => {
  assert.doesNotThrow(() => validateHealth({ status: 'ok', providers: { gemini: true } }));
  for (const bad of [null, { status: 'ok' }, { status: 'down', providers: {} }, 'ok']) assert.throws(() => validateHealth(bad), TypeError);
});

test('document list requires an array of objects', () => {
  assert.doesNotThrow(() => validateDocumentList({ documents: [] }));
  assert.doesNotThrow(() => validateDocumentList({ documents: [{ id: 'a' }] }));
  for (const bad of [null, {}, { documents: 'x' }, { documents: [1] }]) assert.throws(() => validateDocumentList(bad), TypeError);
});

test('generic object check rejects non-objects', () => {
  assert.doesNotThrow(() => expectObject({}));
  for (const bad of [null, [], 'x', 3]) assert.throws(() => expectObject(bad), TypeError);
});
