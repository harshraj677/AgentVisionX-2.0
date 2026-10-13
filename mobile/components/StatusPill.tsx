import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radius, spacing } from '@/constants/theme';
import type { HealthState } from '@/hooks/useHealth';

const LOOK = {
  online: { label: 'Backend online', fg: colors.success, bg: colors.successSoft },
  offline: { label: 'Backend offline', fg: colors.danger, bg: colors.dangerSoft },
  checking: { label: 'Checking…', fg: colors.textMuted, bg: colors.surfaceMuted },
} as const;

export function StatusPill({ status, onPress }: { status: HealthState['status']; onPress?: () => void }) {
  const look = LOOK[status];
  return (
    <Pressable disabled={!onPress} onPress={onPress} accessibilityRole="button" accessibilityLabel={look.label}>
      <View style={[styles.pill, { backgroundColor: look.bg }]}>
        <View style={[styles.dot, { backgroundColor: look.fg }]} />
        <Text style={[styles.text, { color: look.fg }]}>{look.label}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.pill,
  },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: 6 },
  text: { fontSize: 12, fontWeight: '600' },
});
