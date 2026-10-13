import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { colors, radius, spacing } from '@/constants/theme';

export function TypingIndicator() {
  return (
    <View style={styles.bubble} accessibilityLiveRegion="polite" accessibilityLabel="Assistant is thinking">
      <ActivityIndicator size="small" color={colors.primary} />
      <Text style={styles.text}>Thinking…</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  bubble: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: spacing.sm,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    marginVertical: spacing.xs,
  },
  text: { fontSize: 14, color: colors.textMuted },
});
