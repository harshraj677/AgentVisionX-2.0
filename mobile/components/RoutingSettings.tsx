import { Ionicons } from '@expo/vector-icons';
import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, font, radius, spacing } from '@/constants/theme';
import { getRouterModels } from '@/services/api';
import { getRoutingPreference, setRoutingPreference } from '@/services/preferences';
import type { RoutingPolicy, RouterModels } from '@/types/chat';

const POLICIES: { id: RoutingPolicy; label: string; hint: string }[] = [
  { id: 'balanced', label: 'Balanced', hint: 'Task-mapped model if configured, otherwise the default.' },
  { id: 'cost', label: 'Cost', hint: 'Cheapest model, only if prices are configured for every eligible model.' },
  { id: 'quality', label: 'Quality', hint: 'The model mapped to the detected task, otherwise the default.' },
  { id: 'latency', label: 'Latency', hint: 'Fastest by measured speed, once every eligible model has been measured.' },
  { id: 'manual', label: 'Manual', hint: 'Always use the model you pick below. No fallback.' },
];

/** Real controls only: everything shown here comes from /api/router/models and is saved on this device. */
export function RoutingSettings({ online }: { online: boolean }) {
  const [models, setModels] = useState<RouterModels | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [policy, setPolicy] = useState<RoutingPolicy | null>(null); // null = server default
  const [model, setModel] = useState<string | undefined>();

  const load = useCallback(async () => {
    setError(null);
    try {
      setModels(await getRouterModels());
    } catch (e) {
      setModels(null);
      setError(e instanceof Error ? e.message : 'Could not load models.');
    }
  }, []);

  useEffect(() => {
    void getRoutingPreference().then((p) => {
      setPolicy(p.policy ?? null);
      setModel(p.model);
    });
  }, []);

  useEffect(() => {
    if (online) void load();
  }, [online, load]);

  const save = (nextPolicy: RoutingPolicy | null, nextModel: string | undefined) => {
    setPolicy(nextPolicy);
    setModel(nextModel);
    void setRoutingPreference({ policy: nextPolicy ?? undefined, model: nextModel });
  };

  const choosePolicy = (id: RoutingPolicy) => {
    // Switching to manual pre-selects the default model, visibly, so a request never lacks a model.
    save(id, id === 'manual' ? (model ?? models?.default_model ?? undefined) : model);
  };

  if (!online) return <Text style={font.small}>Connect to the backend to see routing options.</Text>;
  if (error) return <Text style={styles.error}>{error}</Text>;
  if (!models) return <ActivityIndicator color={colors.primary} />;

  const effective = policy ?? models.default_policy;
  const eligible = models.providers.flatMap((p) => p.models.filter((m) => m.available).map((m) => ({ ...m, provider: p.name })));
  const providersInUse = new Set(eligible.map((m) => m.provider));
  const active = POLICIES.find((p) => p.id === effective);

  return (
    <View style={styles.wrap}>
      <Text style={font.small}>
        Default model: <Text style={styles.strong}>{models.default_model ?? 'none available'}</Text>
        {'  ·  '}
        {providersInUse.size <= 1
          ? `only ${providersInUse.size === 0 ? 'no provider' : [...providersInUse][0]} available`
          : `${providersInUse.size} providers available`}
      </Text>
      {eligible.length <= 1 ? (
        <Text style={[font.small, styles.notice]}>
          Only one model is available, so every policy selects it. Add models with GEMINI_MODELS in backend/.env to
          give the router a choice.
        </Text>
      ) : null}

      <View style={styles.chips}>
        {POLICIES.map((p) => (
          <Pressable
            key={p.id}
            accessibilityRole="radio"
            accessibilityState={{ selected: effective === p.id }}
            onPress={() => choosePolicy(p.id)}
            style={[styles.chip, effective === p.id && styles.chipOn]}>
            <Text style={[styles.chipText, effective === p.id && styles.chipTextOn]}>{p.label}</Text>
          </Pressable>
        ))}
      </View>
      {active ? <Text style={font.small}>{active.hint}{policy === null ? ' (server default)' : ''}</Text> : null}

      {effective === 'manual' ? (
        <View style={styles.list}>
          {eligible.map((m) => {
            const chosen = (model ?? models.default_model) === m.id;
            return (
              <Pressable key={`${m.provider}/${m.id}`} accessibilityRole="radio" accessibilityState={{ selected: chosen }} style={styles.row} onPress={() => save('manual', m.id)}>
                <Ionicons name={chosen ? 'radio-button-on' : 'radio-button-off'} size={20} color={chosen ? colors.primary : colors.textMuted} />
                <Text style={styles.rowText}>{m.id}</Text>
                <Text style={font.small}>{m.provider}</Text>
              </Pressable>
            );
          })}
          {eligible.length === 0 ? <Text style={styles.error}>No model is currently eligible for manual selection.</Text> : null}
        </View>
      ) : null}

      <Text style={[font.small, styles.notice]}>
        Cost and latency are only compared when the data exists: prices come from your MODEL_PROFILES setting (none are
        built in), and latency is measured from this backend's own successful calls. Until then those policies use the
        default model.
      </Text>
      <Text style={font.small}>
        Fallback: {models.fallback_mode === 'off' ? 'disabled' : models.fallback_mode === 'cheaper_or_equal' ? 'one retry on a different model, only if it is known to cost no more' : 'one retry on any eligible model'}.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.sm },
  strong: { fontWeight: '600', color: colors.text },
  notice: { backgroundColor: colors.surfaceMuted, padding: spacing.sm, borderRadius: radius.sm },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  chip: { paddingHorizontal: spacing.md, paddingVertical: 8, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: 14, fontWeight: '600', color: colors.text },
  chipTextOn: { color: colors.textOnPrimary },
  list: { gap: 2, marginTop: spacing.xs },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm },
  rowText: { flex: 1, fontSize: 15, color: colors.text },
  error: { color: colors.danger, fontSize: 13, lineHeight: 18 },
});
