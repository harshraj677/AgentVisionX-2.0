import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { AppHeader } from '@/components/AppHeader';
import { Screen } from '@/components/Screen';
import { colors, font, radius, spacing } from '@/constants/theme';
import { useConversations } from '@/hooks/ConversationsContext';

export default function HistoryScreen() {
  const router = useRouter();
  const { conversations, remove } = useConversations();
  const items = conversations.filter((c) => c.messages.length > 0);

  return (
    <Screen>
      <AppHeader title="Conversations" />
      <FlatList
        data={items}
        keyExtractor={(c) => c.id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={[font.small, styles.empty]}>No saved conversations yet.</Text>}
        renderItem={({ item }) => (
          <Pressable
            accessibilityRole="button"
            style={styles.item}
            onPress={() => router.push({ pathname: '/chat', params: { id: item.id } })}>
            <View style={styles.flex}>
              <Text style={styles.title} numberOfLines={1}>
                {item.title}
              </Text>
              <Text style={font.small}>
                {item.messages.length} messages · {new Date(item.updatedAt).toLocaleString()}
              </Text>
            </View>
            <Pressable accessibilityRole="button" accessibilityLabel="Delete conversation" hitSlop={10} onPress={() => remove(item.id)}>
              <Ionicons name="trash-outline" size={20} color={colors.danger} />
            </Pressable>
          </Pressable>
        )}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  list: { padding: spacing.lg },
  empty: { textAlign: 'center', marginTop: spacing.xxl },
  item: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.lg,
    marginBottom: spacing.sm,
  },
  title: { fontSize: 15, fontWeight: '600', color: colors.text, marginBottom: 2 },
});
