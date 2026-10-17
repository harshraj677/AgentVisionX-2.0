import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';

import { ApiError, checkHealth, checkReady } from '@/services/api';
import type { DatabaseState, HealthResponse } from '@/types/chat';

export interface HealthState {
  status: 'checking' | 'online' | 'offline';
  data?: HealthResponse;
  /** Documents database state from /api/ready; undefined if the backend predates that endpoint or the call failed. */
  database?: DatabaseState;
  error?: string;
  /** ApiError code of the failed check (network_error, timeout, invalid_response, ...). */
  errorCode?: string;
}

export function useHealth() {
  const [health, setHealth] = useState<HealthState>({ status: 'checking' });

  const refresh = useCallback(async () => {
    setHealth((prev) => ({ ...prev, status: 'checking', error: undefined, errorCode: undefined }));
    try {
      const data = await checkHealth();
      let database: DatabaseState | undefined;
      try {
        database = (await checkReady()).database;
      } catch {
        // Older backend or a transient failure: liveness is what decides online/offline, so just omit the database line.
      }
      setHealth({ status: 'online', data, database });
    } catch (e) {
      setHealth({ status: 'offline', error: e instanceof Error ? e.message : 'Unknown error', errorCode: e instanceof ApiError ? e.code : undefined });
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
