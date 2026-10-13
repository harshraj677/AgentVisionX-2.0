import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { categoryList } from '@/constants/privacy';
import { colors, radius, spacing } from '@/constants/theme';
import type { ResponseMetadata } from '@/types/chat';

const fmt = (n: number | null) => (n == null ? 'Unavailable' : n.toLocaleString());

function replyScanText(rp: NonNullable<ResponseMetadata['response_privacy']>) {
  const labels = categoryList(rp.categories);
  switch (rp.status) {
    case 'clean':
      return 'Passed, nothing found';
    case 'redacted':
      return `Redacted ${rp.redacted_spans} item${rp.redacted_spans === 1 ? '' : 's'}: ${labels}`;
    case 'warned':
      return `Warning: ${labels}`;
    case 'allowed':
      return `Detected, allowed: ${labels}`;
    default:
      return 'Withheld';
  }
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.label}>{label}</Text>
      <Text style={styles.value} selectable>
        {value}
      </Text>
    </View>
  );
}

export function UsageDetailsModal({
  metadata,
  onClose,
}: {
  metadata: ResponseMetadata | null;
  onClose: () => void;
}) {
  const usage = metadata?.usage;
  return (
    <Modal visible={!!metadata} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} accessibilityLabel="Close details" />
      {metadata && usage ? (
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Request details</Text>
          <Row label="Provider" value={metadata.provider} />
          <Row label="Model" value={metadata.model} />
          {metadata.routing ? (
            <>
              <Row
                label="Task type"
                value={metadata.routing.task_type === 'unknown' ? 'unknown' : `${metadata.routing.task_type} (${metadata.routing.task_confidence} confidence)`}
              />
              <Row label="Routing policy" value={metadata.routing.routing_policy} />
              <Row
                label="Fallback"
                value={metadata.routing.fallback_used ? `Yes: ${metadata.routing.fallback_reason ?? 'earlier model failed'}` : 'No'}
              />
            </>
          ) : null}
          <Row label="Input tokens" value={fmt(usage.input_tokens)} />
          <Row label="Output tokens" value={fmt(usage.output_tokens)} />
          <Row label="Total tokens" value={fmt(usage.total_tokens)} />
          <Row label="Latency" value={`${metadata.latency_ms.toLocaleString()} ms`} />
          <Row label="Status" value={metadata.status} />
          {metadata.privacy ? (
            <Row
              label="Privacy check"
              value={
                metadata.privacy.decision === 'allow'
                  ? 'Passed, nothing found'
                  : `${metadata.privacy.decision}: ${Object.keys(metadata.privacy.categories).join(', ')}`
              }
            />
          ) : null}
          {metadata.response_privacy ? (
            <>
              <Row label="Reply scan" value={replyScanText(metadata.response_privacy)} />
              <Row label="Reply policy" value={metadata.response_privacy.policy === 'policy' ? 'per-category (default)' : metadata.response_privacy.policy} />
            </>
          ) : null}
          {metadata.retrieval ? (
            <Row
              label="Document retrieval"
              value={`${metadata.retrieval.used} passage${metadata.retrieval.used === 1 ? '' : 's'} used of ${metadata.retrieval.candidates} (min similarity ${metadata.retrieval.min_similarity})`}
            />
          ) : null}
          {metadata.search ? (
            <>
              <Row label="Web search" value={`Tavily (${metadata.search.depth}), ${metadata.search.search_latency_ms} ms`} />
              <Row
                label="Sources used"
                value={`${metadata.search.sources_used} of ${metadata.search.results_returned} returned${metadata.search.rejected_invalid_url + metadata.search.rejected_duplicate + metadata.search.rejected_empty > 0 ? ` (${metadata.search.rejected_invalid_url + metadata.search.rejected_duplicate + metadata.search.rejected_empty} rejected)` : ''}`}
              />
            </>
          ) : null}
          {metadata.prompt_source ? <Row label="Prompt sent" value={metadata.prompt_source} /> : null}
          <Row label="Request ID" value={metadata.request_id} />
          {metadata.response_privacy ? <Text style={styles.note}>Reply scanning is a best-effort safeguard and can miss sensitive data or flag harmless text.</Text> : null}
          {metadata.routing ? <Text style={styles.note}>Why this model: {metadata.routing.routing_reason}</Text> : null}
          <Text style={styles.note}>
            {usage.source === 'provider'
              ? 'Token counts are reported by the AI provider. Output tokens may exclude internal reasoning tokens, so input + output can be lower than the total.'
              : 'The provider did not report token usage for this request.'}
            {' '}Cost estimates are not available yet.
          </Text>
          <Pressable accessibilityRole="button" style={styles.close} onPress={onClose}>
            <Text style={styles.closeText}>Close</Text>
          </Pressable>
        </View>
      ) : null}
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(15,23,42,0.4)' },
  sheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    padding: spacing.xl,
    paddingBottom: spacing.xxl,
  },
  handle: {
    alignSelf: 'center',
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.border,
    marginBottom: spacing.lg,
  },
  title: { fontSize: 18, fontWeight: '600', color: colors.text, marginBottom: spacing.md },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  label: { fontSize: 14, color: colors.textMuted },
  value: { fontSize: 14, fontWeight: '500', color: colors.text, flexShrink: 1, textAlign: 'right', marginLeft: spacing.lg },
  note: { fontSize: 12, lineHeight: 17, color: colors.textMuted, marginTop: spacing.md },
  close: {
    marginTop: spacing.lg,
    minHeight: 46,
    borderRadius: radius.md,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeText: { color: colors.primary, fontWeight: '600', fontSize: 15 },
});
