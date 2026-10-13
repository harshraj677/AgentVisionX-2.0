import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { categoryList } from '@/constants/privacy';
import { colors, radius, spacing } from '@/constants/theme';
import type { Message } from '@/types/chat';

interface Props {
  message: Message;
  onShowDetails?: (message: Message) => void;
}

export function MessageBubble({ message, onShowDetails }: Props) {
  const isUser = message.role === 'user';
  return (
    <View style={[styles.row, isUser ? styles.rowUser : styles.rowAssistant]}>
      <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAssistant]}>
        <Text selectable style={[styles.text, isUser && { color: colors.textOnPrimary }]}>
          {message.content}
        </Text>
      </View>
      {!isUser && message.metadata?.response_privacy ? <ResponsePrivacyNotice rp={message.metadata.response_privacy} /> : null}
      {!isUser && message.metadata && onShowDetails ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Show request details"
          hitSlop={8}
          style={styles.details}
          onPress={() => onShowDetails(message)}>
          <Ionicons name="stats-chart-outline" size={14} color={colors.textMuted} />
          <Text style={styles.detailsText}>
            {message.metadata.usage.total_tokens != null
              ? `${message.metadata.usage.total_tokens} tokens · `
              : ''}
            {(message.metadata.latency_ms / 1000).toFixed(1)}s · Details
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}

export function ResponsePrivacyNotice({ rp }: { rp: NonNullable<NonNullable<Message['metadata']>['response_privacy']> }) {
  if (rp.status === 'clean') return null;
  const labels = categoryList(rp.categories);
  const look =
    rp.status === 'redacted'
      ? { icon: 'shield-half' as const, color: colors.success, bg: colors.successSoft, text: `Sensitive data was redacted from this reply: ${labels}.` }
      : rp.status === 'warned'
        ? { icon: 'warning' as const, color: colors.warning, bg: colors.warningSoft, text: `This reply may contain sensitive information: ${labels}. Check it before using or sharing.` }
        : { icon: 'information-circle' as const, color: colors.textMuted, bg: colors.surfaceMuted, text: `Detected but allowed by server policy: ${labels}.` };
  return (
    <View style={[styles.notice, { backgroundColor: look.bg }]} accessibilityRole="alert">
      <Ionicons name={look.icon} size={15} color={look.color} />
      <Text style={[styles.noticeText, { color: look.color }]}>{look.text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  notice: { flexDirection: 'row', alignItems: 'flex-start', gap: 6, marginTop: 4, padding: spacing.sm, borderRadius: radius.sm },
  noticeText: { flex: 1, fontSize: 12, lineHeight: 17 },
  row: { marginVertical: spacing.xs, maxWidth: '88%' },
  rowUser: { alignSelf: 'flex-end', alignItems: 'flex-end' },
  rowAssistant: { alignSelf: 'flex-start', alignItems: 'flex-start' },
  bubble: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderRadius: radius.lg },
  bubbleUser: { backgroundColor: colors.primary, borderBottomRightRadius: 4 },
  bubbleAssistant: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderBottomLeftRadius: 4,
  },
  text: { fontSize: 16, lineHeight: 23, color: colors.text },
  details: { flexDirection: 'row', alignItems: 'center', marginTop: 4, marginLeft: 4, gap: 4 },
  detailsText: { fontSize: 12, color: colors.textMuted },
});
