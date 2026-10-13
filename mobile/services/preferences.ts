import AsyncStorage from '@react-native-async-storage/async-storage';

import type { RoutingPreference } from '@/types/chat';

const OPTIMIZER_KEY = 'agentvision.optimizerEnabled.v1';

/** On by default. Optimization is only ever a suggestion the user must approve. */
export async function getOptimizerEnabled(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(OPTIMIZER_KEY)) !== 'false';
  } catch {
    return true;
  }
}

export async function setOptimizerEnabled(enabled: boolean): Promise<void> {
  try {
    await AsyncStorage.setItem(OPTIMIZER_KEY, String(enabled));
  } catch {
    // Preference only; ignore storage failures.
  }
}

const ROUTING_KEY = 'agentvision.routing.v1';

/** Empty = "use the server's default policy". */
export async function getRoutingPreference(): Promise<RoutingPreference> {
  try {
    const raw = await AsyncStorage.getItem(ROUTING_KEY);
    return raw ? (JSON.parse(raw) as RoutingPreference) : {};
  } catch {
    return {};
  }
}

export async function setRoutingPreference(pref: RoutingPreference): Promise<void> {
  try {
    await AsyncStorage.setItem(ROUTING_KEY, JSON.stringify(pref));
  } catch {
    // Preference only; ignore storage failures.
  }
}
