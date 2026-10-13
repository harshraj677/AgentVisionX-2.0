import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radius, shadow, spacing } from '@/constants/theme';

interface Props {
  title: string;
  description: string;
  icon: keyof typeof Ionicons.glyphMap;
  comingSoon?: boolean;
  onPress: () => void;
}

export function FeatureCard({ title, description, icon, comingSoon, onPress }: Props) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={comingSoon ? `${title}, coming soon` : title}
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}>
      <View style={[styles.iconWrap, comingSoon && styles.iconWrapMuted]}>
        <Ionicons name={icon} size={22} color={comingSoon ? colors.textMuted : colors.primary} />
      </View>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.desc} numberOfLines={2}>
        {description}
      </Text>
      {comingSoon ? (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>Coming soon</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flex: 1,
    minHeight: 150,
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    ...shadow,
  },
  iconWrap: {
    width: 40,
    height: 40,
    borderRadius: radius.md,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.md,
  },
  iconWrapMuted: { backgroundColor: colors.surfaceMuted },
  title: { fontSize: 16, fontWeight: '600', color: colors.text },
  desc: { fontSize: 13, lineHeight: 18, color: colors.textMuted, marginTop: 2 },
  badge: {
    alignSelf: 'flex-start',
    marginTop: spacing.sm,
    backgroundColor: colors.warningSoft,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.pill,
  },
  badgeText: { fontSize: 11, fontWeight: '600', color: colors.warning },
});
