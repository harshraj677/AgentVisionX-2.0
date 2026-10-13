import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';

import { checkHealth } from '@/services/api';
import type { HealthResponse } from '@/types/chat';

export interface HealthState {
  status: 'checking' | 'online' | 'offline';
  data?: HealthResponse;
  error?: string;
}

export function useHealth() {
  const [health, setHealth] = useState<HealthState>({ status: 'checking' });

  const refresh = useCallback(async () => {
    setHealth((prev) => ({ ...prev, status: 'checking', error: undefined }));
    try {
      const data = await checkHealth();
      setHealth({ status: 'online', data });
    } catch (e) {
      setHealth({ status: 'offline', error: e instanceof Error ? e.message : 'Unknown error' });
    }
  }, []);

  // Re-check every time the screen comes into focus.
  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  return { health, refresh };
}
