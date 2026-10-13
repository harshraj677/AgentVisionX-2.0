import { Ionicons } from '@expo/vector-icons';
import { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { MAX_INPUT_CHARS } from '@/components/ChatInput';
import { categoryLabel } from '@/constants/privacy';
import { colors, font, radius, spacing } from '@/constants/theme';
import { redactPrompt, scanPrompt } from '@/services/api';
import type { PrivacyAction, PrivacyFinding, PrivacyScan } from '@/types/chat';

interface Props {
  /** The prompt under review. */
  text: string;
  initialScan: PrivacyScan;
  onCancel: () => void;
  /** Called with the final text and the scan of exactly that text. It is permitted to be sent. */
  onSend: (finalText: string, scan: PrivacyScan) => void;
}

const HEADLINE: Record<PrivacyAction, { title: string; body: string; icon: keyof typeof Ionicons.glyphMap; fg: string; bg: string }> = {
  allow: { title: 'No sensitive information found', body: 'This prompt is clear to send.', icon: 'shield-checkmark', fg: colors.success, bg: colors.successSoft },
  warn: { title: 'Possible personal information', body: 'You can edit or redact it, or send it anyway.', icon: 'alert-circle', fg: colors.warning, bg: colors.warningSoft },
  redact: { title: 'Redaction required', body: 'Redact or edit these items before this can be sent.', icon: 'eye-off', fg: colors.danger, bg: colors.dangerSoft },
  block: { title: 'Blocked by privacy policy', body: 'This was not sent. Redact or edit the sensitive items to continue.', icon: 'shield', fg: colors.danger, bg: colors.dangerSoft },
};

const label = categoryLabel;

const ACTION_COLOR: Record<PrivacyAction, string> = {
  allow: colors.success,
  warn: colors.warning,
  redact: colors.danger,
  block: colors.danger,
};

/** Splits text into plain and highlighted pieces. Offsets are code points, so slice by code point. */
function segments(text: string, findings: PrivacyFinding[]) {
  const chars = Array.from(text);
  const out: { text: string; finding?: PrivacyFinding }[] = [];
  let cursor = 0;
  for (const f of [...findings].sort((a, b) => a.start - b.start)) {
    if (f.start > cursor) out.push({ text: chars.slice(cursor, f.start).join('') });
    out.push({ text: chars.slice(f.start, f.end).join(''), finding: f });
    cursor = f.end;
  }
  if (cursor < chars.length) out.push({ text: chars.slice(cursor).join('') });
  return out;
}

export function PrivacyReviewPanel({ text, initialScan, onCancel, onSend }: Props) {
  const [draft, setDraft] = useState(text);
  const [scan, setScan] = useState<PrivacyScan | null>(initialScan);
  const [scannedText, setScannedText] = useState(text);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sending is only allowed for the exact text that was last scanned.
  const stale = draft !== scannedText;
  const decision: PrivacyAction = scan?.decision ?? 'allow';
  const look = HEADLINE[decision];
  const canSend = !!scan && !stale && !busy && scan.can_proceed && draft.trim().length > 0;
  const parts = useMemo(() => (scan && !stale ? segments(draft, scan.findings) : [{ text: draft }]), [draft, scan, stale]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The privacy check failed.');
    } finally {
      setBusy(false);
    }
  };

  const rescan = () =>
    run(async () => {
      const result = await scanPrompt(draft);
      setScan(result);
      setScannedText(draft);
    });

  const redact = () =>
    run(async () => {
      const result = await redactPrompt(draft);
      setDraft(result.redacted_text);
      setScan(result.scan); // the backend re-scanned the redacted text
      setScannedText(result.redacted_text);
      setEditing(false);
    });

  return (
    <SafeAreaView style={styles.safe}>
        <KeyboardAvoidingView style={styles.flex} behavior="padding">
          <View style={styles.header}>
            <Text style={font.heading}>Privacy review</Text>
            <Pressable accessibilityRole="button" accessibilityLabel="Cancel" hitSlop={8} onPress={onCancel}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>

          <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
            <View style={[styles.banner, { backgroundColor: stale ? colors.surfaceMuted : look.bg }]}>
              <Ionicons name={stale ? 'refresh-circle' : look.icon} size={24} color={stale ? colors.textMuted : look.fg} />
              <View style={styles.flex}>
                <Text style={[styles.bannerTitle, { color: stale ? colors.text : look.fg }]}>
                  {stale ? 'Prompt changed' : look.title}
                </Text>
                <Text style={font.small}>{stale ? 'Re-scan to check the edited prompt before sending.' : look.body}</Text>
              </View>
            </View>

            {scan && !stale && scan.findings.length > 0 ? (
              <View style={styles.chips}>
                {Object.entries(scan.categories).map(([category, count]) => {
                  const action = scan.findings.find((f) => f.category === category)?.action ?? 'warn';
                  return (
                    <View key={category} style={[styles.chip, { borderColor: ACTION_COLOR[action] }]}>
                      <Text style={[styles.chipText, { color: ACTION_COLOR[action] }]}>
                        {label(category)} ×{count} · {action}
                      </Text>
                    </View>
                  );
                })}
              </View>
            ) : null}

            <Text style={[font.label, styles.sectionLabel]}>YOUR PROMPT</Text>
            {editing ? (
              <TextInput
                style={styles.editor}
                value={draft}
                onChangeText={setDraft}
                multiline
                autoFocus
                maxLength={MAX_INPUT_CHARS}
                accessibilityLabel="Edit prompt"
              />
            ) : (
              <View style={styles.preview}>
                <Text style={styles.previewText} selectable>
                  {parts.map((p, i) => (
                    <Text
                      key={i}
                      style={p.finding ? { backgroundColor: p.finding.action === 'warn' ? '#FEF3C7' : '#FEE2E2', color: ACTION_COLOR[p.finding.action] } : undefined}>
                      {p.text}
                    </Text>
                  ))}
                </Text>
              </View>
            )}

            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Text style={[font.small, styles.note]}>
              Scanning happens on your own backend; this text has not been sent to any AI provider.
            </Text>
          </ScrollView>

          <View style={styles.footer}>
            {busy ? <ActivityIndicator style={styles.spinner} color={colors.primary} /> : null}
            <View style={styles.row}>
              {scan && !stale && scan.findings.length > 0 ? (
                <View style={styles.flex}>
                  <Button label="Redact" variant="secondary" onPress={redact} disabled={busy} />
                </View>
              ) : null}
              <View style={styles.flex}>
                {editing || stale ? (
                  <Button label="Re-scan" variant="secondary" onPress={() => { setEditing(false); void rescan(); }} disabled={busy || draft.trim().length === 0} />
                ) : (
                  <Button label="Edit" variant="secondary" onPress={() => setEditing(true)} disabled={busy} />
                )}
              </View>
            </View>
            <View style={styles.row}>
              <View style={styles.flex}>
                <Button label="Cancel" variant="danger" onPress={onCancel} />
              </View>
              <View style={styles.flex}>
                <Button
                  label={decision === 'warn' ? 'Send anyway' : 'Send'}
                  onPress={() => scan && onSend(draft, scan)}
                  disabled={!canSend}
                />
              </View>
            </View>
          </View>
        </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  body: { padding: spacing.lg },
  banner: { flexDirection: 'row', gap: spacing.md, alignItems: 'center', padding: spacing.lg, borderRadius: radius.lg },
  bannerTitle: { fontSize: 16, fontWeight: '600', marginBottom: 2 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.md },
  chip: { borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: spacing.md, paddingVertical: 4, backgroundColor: colors.surface },
  chipText: { fontSize: 12, fontWeight: '600' },
  sectionLabel: { marginTop: spacing.xl, marginBottom: spacing.sm },
  preview: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: spacing.lg },
  previewText: { fontSize: 16, lineHeight: 24, color: colors.text },
  editor: {
    minHeight: 140,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.primary,
    borderRadius: radius.md,
    padding: spacing.lg,
    fontSize: 16,
    lineHeight: 24,
    color: colors.text,
    textAlignVertical: 'top',
  },
  error: { color: colors.danger, fontSize: 13, marginTop: spacing.md },
  note: { marginTop: spacing.md },
  footer: { padding: spacing.lg, gap: spacing.sm, backgroundColor: colors.surface, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  spinner: { alignSelf: 'center' },
  row: { flexDirection: 'row', gap: spacing.sm },
});
