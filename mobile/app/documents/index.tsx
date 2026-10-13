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
import { decideDocumentPrivacy, deleteDocument, listDocuments, reindexDocument, uploadDocument } from '@/services/api';
import type { DocumentInfo } from '@/types/documents';

const STATUS: Record<DocumentInfo['status'], { label: string; color: string; bg: string }> = {
  ready: { label: 'Ready', color: colors.success, bg: colors.successSoft },
  needs_review: { label: 'Needs privacy review', color: colors.warning, bg: colors.warningSoft },
  failed: { label: 'Failed', color: colors.danger, bg: colors.dangerSoft },
  processing: { label: 'Processing', color: colors.textMuted, bg: colors.surfaceMuted },
};

export default function DocumentsScreen() {
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setDocs(await listDocuments());
      setError(null);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  const upsert = (doc: DocumentInfo) => setDocs((prev) => [doc, ...prev.filter((d) => d.id !== doc.id)]);

  const pickAndUpload = async () => {
    const picked = await DocumentPicker.getDocumentAsync({ type: ['application/pdf', 'text/plain'], copyToCacheDirectory: true, multiple: false });
    if (picked.canceled || !picked.assets?.length) return;
    const asset = picked.assets[0];
    setUploading(true);
    setError(null);
    const controller = new AbortController();
    abort.current = controller;
    try {
      upsert(await uploadDocument({ uri: asset.uri, name: asset.name, mimeType: asset.mimeType }, controller.signal));
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setUploading(false);
      abort.current = null;
    }
  };

  const act = async (id: string, fn: () => Promise<DocumentInfo | void>) => {
    setBusyId(id);
    setError(null);
    try {
      const doc = await fn();
      if (doc) upsert(doc);
    } catch (e) {
      setError(describeError(e).message);
    } finally {
      setBusyId(null);
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
    Alert.alert('Delete document?', `“${doc.filename}”, its text and its search index will be deleted from the server.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () =>
          void act(doc.id, async () => {
            await deleteDocument(doc.id);
            setDocs((prev) => prev.filter((d) => d.id !== doc.id));
          }),
      },
    ]);

  const ready = docs.filter((d) => d.status === 'ready');

  const renderDoc = ({ item: d }: { item: DocumentInfo }) => {
    const st = STATUS[d.status];
    const busy = busyId === d.id;
    return (
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <Ionicons name={d.file_type === 'pdf' ? 'document-outline' : 'document-text-outline'} size={20} color={colors.primary} />
          <Text style={styles.name} numberOfLines={2}>{d.filename}</Text>
          <View style={[styles.badge, { backgroundColor: st.bg }]}>
            <Text style={[styles.badgeText, { color: st.color }]}>{st.label}</Text>
          </View>
        </View>
        <Text style={font.small}>
          {formatBytes(d.size_bytes)}
          {d.page_count ? ` · ${d.page_count} page${d.page_count === 1 ? '' : 's'}` : ''}
          {d.status === 'ready' ? ` · ${d.chunk_count} passages indexed` : ''}
          {d.privacy.decision === 'redacted' ? ' · redacted' : d.privacy.decision === 'confirmed' ? ' · indexed with your confirmation' : ''}
        </Text>

        {d.status === 'needs_review' ? (
          <Text style={[font.small, styles.review]}>
            Detected: {categoryList(d.privacy.categories)}. Nothing has been sent to the AI provider.{' '}
            {d.privacy.required_action === 'redact' ? 'Policy requires redaction before indexing.' : 'You can redact it, or confirm indexing as-is.'}
          </Text>
        ) : null}
        {d.status === 'failed' ? <Text style={[font.small, { color: colors.danger }]}>{documentErrorMessage(d.error_code)}</Text> : null}

        {busy ? <ActivityIndicator style={styles.spinner} color={colors.primary} /> : (
          <View style={styles.actions}>
            {d.status === 'ready' ? (
              <View style={styles.flex}><Button label="Ask" onPress={() => router.push({ pathname: '/documents/ask', params: { ids: d.id } })} /></View>
            ) : null}
            {d.status === 'needs_review' ? (
              <>
                <View style={styles.flex}><Button label="Redact & index" onPress={() => redactIndex(d)} /></View>
                {d.privacy.required_action === 'confirm' ? (
                  <View style={styles.flex}><Button label="Index as-is" variant="secondary" onPress={() => confirmIndex(d)} /></View>
                ) : null}
              </>
            ) : null}
            {d.status === 'failed' && d.privacy.decision !== 'none' ? (
              <View style={styles.flex}><Button label="Retry indexing" variant="secondary" onPress={() => void act(d.id, () => reindexDocument(d.id))} /></View>
            ) : null}
            <Pressable accessibilityRole="button" accessibilityLabel={`Delete ${d.filename}`} onPress={() => remove(d)} style={styles.delete} hitSlop={6}>
              <Ionicons name="trash-outline" size={20} color={colors.danger} />
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
              <View style={styles.uploading}>
                <ActivityIndicator color={colors.primary} />
                <Text style={[font.small, styles.flex]}>Uploading, scanning and indexing… this can take a minute.</Text>
                <Button label="Stop waiting" variant="danger" onPress={() => abort.current?.abort()} />
              </View>
            ) : (
              <Button label="Upload PDF or TXT" onPress={() => void pickAndUpload()} />
            )}
            {ready.length > 1 ? (
              <Button label={`Ask all ${ready.length} ready documents`} variant="secondary" onPress={() => router.push({ pathname: '/documents/ask', params: { ids: ready.map((d) => d.id).join(',') } })} />
            ) : null}
            {error ? <ErrorBanner message={error} onRetry={() => void refresh()} /> : null}
          </View>
        }
        ListEmptyComponent={
          loading ? <ActivityIndicator style={styles.spinner} color={colors.primary} /> : !error ? <Text style={[font.small, styles.empty]}>No documents yet.</Text> : null
        }
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  list: { padding: spacing.lg, gap: spacing.md },
  top: { gap: spacing.md, marginBottom: spacing.sm },
  uploading: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  empty: { textAlign: 'center', marginTop: spacing.xl },
  spinner: { marginVertical: spacing.md },
  card: { backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg, gap: spacing.sm },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  name: { flex: 1, fontSize: 15, fontWeight: '600', color: colors.text },
  badge: { borderRadius: radius.pill, paddingHorizontal: spacing.sm, paddingVertical: 2 },
  badgeText: { fontSize: 11, fontWeight: '700' },
  review: { color: colors.warning },
  actions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  delete: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
});
