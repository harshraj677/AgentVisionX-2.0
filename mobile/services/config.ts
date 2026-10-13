import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';

const STORAGE_KEY = 'agentvision.apiUrl.v1';

// 10.0.2.2 is how the Android emulator reaches the PC's localhost.
const FALLBACK_URL = Platform.OS === 'android' ? 'http://10.0.2.2:8000' : 'http://localhost:8000';

export const DEFAULT_API_URL = (process.env.EXPO_PUBLIC_API_URL || FALLBACK_URL).replace(/\/+$/, '');

let currentUrl = DEFAULT_API_URL;

export function getApiUrl(): string {
  return currentUrl;
}

/** Loads a URL the user saved in Settings (if any). Call once at startup. */
export async function initApiUrl(): Promise<void> {
  try {
    const saved = await AsyncStorage.getItem(STORAGE_KEY);
    if (saved) currentUrl = saved;
  } catch {
    // Storage unavailable: keep the default.
  }
}

export async function setApiUrl(url: string): Promise<void> {
  const cleaned = url.trim().replace(/\/+$/, '');
  currentUrl = cleaned || DEFAULT_API_URL;
  try {
    if (cleaned) await AsyncStorage.setItem(STORAGE_KEY, cleaned);
    else await AsyncStorage.removeItem(STORAGE_KEY);
  } catch {
    // Non-fatal: the URL still applies for this session.
  }
}
