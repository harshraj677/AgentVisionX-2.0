import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Crypto from 'expo-crypto';

/**
 * Development-mode document scope: a random 256-bit key generated on this device and sent as X-Document-Scope.
 * The server stores only its SHA-256 and shows/uses documents only for that hash. It is a bearer secret, not a login:
 * clearing app data loses access to previously uploaded documents (they stay on the server until deleted there).
 */
const KEY = 'agentvision.documentScope.v1';
const VALID = /^[A-Za-z0-9_-]{32,128}$/;
let cached: string | null = null;

export async function getDocumentScope(): Promise<string> {
  if (cached) return cached;
  try {
    const stored = await AsyncStorage.getItem(KEY);
    if (stored && VALID.test(stored)) {
      cached = stored;
      return stored;
    }
  } catch {
    // fall through and create one
  }
  const hex = Array.from(Crypto.getRandomBytes(32), (b) => b.toString(16).padStart(2, '0')).join('');
  try {
    await AsyncStorage.setItem(KEY, hex);
  } catch {
    // Storage unavailable: the key lives only for this session.
  }
  cached = hex;
  return hex;
}
