import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useState } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { colors } from '@/constants/theme';
import { ConversationsProvider } from '@/hooks/ConversationsContext';
import { initApiUrl } from '@/services/config';

export default function RootLayout() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    initApiUrl().finally(() => setReady(true));
  }, []);

  if (!ready) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ConversationsProvider>
          <StatusBar style="dark" />
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }} />
        </ConversationsProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
