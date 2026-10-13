import { Ionicons } from '@expo/vector-icons';
import { ActivityIndicator, Pressable, StyleSheet, TextInput, View } from 'react-native';

import { colors, radius, spacing } from '@/constants/theme';

// Keep in sync with MAX_MESSAGE_CHARS in backend/.env.example.
export const MAX_INPUT_CHARS = 8000;

interface Props {
  value: string;
  onChangeText: (text: string) => void;
  onSend: () => void;
  disabled?: boolean;
  /** Shows a spinner on the send button (e.g. while the privacy check runs). */
  busy?: boolean;
}

export function ChatInput({ value, onChangeText, onSend, disabled, busy }: Props) {
  const canSend = value.trim().length > 0 && !disabled;

  return (
    <View style={styles.container}>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChangeText}
        placeholder="Message AgentVision X"
        placeholderTextColor={colors.textMuted}
        multiline
        maxLength={MAX_INPUT_CHARS}
        accessibilityLabel="Message input"
      />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Send message"
        accessibilityState={{ disabled: !canSend }}
        disabled={!canSend}
        onPress={onSend}
        style={[styles.send, !canSend && styles.sendDisabled]}>
        {busy ? (
          <ActivityIndicator color={colors.textOnPrimary} />
        ) : (
          <Ionicons name="arrow-up" size={22} color={colors.textOnPrimary} />
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  input: {
    flex: 1,
    maxHeight: 140,
    minHeight: 44,
    paddingHorizontal: spacing.lg,
    paddingTop: 11,
    paddingBottom: 11,
    borderRadius: 22,
    backgroundColor: colors.surfaceMuted,
    borderWidth: 1,
    borderColor: colors.border,
    fontSize: 16,
    color: colors.text,
  },
  send: {
    width: 44,
    height: 44,
    borderRadius: radius.pill,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendDisabled: { backgroundColor: '#93C5FD' },
});
