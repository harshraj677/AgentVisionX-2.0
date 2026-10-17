import { Ionicons } from '@expo/vector-icons';
import * as DocumentPicker from 'expo-document-picker';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';

import { AppHeader } from '@/components/AppHeader';
import { Button } from '@/components/Button';
import { ErrorBanner } from '@/components/ErrorBanner';
import { Screen } from '@/components/Screen';
import { describeError, documentErrorMessage, formatBytes } from '@/constants/documents';
import { categoryList } from '@/constants/privacy';
import { colors, font, radius, spacing } from '@/constants/theme';
import { ApiError, decideDocumentPrivacy, deleteDocument, listDocuments, reindexDocument, uploadDocument } from '@/services/api';
import { canRetryIndexing, describeDocument, type DocTone } from '@/services/documentStatus';
import { latestOnly } from '@/services/latest';
import type { DocumentInfo } from '@/types/documents';

const TONE: Record<DocTone, { color: string; bg: string }> = {
  success: { color: colors.success, bg: colors.successSoft },
  warning: { color: colors.warning, bg: colors.warningSoft },
  danger: { color: colors.danger, bg: colors.dangerSoft },
  muted: { color: colors.textMuted, bg: colors.surfaceMuted },
};

/** A screen-level error. `retry` is present only when repeating the action is safe and means something. */
interface Problem {
  message: string;
  retry?: () => void;
}

