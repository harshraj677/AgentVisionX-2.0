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
