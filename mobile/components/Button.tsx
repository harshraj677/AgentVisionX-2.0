import { ActivityIndicator, Pressable, StyleSheet, Text } from 'react-native';

import { colors, radius, spacing } from '@/constants/theme';

interface Props {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'danger';
  loading?: boolean;
  disabled?: boolean;
  /** Screen-reader name when the visible label is not specific enough (e.g. "Ask" on a list of documents). */
  accessibilityLabel?: string;
}

export function Button({ label, onPress, variant = 'primary', loading, disabled, accessibilityLabel }: Props) {
  const inactive = disabled || loading;
  const textColor = variant === 'primary' ? colors.textOnPrimary : variant === 'danger' ? colors.danger : colors.primary;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ disabled: !!inactive, busy: !!loading }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        styles.base,
        variant === 'primary' && styles.primary,
        variant === 'secondary' && styles.secondary,
        variant === 'danger' && styles.danger,
        (pressed || inactive) && { opacity: inactive ? 0.55 : 0.85 },
      ]}>
      {loading ? <ActivityIndicator color={textColor} /> : <Text style={[styles.label, { color: textColor }]}>{label}</Text>}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 46,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primary: { backgroundColor: colors.primary },
  secondary: { backgroundColor: colors.primarySoft, borderWidth: 1, borderColor: '#BFDBFE' },
  danger: { backgroundColor: colors.dangerSoft, borderWidth: 1, borderColor: '#FECACA' },
  label: { fontSize: 15, fontWeight: '600' },
});
