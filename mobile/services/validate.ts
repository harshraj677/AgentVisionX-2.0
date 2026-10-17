/** Shape checks for successful backend replies. Pure (no React Native imports) so they can be tested with plain Node.
 *  They check only what the UI dereferences, and never include any of the received content in an error message. */

export const INVALID_RESPONSE_MESSAGE =
  'The server sent a reply the app could not understand. Check that the Backend URL in Settings points at AgentVision X.';

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Throws if `body` is not a JSON object. Returns it otherwise. */
export function expectObject(body: unknown): Record<string, unknown> {
  if (!isObject(body)) throw new TypeError('not an object');
  return body;
}

export function validateChat(body: unknown): void {
  const b = expectObject(body);
  const m = b.message;
  if (!isObject(m) || m.role !== 'assistant' || typeof m.content !== 'string') throw new TypeError('bad chat message');
  if (!isObject(b.metadata)) throw new TypeError('bad chat metadata');
}

export function validateHealth(body: unknown): void {
  const b = expectObject(body);
  if (b.status !== 'ok' || !isObject(b.providers)) throw new TypeError('bad health');
}

export function validateDocumentList(body: unknown): void {
  const b = expectObject(body);
  if (!Array.isArray(b.documents) || !b.documents.every(isObject)) throw new TypeError('bad document list');
}

export type UrlCheck = { ok: true; url: string } | { ok: false; reason: string };

/** Cleans a user-typed Backend URL. A missing scheme ("192.168.1.5:8000") is the most common typo and would make
 *  fetch() throw, which used to be reported as "cannot reach the backend". Empty input means "use the default". */
export function normalizeApiUrl(input: string): UrlCheck {
  const raw = input.trim();
  if (!raw) return { ok: true, url: '' };
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `http://${raw}`;
  const m = /^(https?):\/\/([^\s/:?#]+)(:(\d{1,5}))?(\/[^\s?#]*)?$/i.exec(withScheme);
  if (!m) return { ok: false, reason: 'Enter a URL like http://192.168.1.23:8000 (http or https, a host and optionally a port).' };
  if (m[4] !== undefined && (Number(m[4]) < 1 || Number(m[4]) > 65535)) return { ok: false, reason: 'The port must be between 1 and 65535.' };
  return { ok: true, url: `${m[1].toLowerCase()}://${m[2]}${m[3] ?? ''}${(m[5] ?? '').replace(/\/+$/, '')}` };
}

/** Explains why a request never reached the server, with the fixes that apply to the URL in use. */
export function networkErrorMessage(apiUrl: string): string {
  const host = /^https?:\/\/([^/:]+)/i.exec(apiUrl)?.[1]?.toLowerCase() ?? '';
  let hint: string;
  if (host === 'localhost' || host === '127.0.0.1') {
    hint = 'On a phone, localhost is the phone itself. Use your PC\'s Wi-Fi IP address instead.';
  } else if (host === '10.0.2.2') {
    hint = '10.0.2.2 only works in the Android emulator. On a real phone use your PC\'s Wi-Fi IP address.';
  } else {
    hint = `Check that the backend is running, the phone is on the same Wi-Fi as the PC, and Windows Firewall allows the port. Open ${apiUrl}/health in the phone's browser to test.`;
  }
  return `Cannot reach the backend at ${apiUrl}. ${hint} You can change the URL in Settings.`;
}

const DATABASE_STATES = ['not_configured', 'ok', 'not_migrated', 'unavailable'];

export function validateReady(body: unknown): void {
  const b = expectObject(body);
  if ((b.status !== 'ok' && b.status !== 'degraded') || typeof b.database !== 'string' || !DATABASE_STATES.includes(b.database)) {
    throw new TypeError('bad readiness');
  }
}

/** What the documents database state means for the user, and what to do about it. */
export function describeDatabase(state: string): { ok: boolean; text: string } {
  switch (state) {
    case 'ok':
      return { ok: true, text: 'Documents database: connected' };
    case 'not_configured':
      return { ok: false, text: 'Documents are not set up on the server (no DATABASE_URL). Chat still works.' };
    case 'not_migrated':
      return { ok: false, text: 'The documents database is reachable but not set up. On the PC run: python -m app.db.migrate' };
    default:
      return { ok: false, text: 'The backend cannot reach its documents database. Check that PostgreSQL (Docker) is running. Chat still works.' };
  }
}
