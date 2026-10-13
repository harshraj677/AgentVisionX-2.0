import { useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { FlatList, Keyboard, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppHeader, HeaderIconButton } from '@/components/AppHeader';
import { ChatInput } from '@/components/ChatInput';
import { ErrorBanner } from '@/components/ErrorBanner';
import { Logo } from '@/components/Logo';
import { MessageBubble } from '@/components/MessageBubble';
import { Screen } from '@/components/Screen';
import { TypingIndicator } from '@/components/TypingIndicator';
import { UsageDetailsModal } from '@/components/UsageDetailsModal';
import { colors, font, radius, spacing } from '@/constants/theme';
import { useConversations } from '@/hooks/ConversationsContext';
import { usePromptReview } from '@/hooks/usePromptReview';
import type { Message, ResponseMetadata } from '@/types/chat';

const SUGGESTIONS = [
  'Explain how HTTPS works in simple terms',
  'Give me a 3-day study plan for databases',
  'Help me write a polite email to my professor',
];


export default function ChatScreen() {
  const params = useLocalSearchParams<{ id?: string }>();
  const [convId, setConvId] = useState<string | undefined>(params.id);
  const { getConversation, statusOf, send, retry } = useConversations();
  const [details, setDetails] = useState<ResponseMetadata | null>(null);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const insets = useSafeAreaInsets();
  const listRef = useRef<FlatList<Message>>(null);

  const conversation = getConversation(convId);
  const messages = conversation?.messages ?? [];
  const status = statusOf(convId);
  const loading = status.state === 'loading';
  const lastIsUser = messages.length > 0 && messages[messages.length - 1].role === 'user';
  const privacyRefusal = status.state === 'error' && status.code?.startsWith('privacy_') ? status.error : undefined;

  useEffect(() => {
    const show = Keyboard.addListener(Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow', () => setKeyboardOpen(true));
    const hide = Keyboard.addListener(Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide', () => setKeyboardOpen(false));
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);

  const review = usePromptReview((text, source) => {
    setDraft('');
    setConvId(send(convId, text, source));
  });
  const busy = review.busy;
  const scanError = review.error;

  const handleSend = (override?: string) => review.start(override ?? draft);

  const scrollToEnd = () => listRef.current?.scrollToEnd({ animated: true });

  return (
    <Screen edges={['top', 'left', 'right']}>
      <AppHeader
        title={conversation?.title ?? 'New chat'}
        subtitle="Gemini via AgentVision backend"
        right={<HeaderIconButton icon="create-outline" label="New conversation" onPress={() => setConvId(undefined)} />}
      />
      <KeyboardAvoidingView style={styles.flex} behavior="padding">
        {messages.length === 0 ? (
          <View style={styles.empty}>
            <Logo size={56} />
            <Text style={styles.emptyTitle}>How can I help?</Text>
            <Text style={[font.small, styles.emptyNote]}>
              Each message is checked locally on your backend for sensitive data before anything is sent to Gemini, and
              you can review a shorter version of your prompt first. Detection is not perfect, so still avoid sharing
              secrets.
            </Text>
            {SUGGESTIONS.map((s) => (
              <Pressable key={s} accessibilityRole="button" style={styles.chip} onPress={() => handleSend(s)}>
                <Text style={styles.chipText}>{s}</Text>
              </Pressable>
            ))}
          </View>
        ) : (
          <FlatList
            ref={listRef}
            style={styles.flex}
            data={messages}
            keyExtractor={(m) => m.id}
            contentContainerStyle={styles.list}
            keyboardShouldPersistTaps="handled"
            onContentSizeChange={scrollToEnd}
            renderItem={({ item }) => (
              <MessageBubble message={item} onShowDetails={(m) => setDetails(m.metadata ?? null)} />
            )}
            ListFooterComponent={
              <View>
                {loading ? <TypingIndicator /> : null}
                {!loading && lastIsUser && !privacyRefusal ? (
                  <ErrorBanner
                    message={status.error ?? 'No reply was received for your last message.'}
                    onRetry={() => convId && retry(convId)}
                  />
                ) : null}
              </View>
            }
          />
        )}
        {scanError || privacyRefusal ? (
          <View style={styles.bannerWrap}>
            <ErrorBanner message={(scanError ?? privacyRefusal) as string} />
          </View>
        ) : null}
        <View style={{ paddingBottom: keyboardOpen ? 0 : insets.bottom, backgroundColor: colors.surface }}>
          <ChatInput
            value={draft}
            onChangeText={setDraft}
            onSend={() => handleSend()}
            disabled={loading || busy}
            busy={busy}
          />
        </View>
      </KeyboardAvoidingView>

      {review.modal}
      <UsageDetailsModal metadata={details} onClose={() => setDetails(null)} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  bannerWrap: { paddingHorizontal: spacing.lg },
  list: { padding: spacing.lg, flexGrow: 1 },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl },
  emptyTitle: { ...font.title, fontSize: 24, marginTop: spacing.lg },
  emptyNote: { textAlign: 'center', marginTop: spacing.sm, marginBottom: spacing.xl, maxWidth: 320 },
  chip: {
    alignSelf: 'stretch',
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  chipText: { fontSize: 14, color: colors.primary, textAlign: 'center' },
});
