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

import { networkErrorMessage, normalizeApiUrl } from '../services/validate.ts';

test('a Backend URL typed without a scheme gets http:// (previously fetch threw and the app said "cannot reach")', () => {
  assert.deepEqual(normalizeApiUrl('192.168.29.82:8000'), { ok: true, url: 'http://192.168.29.82:8000' });
  assert.deepEqual(normalizeApiUrl('  HTTP://Example.com:8000/  '), { ok: true, url: 'http://Example.com:8000' });
  assert.deepEqual(normalizeApiUrl('https://api.example.com/v1/'), { ok: true, url: 'https://api.example.com/v1' });
  assert.deepEqual(normalizeApiUrl(''), { ok: true, url: '' });
});

test('invalid Backend URLs are rejected with a reason', () => {
  for (const bad of ['ftp://host:8000', 'http://', 'http://host:99999', 'http://host:0', 'http://ho st:8000', 'not a url', 'http://host:8000?x=1']) {
    const r = normalizeApiUrl(bad);
    assert.equal(r.ok, false, bad);
    assert.ok(r.reason.length > 0);
  }
});

test('network error messages explain the fix that applies to the URL in use', () => {
  assert.match(networkErrorMessage('http://localhost:8000'), /localhost is the phone itself/);
  assert.match(networkErrorMessage('http://10.0.2.2:8000'), /only works in the Android emulator/);
  const lan = networkErrorMessage('http://192.168.29.82:8000');
  assert.match(lan, /same Wi-Fi/);
  assert.match(lan, /http:\/\/192\.168\.29\.82:8000\/health/);
  assert.ok(!/AIza/.test(lan));
});

import { describeDatabase, validateReady } from '../services/validate.ts';

test('readiness replies are validated and every database state has an actionable message', () => {
  for (const database of ['ok', 'not_configured', 'not_migrated', 'unavailable']) {
    assert.doesNotThrow(() => validateReady({ status: 'ok', database }));
    assert.ok(describeDatabase(database).text.length > 10);
  }
  for (const bad of [null, {}, { status: 'ok' }, { status: 'fine', database: 'ok' }, { status: 'ok', database: 'weird' }, '<html>']) {
    assert.throws(() => validateReady(bad), TypeError);
  }
  assert.equal(describeDatabase('ok').ok, true);
  assert.equal(describeDatabase('unavailable').ok, false);
  assert.match(describeDatabase('not_migrated').text, /python -m app\.db\.migrate/);
  assert.ok(!/postgresql:\/\//.test(describeDatabase('unavailable').text)); // never shows connection strings
});
