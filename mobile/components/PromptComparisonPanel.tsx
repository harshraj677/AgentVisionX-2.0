import { Ionicons } from '@expo/vector-icons';
import { useState } from 'react';
import {
  KeyboardAvoidingView,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { MAX_INPUT_CHARS } from '@/components/ChatInput';
import { colors, font, radius, spacing } from '@/constants/theme';
import type { OptimizeResult, PrivacyScan, PromptSource } from '@/types/chat';

type Choice = 'original' | 'optimized' | 'custom';

interface Props {
  original: string;
  /** Source label for the original text (it is 'edited' if the user already redacted/edited it in privacy review). */
  originalSource: PromptSource;
  result: OptimizeResult;
  onCancel: () => void;
  /** The chosen text. It is NOT sent yet: the caller re-scans exactly this text first. */
  onUse: (text: string, source: PromptSource) => void;
}

function privacyLabel(scan: PrivacyScan) {
  return scan.decision === 'allow'
    ? { text: 'Privacy: nothing found', color: colors.success }
    : { text: `Privacy: ${scan.decision} (${Object.keys(scan.categories).length} type${Object.keys(scan.categories).length === 1 ? '' : 's'})`, color: colors.danger };
}

function OptionCard(props: {
  title: string;
  selected: boolean;
  onSelect: () => void;
  meta: string;
  privacy?: PrivacyScan;
  children: React.ReactNode;
  wide: boolean;
}) {
  const priv = props.privacy ? privacyLabel(props.privacy) : null;
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ selected: props.selected }}
      onPress={props.onSelect}
      style={[styles.card, props.selected && styles.cardSelected, props.wide && styles.flex]}>
      <View style={styles.cardHeader}>
        <Ionicons name={props.selected ? 'radio-button-on' : 'radio-button-off'} size={22} color={props.selected ? colors.primary : colors.textMuted} />
        <Text style={styles.cardTitle}>{props.title}</Text>
      </View>
      {props.children}
      <Text style={styles.meta}>{props.meta}</Text>
      {priv ? <Text style={[styles.meta, { color: priv.color }]}>{priv.text}</Text> : null}
    </Pressable>
  );
}

export function PromptComparisonPanel({ original, originalSource, result, onCancel, onUse }: Props) {
  const [choice, setChoice] = useState<Choice>('optimized');
  const [custom, setCustom] = useState(result.optimized_text);
  const { width } = useWindowDimensions();
  const wide = width >= 700;
  const t = result.tokens;

  const selectedText = choice === 'original' ? original : choice === 'optimized' ? result.optimized_text : custom;
  const source: PromptSource =
    choice === 'original' ? originalSource : choice === 'optimized' ? 'optimized' : custom === result.optimized_text ? 'optimized' : custom === original ? originalSource : 'edited';

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView style={styles.flex} behavior="padding">
        <View style={styles.header}>
          <Text style={font.heading}>Compare prompts</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Cancel" hitSlop={8} onPress={onCancel}>
            <Ionicons name="close" size={26} color={colors.text} />
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
          <View style={styles.summary}>
            <Text style={styles.summaryTitle}>
              {t.saved > 0
                ? `Estimated ${t.original} → ${t.optimized} tokens (−${t.saved}, ${t.saved_percent}%)`
                : `Estimated ${t.original} → ${t.optimized} tokens (no estimated saving)`}
            </Text>
            <Text style={font.small}>
              Estimate only, from {t.method}. The AI provider counts differently, and real savings are not measured.
              Exact usage is shown after sending, for the version you send.
            </Text>
            {result.changes.map((c) => (
              <Text key={c.rule} style={styles.change}>
                • {c.description}
                {c.count > 1 ? ` ×${c.count}` : ''}
              </Text>
            ))}
          </View>

          <View style={[styles.options, wide && styles.optionsWide]}>
            <OptionCard
              wide={wide}
              title="Original"
              selected={choice === 'original'}
              onSelect={() => setChoice('original')}
              meta={`≈${t.original} tokens (estimate)`}
              privacy={result.privacy.original}>
              <Text style={styles.text} selectable>
                {original}
              </Text>
            </OptionCard>

            <OptionCard
              wide={wide}
              title="Optimized"
              selected={choice === 'optimized'}
              onSelect={() => setChoice('optimized')}
              meta={`≈${t.optimized} tokens (estimate)`}
              privacy={result.privacy.optimized}>
              <Text style={styles.text} selectable>
                {result.optimized_text}
              </Text>
            </OptionCard>
          </View>

          <OptionCard
            wide={false}
            title="Edit it yourself"
            selected={choice === 'custom'}
            onSelect={() => setChoice('custom')}
            meta="Starts from the optimized version. Re-checked for privacy before sending.">
            <TextInput
              style={styles.editor}
              value={custom}
              onChangeText={(v) => {
                setCustom(v);
                setChoice('custom');
              }}
              multiline
              maxLength={MAX_INPUT_CHARS}
              accessibilityLabel="Edit prompt"
            />
          </OptionCard>
        </ScrollView>

        <View style={styles.footer}>
          <Text style={[font.small, styles.footerNote]}>Nothing is sent until you tap the button below and the chosen text passes the privacy check.</Text>
          <View style={styles.row}>
            <View style={styles.flex}>
              <Button label="Cancel" variant="danger" onPress={onCancel} />
            </View>
            <View style={styles.flex}>
              <Button label="Check & send" onPress={() => onUse(selectedText.trim(), source)} disabled={selectedText.trim().length === 0} />
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
  body: { padding: spacing.lg, gap: spacing.md },
  summary: { backgroundColor: colors.primarySoft, borderRadius: radius.lg, padding: spacing.lg, gap: 4 },
  summaryTitle: { fontSize: 15, fontWeight: '600', color: colors.primaryDark },
  change: { fontSize: 13, color: colors.text, marginTop: 2 },
  options: { gap: spacing.md },
  optionsWide: { flexDirection: 'row' },
  card: { backgroundColor: colors.surface, borderWidth: 1.5, borderColor: colors.border, borderRadius: radius.lg, padding: spacing.lg },
  cardSelected: { borderColor: colors.primary },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.sm },
  cardTitle: { fontSize: 15, fontWeight: '600', color: colors.text },
  text: { fontSize: 15, lineHeight: 22, color: colors.text },
  meta: { fontSize: 12, color: colors.textMuted, marginTop: spacing.sm },
  editor: {
    minHeight: 90,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radius.md,
    padding: spacing.md,
    fontSize: 15,
    lineHeight: 22,
    color: colors.text,
    textAlignVertical: 'top',
  },
  footer: { padding: spacing.lg, gap: spacing.sm, backgroundColor: colors.surface, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  footerNote: { textAlign: 'center' },
  row: { flexDirection: 'row', gap: spacing.sm },
});
