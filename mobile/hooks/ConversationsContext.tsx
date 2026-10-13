import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { categoryList } from '@/constants/privacy';
import { ApiError, sendChat } from '@/services/api';
import { getRoutingPreference } from '@/services/preferences';
import { loadConversations, saveConversations } from '@/services/storage';
import type { Conversation, Message, PromptSource } from '@/types/chat';

export interface RequestStatus {
  state: 'idle' | 'loading' | 'error';
  error?: string;
  code?: string;
}

interface ConversationsValue {
  conversations: Conversation[];
  loaded: boolean;
  getConversation: (id: string | undefined) => Conversation | undefined;
  statusOf: (id: string | undefined) => RequestStatus;
  /** Sends a user message. Creates the conversation if `id` is undefined and returns its id. */
  send: (id: string | undefined, text: string, source?: PromptSource) => string;
  /** Re-sends the conversation as it is (used after an error). */
  retry: (id: string) => void;
  remove: (id: string) => void;
  clearAll: () => void;
}

const Ctx = createContext<ConversationsValue | null>(null);

const makeId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
const IDLE: RequestStatus = { state: 'idle' };
const MAX_CONTEXT_MESSAGES = 30;

export function ConversationsProvider({ children }: { children: ReactNode }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [statuses, setStatuses] = useState<Record<string, RequestStatus>>({});
  const [loaded, setLoaded] = useState(false);
  const latest = useRef<Conversation[]>([]);

  const update = useCallback((fn: (prev: Conversation[]) => Conversation[]) => {
    latest.current = fn(latest.current);
    setConversations(latest.current);
  }, []);

  useEffect(() => {
    loadConversations().then((stored) => {
      // Keep anything created before loading finished.
      const existing = latest.current;
      const ids = new Set(existing.map((c) => c.id));
      latest.current = [...existing, ...stored.filter((c) => !ids.has(c.id))];
      setConversations(latest.current);
      setLoaded(true);
    });
  }, []);

  useEffect(() => {
    if (loaded) saveConversations(conversations);
  }, [conversations, loaded]);

  const setStatus = useCallback((id: string, status: RequestStatus) => {
    setStatuses((prev) => ({ ...prev, [id]: status }));
  }, []);

  const runRequest = useCallback(
    async (id: string) => {
      const conv = latest.current.find((c) => c.id === id);
      if (!conv) return;
      setStatus(id, { state: 'loading' });
      try {
        const payload = conv.messages
          .slice(-MAX_CONTEXT_MESSAGES)
          .map((m) => ({ role: m.role, content: m.content }));
        const lastUser = [...conv.messages].reverse().find((m) => m.role === 'user');
        const res = await sendChat(payload, lastUser?.promptSource, await getRoutingPreference());
        const reply: Message = {
          id: makeId(),
          role: 'assistant',
          content: res.message.content,
          createdAt: Date.now(),
          metadata: res.metadata,
        };
        update((prev) =>
          prev.map((c) =>
            c.id === id ? { ...c, messages: [...c.messages, reply], updatedAt: Date.now() } : c,
          ),
        );
        setStatus(id, IDLE);
      } catch (e) {
        if (e instanceof ApiError && e.code.startsWith('response_privacy_')) {
          // The server withheld the AI's reply. Nothing from it exists on the device: it is never in the error
          // payload, so it cannot reach history or storage. The user's own message stays so they can retry.
          const found = (e.details as { response_privacy?: { categories?: Record<string, number> } } | undefined)?.response_privacy?.categories;
          const detail = found && Object.keys(found).length ? ` (detected: ${categoryList(found)})` : '';
          const message =
            e.code === 'response_privacy_blocked'
              ? `The AI's reply was withheld by the response privacy policy${detail}. Nothing was saved. You can retry or rephrase.`
              : e.message;
          setStatus(id, { state: 'error', error: message, code: e.code });
          return;
        }
        if (e instanceof ApiError && e.code.startsWith('privacy_')) {
          // The backend refused this conversation on privacy grounds, so nothing was sent to the AI.
          // Drop the offending user message so a retry can't loop on it.
          update((prev) =>
            prev.map((c) => (c.id === id ? { ...c, messages: c.messages.slice(0, -1) } : c)),
          );
          setStatus(id, { state: 'error', error: e.message, code: e.code });
          return;
        }
        setStatus(id, { state: 'error', error: e instanceof Error ? e.message : 'Something went wrong.' });
      }
    },
    [setStatus, update],
  );

  const send = useCallback(
    (id: string | undefined, text: string, source?: PromptSource): string => {
      const now = Date.now();
      const message: Message = { id: makeId(), role: 'user', content: text, createdAt: now, promptSource: source };
      const convId = id ?? makeId();
      if (id && latest.current.some((c) => c.id === id)) {
        update((prev) =>
          prev.map((c) => (c.id === id ? { ...c, messages: [...c.messages, message], updatedAt: now } : c)),
        );
      } else {
        const title = text.length > 40 ? `${text.slice(0, 40).trim()}…` : text;
        update((prev) => [
          { id: convId, title, createdAt: now, updatedAt: now, messages: [message] },
          ...prev,
        ]);
      }
      void runRequest(convId);
      return convId;
    },
    [runRequest, update],
  );

  const retry = useCallback((id: string) => void runRequest(id), [runRequest]);

  const remove = useCallback(
    (id: string) => update((prev) => prev.filter((c) => c.id !== id)),
    [update],
  );

  const clearAll = useCallback(() => update(() => []), [update]);

  const value = useMemo<ConversationsValue>(
    () => ({
      conversations,
      loaded,
      getConversation: (id) => (id ? conversations.find((c) => c.id === id) : undefined),
      statusOf: (id) => (id ? (statuses[id] ?? IDLE) : IDLE),
      send,
      retry,
      remove,
      clearAll,
    }),
    [conversations, loaded, statuses, send, retry, remove, clearAll],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConversations(): ConversationsValue {
  const value = useContext(Ctx);
  if (!value) throw new Error('useConversations must be used inside ConversationsProvider');
  return value;
}
