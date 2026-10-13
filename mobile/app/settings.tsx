import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import { AppHeader } from '@/components/AppHeader';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { RoutingSettings } from '@/components/RoutingSettings';
import { Screen } from '@/components/Screen';
import { StatusPill } from '@/components/StatusPill';
import { colors, font, radius, spacing } from '@/constants/theme';
import { useConversations } from '@/hooks/ConversationsContext';
import { useHealth } from '@/hooks/useHealth';
import { DEFAULT_API_URL, getApiUrl, setApiUrl } from '@/services/config';
import { getOptimizerEnabled, setOptimizerEnabled } from '@/services/preferences';

export default function SettingsScreen() {
  const router = useRouter();
  const { health, refresh } = useHealth();
  const { clearAll, conversations } = useConversations();
  const [url, setUrl] = useState(getApiUrl());
  const [optimizer, setOptimizer] = useState(true);

  useEffect(() => {
    void getOptimizerEnabled().then(setOptimizer);
  }, []);

  const toggleOptimizer = (value: boolean) => {
    setOptimizer(value);
    void setOptimizerEnabled(value);
  };

  const saveAndTest = async () => {
    await setApiUrl(url);
    setUrl(getApiUrl());
    await refresh();
  };

  const resetUrl = async () => {
    await setApiUrl('');
    setUrl(getApiUrl());
    await refresh();
  };

  const confirmClear = () =>
    Alert.alert('Delete all conversations?', 'This removes the history stored on this device.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: clearAll },
    ]);

  const providers = health.data?.providers ?? {};

  return (
    <Screen>
      <AppHeader title="Settings" />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={styles.section}>BACKEND CONNECTION</Text>
          <Card>
            <StatusPill status={health.status} />
            {health.status === 'online' && health.data ? (
              <View style={styles.info}>
                <Text style={font.small}>Backend version {health.data.version}</Text>
                {Object.entries(providers).map(([name, ok]) => (
                  <Text key={name} style={[font.small, { color: ok ? colors.success : colors.textMuted }]}>
                    {name}: {ok ? 'API key configured' : 'not configured'}
                  </Text>
                ))}
              </View>
            ) : null}
            {health.status === 'offline' ? <Text style={styles.error}>{health.error}</Text> : null}

            <Text style={styles.fieldLabel}>Backend URL</Text>
            <TextInput
              style={styles.input}
              value={url}
              onChangeText={setUrl}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder={DEFAULT_API_URL}
              placeholderTextColor={colors.textMuted}
              accessibilityLabel="Backend URL"
            />
            <Text style={font.small}>
              Emulator: http://10.0.2.2:8000 · Physical phone: http://YOUR-PC-IP:8000
            </Text>
            <View style={styles.buttons}>
              <View style={styles.flex}>
                <Button label="Save & test" onPress={saveAndTest} loading={health.status === 'checking'} />
              </View>
              <View style={styles.flex}>
                <Button label="Reset" variant="secondary" onPress={resetUrl} />
              </View>
            </View>
          </Card>

          <Text style={styles.section}>MODEL ROUTING</Text>
          <Card>
            <RoutingSettings online={health.status === 'online'} />
          </Card>

          <Text style={styles.section}>PROMPT OPTIMIZER</Text>
          <Card>
            <View style={styles.switchRow}>
              <View style={styles.flex}>
                <Text style={font.body}>Suggest shorter prompts</Text>
                <Text style={font.small}>
                  Local rules remove filler and repetition. You compare and choose the original, the optimized version
                  or your own edit before anything is sent. Token figures are estimates, not measured savings.
                </Text>
              </View>
              <Switch value={optimizer} onValueChange={toggleOptimizer} accessibilityLabel="Prompt optimizer" />
            </View>
          </Card>

          <Text style={styles.section}>PRIVACY</Text>
          <Card>
            <Text style={font.body}>
              Before a message is sent to Gemini, your backend scans it locally with Microsoft Presidio and custom
              rules for API keys, passwords, emails, phone numbers and IDs. Risky items can be blocked, redacted or
              confirmed by you. Detection is rule- and model-based and can miss things, so don't treat it as a guarantee.
            </Text>
            <Text style={[font.small, { marginTop: spacing.sm }]}>
              API keys are stored only on the backend. Prompts and detected values are not written to backend logs.
            </Text>
            <Text style={[font.small, { marginTop: spacing.sm }]}>
              AI replies are scanned too before they reach this app: secrets are redacted by default and contact details
              are flagged.{' '}
              {health.status === 'online' && health.data?.privacy?.response_policy
                ? `Reply policy on this server: ${health.data.privacy.response_policy === 'policy' ? 'per-category defaults' : health.data.privacy.response_policy} (set in backend/.env, not here).`
                : ''}
            </Text>
            <Text style={[font.small, { marginTop: spacing.sm, color: health.data?.privacy?.ready ? colors.success : colors.textMuted }]}>
              Privacy engine:{' '}
              {health.status !== 'online'
                ? 'unknown (backend offline)'
                : health.data?.privacy
                  ? health.data.privacy.ready ? 'Presidio loaded' : 'Presidio (loads on first use)'
                  : 'not reported by this backend version'}
            </Text>
          </Card>

          <Text style={styles.section}>DATA</Text>
          <Card>
            <Text style={[font.small, { marginBottom: spacing.md }]}>
              {conversations.filter((c) => c.messages.length > 0).length} conversations stored on this device.
            </Text>
            <Button label="Delete all conversations" variant="danger" onPress={confirmClear} />
            <View style={{ height: spacing.sm }} />
            <Button label="View all conversations" variant="secondary" onPress={() => router.push('/history')} />
          </Card>

          <Text style={[font.small, styles.footer]}>AgentVision X · MVP 0.1.0</Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: { padding: spacing.lg, paddingBottom: spacing.xxl },
  section: { ...font.label, marginTop: spacing.xl, marginBottom: spacing.sm, marginLeft: spacing.xs },
  info: { marginTop: spacing.md, gap: 2 },
  error: { color: colors.danger, fontSize: 13, lineHeight: 18, marginTop: spacing.md },
  fieldLabel: { ...font.label, marginTop: spacing.lg, marginBottom: spacing.xs },
  input: {
    minHeight: 46,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    fontSize: 15,
    color: colors.text,
    backgroundColor: colors.surfaceMuted,
    marginBottom: spacing.sm,
  },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  buttons: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.md },
  footer: { textAlign: 'center', marginTop: spacing.xl },
});
