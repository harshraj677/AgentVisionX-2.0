import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { AppHeader } from '@/components/AppHeader';
import { Button } from '@/components/Button';
import { ErrorBanner } from '@/components/ErrorBanner';
import { ResponsePrivacyNotice } from '@/components/MessageBubble';
import { Screen } from '@/components/Screen';
import { UsageDetailsModal } from '@/components/UsageDetailsModal';
import { describeError } from '@/constants/documents';
import { categoryList } from '@/constants/privacy';
import { colors, font, radius, spacing } from '@/constants/theme';
import { usePromptReview } from '@/hooks/usePromptReview';
import { ApiError, askDocuments, listDocuments } from '@/services/api';
import { RETRYABLE_ASK_CODES, selectAskable } from '@/services/documentStatus';
import { getRoutingPreference } from '@/services/preferences';
import type { PromptSource, ResponseMetadata } from '@/types/chat';
import type { AskDocumentsResponse, DocumentInfo } from '@/types/documents';

interface Failure {
  message: string;
  retry?: () => void;
}

export default function AskDocumentsScreen() {
  const params = useLocalSearchParams<{ ids?: string }>();
  const selectedIds = useMemo(() => (params.ids ?? '').split(',').filter(Boolean), [params.ids]);
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null); // null = not loaded yet
  const [docsError, setDocsError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskDocumentsResponse | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<string | null>(null); // not an error: the documents simply do not answer this
  const [open, setOpen] = useState<string | null>(null); // passage ref whose excerpt is shown
  const [details, setDetails] = useState<ResponseMetadata | null>(null);
  const abort = useRef<AbortController | null>(null);
  const lastApproved = useRef<{ text: string; source: PromptSource } | null>(null);
  const running = useRef(false); // blocks a second request before React has re-rendered

  // Always re-read from the server, so a deleted or still-processing document is never sent as a source.
  const loadDocs = useCallback(() => {
    setDocsError(null);
    listDocuments()
      .then(setDocs)
      .catch((e) => setDocsError(describeError(e).message));
  }, []);
  useEffect(() => loadDocs(), [loadDocs, selectedIds]);
  useEffect(() => () => abort.current?.abort(), []); // leaving the screen stops waiting for an answer

  const askable = useMemo(() => (docs ? selectAskable(selectedIds, docs) : null), [docs, selectedIds]);
  const askIds = askable?.readyIds ?? [];
  const names = docs ? docs.filter((d) => askIds.includes(d.id)).map((d) => d.filename) : [];

  const run = async (text: string, source: PromptSource) => {
    if (running.current || askIds.length === 0) return;
    running.current = true;
    lastApproved.current = { text, source };
    setLoading(true);
    setFailure(null);
    setNotice(null);
    setResult(null);
    setOpen(null);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const routing = await getRoutingPreference();
      setResult(
        await askDocuments(
          {
            question: text, document_ids: askIds, privacy_ack: true, prompt_source: source,
            routing_policy: routing.policy, model: routing.policy === 'manual' ? routing.model : undefined,
          },
          controller.signal,
        ),
      );
    } catch (e) {
      if (e instanceof ApiError && e.code === 'documents_insufficient_evidence') {
        setNotice('The selected documents do not contain passages relevant enough to answer this question. Try rephrasing it, or ask about something the documents cover.');
      } else if (e instanceof ApiError && e.code === 'response_privacy_blocked') {
        const found = (e.details as { response_privacy?: { categories?: Record<string, number> } } | undefined)?.response_privacy?.categories;
        setFailure({ message: `The answer was withheld by the response privacy policy${found ? ` (detected: ${categoryList(found)})` : ''}.` });
      } else if (e instanceof ApiError && e.code === 'document_not_found') {
        setFailure({ message: 'One of the documents no longer exists on the server. The list was refreshed; ask again.' });
        loadDocs();
      } else {
        const err = describeError(e);
        // Asking again re-uses the text that already passed the privacy review, so a retry after a network failure is safe.
        setFailure({
          message: err.message,
          retry: RETRYABLE_ASK_CODES.includes(err.code) && lastApproved.current ? () => void run(lastApproved.current!.text, lastApproved.current!.source) : undefined,
        });
      }
    } finally {
      setLoading(false);
      running.current = false;
      abort.current = null;
    }
  };

  const review = usePromptReview((text, source) => void run(text, source));
  const parts = result ? result.answer.split(/(\[D\d+\])/g) : [];
  const refs = new Set(result?.passages.map((p) => p.ref));
  const selected = result?.passages.find((p) => p.ref === open) ?? null;
  const skipped = askable ? askable.missing + askable.notReady : 0;
  const canAsk = !!docs && askIds.length > 0 && !!question.trim();

  return (
    <Screen>
      <AppHeader title="Ask documents" subtitle={names.length ? names.join(', ') : `${selectedIds.length} document(s)`} />
      <KeyboardAvoidingView style={styles.flex} behavior="padding">
        <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
          {docsError ? <ErrorBanner message={docsError} onRetry={loadDocs} /> : null}
          {!docs && !docsError ? (
            <View style={styles.row} accessibilityLiveRegion="polite">
              <ActivityIndicator color={colors.primary} />
              <Text style={font.small}>Checking your documents…</Text>
            </View>
          ) : null}
          {askable && skipped > 0 ? (
            <Text style={[styles.status, styles.warn]} accessibilityRole="alert">
              {askIds.length === 0
                ? 'None of the selected documents can be asked right now (they were deleted or are not indexed yet). Go back and pick a ready document.'
                : `${skipped} selected document${skipped === 1 ? ' was' : 's were'} skipped because ${skipped === 1 ? 'it is' : 'they are'} no longer available or not indexed yet.`}
            </Text>
          ) : null}

          <TextInput
            style={styles.input}
            value={question}
            onChangeText={setQuestion}
            placeholder="Ask a question about the selected documents"
            placeholderTextColor={colors.textMuted}
            multiline
            maxLength={2000}
            editable={!loading && !review.busy}
            accessibilityLabel="Question"
          />
          {loading ? (
            <View style={styles.row} accessibilityLiveRegion="polite">
              <ActivityIndicator color={colors.primary} />
              <Text style={[font.small, styles.flex]}>Searching your documents and writing an answer…</Text>
              <Button label="Stop waiting" variant="danger" onPress={() => abort.current?.abort()} />
            </View>
          ) : (
            <Button label="Ask" onPress={() => review.start(question)} loading={review.busy} disabled={!canAsk} />
          )}
          {review.error ? <ErrorBanner message={review.error} /> : null}
          {failure ? <ErrorBanner message={failure.message} onRetry={failure.retry} /> : null}
          {notice ? <Text style={[styles.status, styles.warn]} accessibilityRole="alert">{notice}</Text> : null}

          {result ? (
            <View style={styles.result}>
              {result.evidence_status === 'insufficient_evidence' ? (
                <Text style={[styles.status, styles.warn]}>The documents do not contain enough information to fully answer this.</Text>
              ) : result.evidence_status === 'uncited' ? (
                <Text style={[styles.status, styles.warn]}>This answer cites no passage. Treat it as unverified.</Text>
              ) : (
                <Text style={[styles.status, styles.info]}>Tap a reference like [D1] to see the passage it came from. References are not a guarantee of correctness.</Text>
              )}
              <Text style={styles.answer} selectable>
                {parts.map((part, i) =>
                  refs.has(part.slice(1, -1)) ? (
                    <Text key={i} style={styles.cite} onPress={() => setOpen(part.slice(1, -1))} accessibilityRole="button">
                      {part}
                    </Text>
                  ) : (
                    <Text key={i}>{part}</Text>
                  ),
                )}
              </Text>

              {selected ? (
                <View style={styles.source} accessibilityLiveRegion="polite">
                  <View style={styles.row}>
                    <Text style={styles.ref}>[{selected.ref}]</Text>
                    <Text style={[styles.flex, styles.pname]} numberOfLines={1}>{selected.filename}</Text>
                    <Pressable accessibilityRole="button" accessibilityLabel="Close source" hitSlop={10} onPress={() => setOpen(null)}>
                      <Ionicons name="close" size={20} color={colors.textMuted} />
                    </Pressable>
                  </View>
                  <Text style={font.small}>
                    {selected.page_number ? `Page ${selected.page_number}` : `Part ${selected.chunk_index + 1}`} · similarity {selected.similarity.toFixed(2)} · {selected.cited ? 'cited in the answer' : 'retrieved, not cited'}
                  </Text>
                  <Text style={styles.excerpt}>{selected.excerpt ?? 'Excerpt withheld by the response privacy policy.'}</Text>
                </View>
              ) : null}

              {result.metadata.response_privacy ? <ResponsePrivacyNotice rp={result.metadata.response_privacy} /> : null}
              {result.invalid_citations_removed > 0 || result.unverified_links_removed > 0 ? (
                <Text style={font.small}>
                  Removed: {result.invalid_citations_removed} reference(s) to passages that were not retrieved, {result.unverified_links_removed} link(s) not found in the passages.
                </Text>
              ) : null}
              <Pressable accessibilityRole="button" onPress={() => setDetails(result.metadata)} style={styles.detailsLink}>
                <Ionicons name="stats-chart-outline" size={14} color={colors.primary} />
                <Text style={styles.detailsText}>{result.metadata.model} · Request details</Text>
              </Pressable>

              <Text style={[font.label, styles.label]}>PASSAGES ({result.passages.length})</Text>
              {result.passages.map((p) => (
                <Pressable
                  key={p.ref}
                  accessibilityRole="button"
                  accessibilityLabel={`Source ${p.ref}, ${p.filename}, ${p.page_number ? `page ${p.page_number}` : `part ${p.chunk_index + 1}`}, ${p.cited ? 'cited' : 'not cited'}`}
                  onPress={() => setOpen(open === p.ref ? null : p.ref)}
                  style={[styles.passage, open === p.ref && styles.passageOpen]}>
                  <View style={styles.row}>
                    <Text style={styles.ref}>[{p.ref}]</Text>
                    <Text style={[styles.flex, styles.pname]} numberOfLines={1}>{p.filename}</Text>
                    <Text style={font.small}>{p.page_number ? `p. ${p.page_number}` : `part ${p.chunk_index + 1}`}</Text>
                  </View>
                  <Text style={font.small}>
                    {p.cited ? 'Cited' : 'Not cited'} · similarity {p.similarity.toFixed(2)}
                    {p.flags.includes('possible_prompt_injection') ? ' · instruction-like text removed' : ''}
                  </Text>
                  {open === p.ref ? <Text style={styles.excerpt}>{p.excerpt ?? 'Excerpt withheld by the response privacy policy.'}</Text> : null}
                </Pressable>
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
    minHeight: 90, maxHeight: 180, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.md, padding: spacing.md, fontSize: 16, color: colors.text, textAlignVertical: 'top',
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  result: { gap: spacing.sm, marginTop: spacing.sm },
  status: { fontSize: 13, lineHeight: 18, padding: spacing.sm, borderRadius: radius.sm },
  warn: { backgroundColor: colors.warningSoft, color: colors.warning },
  info: { backgroundColor: colors.primarySoft, color: colors.primaryDark },
  answer: { fontSize: 16, lineHeight: 24, color: colors.text, backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  cite: { color: colors.primary, fontWeight: '700' },
  source: { backgroundColor: colors.primarySoft, borderRadius: radius.md, borderWidth: 1, borderColor: colors.primary, padding: spacing.md, gap: 4 },
  detailsLink: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: spacing.sm, minHeight: 44 },
  detailsText: { fontSize: 13, color: colors.primary },
  label: { marginTop: spacing.md },
  passage: { backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.md, gap: 4 },
  passageOpen: { borderColor: colors.primary },
  ref: { fontWeight: '700', color: colors.primary },
  pname: { fontSize: 14, fontWeight: '600', color: colors.text },
  excerpt: { fontSize: 14, lineHeight: 20, color: colors.text, marginTop: spacing.xs },
});
