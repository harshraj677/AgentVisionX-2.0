import { Ionicons } from '@expo/vector-icons';
import { useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { AppHeader } from '@/components/AppHeader';
import { Button } from '@/components/Button';
import { ErrorBanner } from '@/components/ErrorBanner';
import { ResponsePrivacyNotice } from '@/components/MessageBubble';
import { Screen } from '@/components/Screen';
import { UsageDetailsModal } from '@/components/UsageDetailsModal';
import { categoryList } from '@/constants/privacy';
import { colors, font, radius, spacing } from '@/constants/theme';
import { usePromptReview } from '@/hooks/usePromptReview';
import { ApiError, runResearch } from '@/services/api';
import { getRoutingPreference } from '@/services/preferences';
import type { PromptSource, ResponseMetadata } from '@/types/chat';
import type { ResearchResponse, ResearchSource } from '@/types/research';

const MAX_QUESTION_CHARS = 400; // matches RESEARCH_MAX_QUERY_CHARS default on the server
const SOURCE_COUNTS = [3, 5, 8];

function friendlyError(e: unknown): { title: string; message: string } {
  if (!(e instanceof ApiError)) return { title: 'Something went wrong', message: e instanceof Error ? e.message : 'Unknown error.' };
  switch (e.code) {
    case 'research_provider_not_configured':
      return { title: 'Research is not set up', message: 'The server has no Tavily key. Add TAVILY_API_KEY to backend/.env and restart the backend.' };
    case 'research_timeout':
      return { title: 'Search timed out', message: 'The web search took too long. Please try again.' };
    case 'research_no_sources':
      return { title: 'Not enough evidence', message: 'The search found no usable sources, so no answer was generated. Try different wording.' };
    case 'response_privacy_blocked': {
      const found = (e.details as { response_privacy?: { categories?: Record<string, number> } } | undefined)?.response_privacy?.categories;
      return { title: 'Answer withheld', message: `The answer was withheld by the response privacy policy${found ? ` (detected: ${categoryList(found)})` : ''}.` };
    }
    case 'cancelled':
      return { title: 'Stopped', message: 'The app stopped waiting. The server may still finish the search and answer it already started (and may still be billed).' };
    default:
      return { title: 'Research failed', message: e.message };
  }
}

/** Opens only http(s) links that came back from the server's validated source list, after the user confirms. */
function openSource(source: ResearchSource) {
  if (!/^https?:\/\//i.test(source.url)) return;
  Alert.alert('Open external link?', `${source.domain}\n\nThis opens your browser. The page has not been checked by AgentVision X.`, [
    { text: 'Cancel', style: 'cancel' },
    { text: 'Open', onPress: () => void Linking.openURL(source.url).catch(() => Alert.alert('Could not open the link.')) },
  ]);
}

export default function ResearchScreen() {
  const [question, setQuestion] = useState('');
  const [depth, setDepth] = useState<'basic' | 'advanced'>('basic');
  const [count, setCount] = useState(5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [failure, setFailure] = useState<{ title: string; message: string } | null>(null);
  const [details, setDetails] = useState<ResponseMetadata | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scroll = useRef<ScrollView>(null);

  const run = async (text: string, source: PromptSource) => {
    setLoading(true);
    setFailure(null);
    setResult(null);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const routing = await getRoutingPreference();
      const res = await runResearch(
        {
          question: text,
          search_depth: depth,
          max_sources: count,
          privacy_ack: true, // the user has been through the privacy review for this exact text
          prompt_source: source,
          routing_policy: routing.policy,
          model: routing.policy === 'manual' ? routing.model : undefined,
        },
        controller.signal,
      );
      setResult(res);
    } catch (e) {
      setFailure(friendlyError(e));
    } finally {
      setLoading(false);
      abort.current = null;
    }
  };

  const review = usePromptReview((text, source) => void run(text, source));
  const busy = review.busy || loading;

  const sourcesById = new Map(result?.sources.map((s) => [s.id, s]) ?? []);
  const answerParts = result ? result.answer.split(/(\[\d+\])/g) : [];

  return (
    <Screen>
      <AppHeader title="Research" subtitle="Web search via Tavily, answered with citations" />
      <KeyboardAvoidingView style={styles.flex} behavior="padding">
        <ScrollView ref={scroll} contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
          <TextInput
            style={styles.input}
            value={question}
            onChangeText={setQuestion}
            placeholder="Ask a question to research on the web"
            placeholderTextColor={colors.textMuted}
            multiline
            maxLength={MAX_QUESTION_CHARS}
            editable={!busy}
            accessibilityLabel="Research question"
          />
          <Text style={[font.small, styles.counter]}>
            {question.length}/{MAX_QUESTION_CHARS} · Only this question is sent to the search service, after the privacy check.
          </Text>

          <View style={styles.optionRow}>
            <Text style={styles.optionLabel}>Depth</Text>
            {(['basic', 'advanced'] as const).map((d) => (
              <Pressable key={d} accessibilityRole="radio" accessibilityState={{ selected: depth === d }} disabled={busy} onPress={() => setDepth(d)} style={[styles.chip, depth === d && styles.chipOn]}>
                <Text style={[styles.chipText, depth === d && styles.chipTextOn]}>{d === 'basic' ? 'Basic' : 'Advanced'}</Text>
              </Pressable>
            ))}
          </View>
          <View style={styles.optionRow}>
            <Text style={styles.optionLabel}>Sources</Text>
            {SOURCE_COUNTS.map((n) => (
              <Pressable key={n} accessibilityRole="radio" accessibilityState={{ selected: count === n }} disabled={busy} onPress={() => setCount(n)} style={[styles.chip, count === n && styles.chipOn]}>
                <Text style={[styles.chipText, count === n && styles.chipTextOn]}>{n}</Text>
              </Pressable>
            ))}
          </View>
          <Text style={font.small}>Advanced search costs more Tavily credits. The server may cap the number of sources.</Text>

          <View style={styles.actions}>
            {loading ? (
              <Button label="Stop waiting" variant="danger" onPress={() => abort.current?.abort()} />
            ) : (
              <Button label="Research" onPress={() => review.start(question)} loading={review.busy} disabled={!question.trim()} />
            )}
          </View>

          {review.error ? <ErrorBanner message={review.error} /> : null}

          {loading ? (
            <View style={styles.loading}>
              <ActivityIndicator color={colors.primary} />
              <Text style={font.small}>Searching the web and writing a cited answer… This can take up to a minute.</Text>
            </View>
          ) : null}

          {failure ? (
            <View style={styles.failure} accessibilityRole="alert">
              <Text style={styles.failureTitle}>{failure.title}</Text>
              <Text style={styles.failureText}>{failure.message}</Text>
            </View>
          ) : null}

          {result ? (
            <View style={styles.result}>
              {result.evidence_status === 'insufficient_evidence' ? (
                <Text style={[styles.status, styles.statusWarn]}>The sources did not contain enough information to fully answer this.</Text>
              ) : result.evidence_status === 'uncited' ? (
                <Text style={[styles.status, styles.statusWarn]}>This answer cites none of the sources. Treat it as unverified.</Text>
              ) : (
                <Text style={[styles.status, styles.statusInfo]}>
                  Numbers like [1] point to the sources below. Citations show where a claim came from; they are not a guarantee it is correct.
                </Text>
              )}

              <Text style={styles.answer} selectable>
                {answerParts.map((part, i) => {
                  const m = /^\[(\d+)\]$/.exec(part);
                  const src = m ? sourcesById.get(Number(m[1])) : undefined;
                  return src ? (
                    <Text key={i} style={styles.cite} onPress={() => openSource(src)} accessibilityRole="link" accessibilityLabel={`Source ${src.id}: ${src.title}`}>
                      {part}
                    </Text>
                  ) : (
                    <Text key={i}>{part}</Text>
                  );
                })}
              </Text>

              {result.metadata.response_privacy ? <ResponsePrivacyNotice rp={result.metadata.response_privacy} /> : null}
              {result.citations.invalid_citations_removed > 0 || result.citations.unverified_links_removed > 0 ? (
                <Text style={font.small}>
                  Removed from the answer: {result.citations.invalid_citations_removed} citation(s) to sources that don't exist,{' '}
                  {result.citations.unverified_links_removed} link(s) that were not among the sources.
                </Text>
              ) : null}

              <Pressable accessibilityRole="button" onPress={() => setDetails(result.metadata)} style={styles.detailsLink}>
                <Ionicons name="stats-chart-outline" size={14} color={colors.primary} />
                <Text style={styles.detailsText}>
                  {result.metadata.model} · {result.metadata.usage.total_tokens != null ? `${result.metadata.usage.total_tokens} tokens · ` : ''}Request details
                </Text>
              </Pressable>

              <Text style={[font.label, styles.sourcesLabel]}>SOURCES ({result.sources.length})</Text>
              {result.sources.map((s) => (
                <View key={s.id} style={styles.source}>
                  <View style={styles.sourceHead}>
                    <Text style={styles.sourceId}>[{s.id}]</Text>
                    <Text style={styles.sourceTitle} numberOfLines={2}>{s.title}</Text>
                  </View>
                  <Text style={styles.domain}>{s.domain}{s.cited ? '' : ' · not cited'}</Text>
                  {s.snippet ? <Text style={styles.snippet}>{s.snippet}</Text> : <Text style={font.small}>Snippet withheld by the privacy policy.</Text>}
                  {s.flags.includes('possible_prompt_injection') ? (
                    <Text style={[font.small, { color: colors.warning }]}>Instruction-like text was removed from this source before it was used.</Text>
                  ) : null}
                  <Text style={font.small}>
                    {s.published_date ? `Published ${s.published_date.slice(0, 10)} · ` : ''}Retrieved {new Date(s.retrieved_at).toLocaleString()}
                  </Text>
                  <Pressable accessibilityRole="link" onPress={() => openSource(s)} style={styles.open}>
                    <Ionicons name="open-outline" size={15} color={colors.primary} />
                    <Text style={styles.openText}>Open source</Text>
                  </Pressable>
                </View>
              ))}
            </View>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
      {review.modal}
      <UsageDetailsModal metadata={details} onClose={() => setDetails(null)} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  body: { padding: spacing.lg, paddingBottom: spacing.xxl, gap: spacing.sm },
  input: {
    minHeight: 96,
    maxHeight: 180,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    fontSize: 16,
    color: colors.text,
    textAlignVertical: 'top',
  },
  counter: { marginBottom: spacing.xs },
  optionRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: spacing.sm },
  optionLabel: { ...font.label, width: 64 },
  chip: { paddingHorizontal: spacing.md, paddingVertical: 6, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: 14, fontWeight: '600', color: colors.text },
  chipTextOn: { color: colors.textOnPrimary },
  actions: { marginTop: spacing.sm },
  loading: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, padding: spacing.md },
  failure: { backgroundColor: colors.dangerSoft, borderRadius: radius.md, padding: spacing.lg, borderWidth: 1, borderColor: '#FECACA' },
  failureTitle: { fontSize: 15, fontWeight: '600', color: colors.danger, marginBottom: 4 },
  failureText: { fontSize: 14, lineHeight: 20, color: colors.danger },
  result: { gap: spacing.sm, marginTop: spacing.sm },
  status: { fontSize: 13, lineHeight: 18, padding: spacing.sm, borderRadius: radius.sm },
  statusWarn: { backgroundColor: colors.warningSoft, color: colors.warning },
  statusInfo: { backgroundColor: colors.primarySoft, color: colors.primaryDark },
  answer: { fontSize: 16, lineHeight: 24, color: colors.text, backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  cite: { color: colors.primary, fontWeight: '700' },
  detailsLink: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: spacing.xs },
  detailsText: { fontSize: 13, color: colors.primary },
  sourcesLabel: { marginTop: spacing.md },
  source: { backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.md, gap: 4 },
  sourceHead: { flexDirection: 'row', gap: spacing.sm, alignItems: 'flex-start' },
  sourceId: { fontWeight: '700', color: colors.primary, fontSize: 14 },
  sourceTitle: { flex: 1, fontSize: 15, fontWeight: '600', color: colors.text },
  domain: { fontSize: 12, color: colors.textMuted },
  snippet: { fontSize: 14, lineHeight: 20, color: colors.text },
  open: { flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start', paddingVertical: 4, minHeight: 32 },
  openText: { fontSize: 13, fontWeight: '600', color: colors.primary },
});
