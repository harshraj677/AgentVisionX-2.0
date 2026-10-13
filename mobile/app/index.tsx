import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Card } from '@/components/Card';
import { FeatureCard } from '@/components/FeatureCard';
import { Logo } from '@/components/Logo';
import { Screen } from '@/components/Screen';
import { StatusPill } from '@/components/StatusPill';
import { colors, font, spacing } from '@/constants/theme';
import { useConversations } from '@/hooks/ConversationsContext';
import { useHealth } from '@/hooks/useHealth';

export default function HomeScreen() {
  const router = useRouter();
  const { conversations } = useConversations();
  const { health } = useHealth();
  const recent = conversations.filter((c) => c.messages.length > 0).slice(0, 3);

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.topRow}>
          <View style={styles.brand}>
            <Logo />
            <View>
              <Text style={styles.brandName}>AgentVision X</Text>
              <Text style={font.small}>Privacy-aware AI gateway</Text>
            </View>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Settings"
            hitSlop={8}
            onPress={() => router.push('/settings')}
            style={styles.settingsBtn}>
            <Ionicons name="settings-outline" size={22} color={colors.text} />
          </Pressable>
        </View>

        <Text style={styles.greeting}>What would you like to do?</Text>
        <StatusPill status={health.status} onPress={() => router.push('/settings')} />

        <View style={styles.grid}>
          <View style={styles.gridRow}>
            <FeatureCard
              title="Chat"
              description="Ask anything, powered by Gemini."
              icon="chatbubble-ellipses-outline"
              onPress={() => router.push('/chat')}
            />
            <FeatureCard
              title="Research"
              description="Web search with cited sources."
              icon="search-outline"
              onPress={() => router.push('/research')}
            />
          </View>
          <View style={styles.gridRow}>
            <FeatureCard
              title="Voice"
              description="Speak your questions."
              icon="mic-outline"
              comingSoon
              onPress={() => router.push({ pathname: '/coming-soon', params: { feature: 'voice' } })}
            />
            <FeatureCard
              title="Documents"
              description="Ask questions about your PDFs and text files."
              icon="document-text-outline"
              onPress={() => router.push('/documents')}
            />
          </View>
        </View>

        <View style={styles.sectionHeader}>
          <Text style={font.heading}>Recent conversations</Text>
          {conversations.length > 3 ? (
            <Pressable onPress={() => router.push('/history')} hitSlop={8}>
              <Text style={styles.link}>See all</Text>
            </Pressable>
          ) : null}
        </View>

        {recent.length === 0 ? (
          <Card>
            <Text style={font.small}>No conversations yet. Start a chat and it will appear here.</Text>
          </Card>
        ) : (
          recent.map((c) => (
            <Pressable
              key={c.id}
              accessibilityRole="button"
              onPress={() => router.push({ pathname: '/chat', params: { id: c.id } })}
              style={styles.recentItem}>
              <Ionicons name="chatbubble-outline" size={18} color={colors.primary} />
              <Text style={styles.recentTitle} numberOfLines={1}>
                {c.title}
              </Text>
              <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
            </Pressable>
          ))
        )}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.lg, paddingBottom: spacing.xxl },
  topRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  brand: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  brandName: { fontSize: 20, fontWeight: '700', color: colors.text },
  settingsBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  greeting: { ...font.title, marginTop: spacing.xl, marginBottom: spacing.md },
  grid: { marginTop: spacing.xl, gap: spacing.md },
  gridRow: { flexDirection: 'row', gap: spacing.md },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: spacing.xl,
    marginBottom: spacing.md,
  },
  link: { color: colors.primary, fontWeight: '600', fontSize: 14 },
  recentItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    padding: spacing.lg,
    marginBottom: spacing.sm,
  },
  recentTitle: { flex: 1, fontSize: 15, color: colors.text },
});
