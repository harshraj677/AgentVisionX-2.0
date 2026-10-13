import AsyncStorage from '@react-native-async-storage/async-storage';

import type { Conversation } from '@/types/chat';

const KEY = 'agentvision.conversations.v1';
const MAX_CONVERSATIONS = 50;

export async function loadConversations(): Promise<Conversation[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export async function saveConversations(conversations: Conversation[]): Promise<void> {
  try {
    const trimmed = conversations.filter((c) => c.messages.length > 0).slice(0, MAX_CONVERSATIONS);
    await AsyncStorage.setItem(KEY, JSON.stringify(trimmed));
  } catch {
    // Local history is a convenience; never crash because of it.
  }
}