export default function DocumentsScreen() {
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [busyIds, setBusyIds] = useState<string[]>([]);
  const [problem, setProblem] = useState<Problem | null>(null);
  const abort = useRef<AbortController | null>(null);
  const pickerGuard = useRef(false); // blocks a second picker/upload before React has re-rendered
  const inFlight = useRef(new Set<string>()); // documents with an action running (state lags a render behind taps)
  const reloads = useRef(latestOnly());

  /** Ignore list responses that started before a newer reload or a local change (a deleted item must not reappear). */
  const invalidateReloads = () => {
    reloads.current.next();
    setLoading(false);
  };

  const refresh = useCallback(async (keepProblem = false) => {
    const token = reloads.current.next();
    setLoading(true);
    try {
      const list = await listDocuments();
      if (!reloads.current.isCurrent(token)) return;
      setDocs(list);
      if (!keepProblem) setProblem(null);
    } catch (e) {
      if (!reloads.current.isCurrent(token)) return;
      // The list could not be loaded: keep what is on screen and offer a retry, which is always safe here.
      setProblem({ message: describeError(e).message, retry: () => void refresh() });
    } finally {
      if (reloads.current.isCurrent(token)) setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  const upsert = (doc: DocumentInfo) => {
    invalidateReloads();
    setDocs((prev) => [doc, ...prev.filter((d) => d.id !== doc.id)]);
  };

  const pickAndUpload = async () => {
    if (pickerGuard.current) return;
    pickerGuard.current = true;
    setPickerOpen(true);
    setProblem(null);
    const controller = new AbortController();
    try {
      let picked: DocumentPicker.DocumentPickerResult;
      try {
        picked = await DocumentPicker.getDocumentAsync({ type: ['application/pdf', 'text/plain'], copyToCacheDirectory: true, multiple: false });
      } catch {
        setProblem({ message: 'The file picker could not be opened. Please try again.' });
        return;
      }
      if (picked.canceled || !picked.assets?.length) return;
      const asset = picked.assets[0];
      setUploading(true);
      abort.current = controller;
      try {
        upsert(await uploadDocument({ uri: asset.uri, name: asset.name, mimeType: asset.mimeType }, controller.signal));
      } catch (e) {
        const err = describeError(e);
        const unsure = ['timeout', 'network_error', 'cancelled'].includes(err.code);
        // The server may have received (and even indexed) the file before the connection failed: say so, and look.
        setProblem({
          message: unsure ? `${err.message} The file may still have been received, so the list below was reloaded. Check it before uploading again.` : err.message,
        });
        if (unsure) void refresh(true);
      }
    } finally {
      setUploading(false);
      setPickerOpen(false);
      abort.current = null;
      pickerGuard.current = false;
    }
  };

  /** Runs one action for one document. A second tap while it runs is ignored. */
  const act = async (id: string, fn: () => Promise<DocumentInfo | void>) => {
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    setBusyIds((prev) => [...prev, id]);
    setProblem(null);
    try {
      const doc = await fn();
      if (doc) upsert(doc);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'document_not_found') {
        invalidateReloads();
        setDocs((prev) => prev.filter((d) => d.id !== id));
        setProblem({ message: 'That document no longer exists on the server, so it was removed from the list.' });
      } else {
        setProblem({ message: describeError(e).message });
      }
    } finally {
      inFlight.current.delete(id);
      setBusyIds((prev) => prev.filter((x) => x !== id));
    }
  };

  const confirmIndex = (doc: DocumentInfo) =>
    Alert.alert(
      'Index without redaction?',
      `The text, including the detected ${categoryList(doc.privacy.categories)}, will be sent to the AI provider to create embeddings and later as answer context. This decision is recorded.`,
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Index as-is', onPress: () => void act(doc.id, () => decideDocumentPrivacy(doc.id, 'confirm')) },
      ],
    );

  const redactIndex = (doc: DocumentInfo) =>
    Alert.alert(
      'Redact and index?',
      `Every detected item (${categoryList(doc.privacy.categories)}) is replaced by a label like [EMAIL_ADDRESS] before indexing. The original text is then discarded from the server.`,
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Redact & index', onPress: () => void act(doc.id, () => decideDocumentPrivacy(doc.id, 'redact')) },
      ],
    );

  const remove = (doc: DocumentInfo) =>
    Alert.alert('Delete document?', `“${doc.filename}”, its text and its search index will be deleted from the server. This cannot be undone.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () =>
          void act(doc.id, async () => {
            try {
              await deleteDocument(doc.id);
            } catch (e) {
              // Already gone (deleted from another screen or after a lost response): the goal is reached.
              if (!(e instanceof ApiError && e.code === 'document_not_found')) throw e;
            }
            invalidateReloads();
            setDocs((prev) => prev.filter((d) => d.id !== doc.id));
          }),
      },
    ]);

  const ready = docs.filter((d) => d.status === 'ready');

  const renderDoc = ({ item: d }: { item: DocumentInfo }) => {
    const view = describeDocument(d, documentErrorMessage(d.error_code), categoryList(d.privacy.categories));
    const tone = TONE[view.tone];
    const busy = busyIds.includes(d.id);
    return (
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <Ionicons name={d.file_type === 'pdf' ? 'document-outline' : 'document-text-outline'} size={20} color={colors.primary} />
          <Text style={styles.name} numberOfLines={2}>{d.filename}</Text>
          <View style={[styles.badge, { backgroundColor: tone.bg }]}>
            <Text style={[styles.badgeText, { color: tone.color }]} accessibilityLabel={`Status: ${view.label}`}>{view.label}</Text>
          </View>
        </View>
        <Text style={font.small}>
          {formatBytes(d.size_bytes)}
          {d.page_count ? ` · ${d.page_count} page${d.page_count === 1 ? '' : 's'}` : ''}
          {d.privacy.decision === 'redacted' ? ' · redacted' : d.privacy.decision === 'confirmed' ? ' · indexed with your confirmation' : ''}
        </Text>
        <Text style={[font.small, view.tone === 'danger' && { color: colors.danger }, view.tone === 'warning' && { color: colors.warning }]}>
          {view.detail}
        </Text>

        {busy ? (
          <View style={styles.busy} accessibilityLiveRegion="polite">
            <ActivityIndicator color={colors.primary} />
            <Text style={font.small}>Working… this can take a minute.</Text>
          </View>
        ) : (
          <View style={styles.actions}>
            {view.action === 'ask' ? (
              <View style={styles.action}>
                <Button label="Ask" accessibilityLabel={`Ask questions about ${d.filename}`} onPress={() => router.push({ pathname: '/documents/ask', params: { ids: d.id } })} />
              </View>
            ) : null}
            {view.action === 'review' ? (
              <>
                <View style={styles.action}>
                  <Button label="Redact & index" accessibilityLabel={`Redact and index ${d.filename}`} onPress={() => redactIndex(d)} />
                </View>
                {d.privacy.required_action === 'confirm' ? (
                  <View style={styles.action}>
                    <Button label="Index as-is" variant="secondary" accessibilityLabel={`Index ${d.filename} without redaction`} onPress={() => confirmIndex(d)} />
                  </View>
                ) : null}
              </>
            ) : null}
            {canRetryIndexing(d) ? (
              <View style={styles.action}>
                <Button label="Retry indexing" variant="secondary" accessibilityLabel={`Retry indexing ${d.filename}`} onPress={() => void act(d.id, () => reindexDocument(d.id))} />
              </View>
            ) : null}
            <Pressable accessibilityRole="button" accessibilityLabel={`Delete ${d.filename}`} onPress={() => remove(d)} style={styles.delete} hitSlop={6}>
              <Ionicons name="trash-outline" size={22} color={colors.danger} />
            </Pressable>
          </View>
        )}
      </View>
    );
  };

  return (
    <Screen>
      <AppHeader title="Documents" subtitle="Ask questions about your own files" />
      <FlatList
        data={docs}
        keyExtractor={(d) => d.id}
        renderItem={renderDoc}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={loading && docs.length > 0} onRefresh={() => void refresh()} />}
        ListHeaderComponent={
          <View style={styles.top}>
            <Text style={font.small}>
              PDF (with selectable text) and UTF-8 TXT files. Text is privacy-scanned on your backend before anything is sent to the
              AI provider for indexing. Scanned PDFs are not supported (no OCR).
            </Text>
            {uploading ? (
              <View style={styles.uploading} accessibilityLiveRegion="polite">
                <ActivityIndicator color={colors.primary} />
                <Text style={[font.small, styles.flex]}>Uploading, scanning and indexing… this can take a minute.</Text>
                <Button label="Stop waiting" variant="danger" onPress={() => abort.current?.abort()} />
              </View>
            ) : (
              <Button label="Upload PDF or TXT" onPress={() => void pickAndUpload()} disabled={pickerOpen} />
            )}
            {ready.length > 1 ? (
              <Button label={`Ask all ${ready.length} ready documents`} variant="secondary" onPress={() => router.push({ pathname: '/documents/ask', params: { ids: ready.map((d) => d.id).join(',') } })} />
            ) : null}
            {problem ? <ErrorBanner message={problem.message} onRetry={problem.retry} /> : null}
          </View>
        }
        ListEmptyComponent={
          loading ? (
            <ActivityIndicator style={styles.spinner} color={colors.primary} />
          ) : !problem ? (
            <Text style={[font.small, styles.empty]}>No documents yet. Tap “Upload PDF or TXT” to add one.</Text>
          ) : null
        }
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  list: { padding: spacing.lg, gap: spacing.md },
  top: { gap: spacing.md, marginBottom: spacing.sm },
  uploading: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  empty: { textAlign: 'center', marginTop: spacing.xl },
  spinner: { marginVertical: spacing.md },
  card: { backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg, gap: spacing.sm },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  name: { flex: 1, fontSize: 15, fontWeight: '600', color: colors.text },
  badge: { borderRadius: radius.pill, paddingHorizontal: spacing.sm, paddingVertical: 3, maxWidth: 150 },
  badgeText: { fontSize: 12, fontWeight: '700' },
  busy: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 46 },
  // Wraps on narrow screens instead of squeezing two buttons and the delete icon into one row.
  actions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  action: { flexGrow: 1, flexBasis: 130 },
  delete: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center', marginLeft: 'auto' },
});
