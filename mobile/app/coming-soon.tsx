import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';

import { AppHeader } from '@/components/AppHeader';
import { Button } from '@/components/Button';
import { Screen } from '@/components/Screen';
import { colors, font, spacing } from '@/constants/theme';

const FEATURES: Record<string, { title: string; icon: keyof typeof Ionicons.glyphMap; text: string }> = {
  research: {
    title: 'Research',
    icon: 'search-outline',
    text: 'Source-grounded web research with preserved URLs is planned for Phase 6.',
  },
  voice: {
    title: 'Voice',
    icon: 'mic-outline',
    text: 'Speech-to-text and transcript correction are planned for Phase 7.',
  },
  documents: {
    title: 'Documents',
    icon: 'document-text-outline',
    text: 'PDF and image understanding, with privacy scanning of extracted text, is planned for Phase 7.',
  },
};

export default function ComingSoonScreen() {
  const { feature } = useLocalSearchParams<{ feature?: string }>();
  const router = useRouter();
  const info = FEATURES[feature ?? ''] ?? FEATURES.research;

  return (
    <Screen>
      <AppHeader title={info.title} />
      <View style={styles.body}>
        <View style={styles.iconWrap}>
          <Ionicons name={info.icon} size={36} color={colors.primary} />
        </View>
        <Text style={styles.title}>Coming soon</Text>
        <Text style={[font.body, styles.text]}>{info.text}</Text>
        <Text style={font.small}>This feature is not implemented yet.</Text>
        <View style={styles.button}>
          <Button label="Back to Home" variant="secondary" onPress={() => router.replace('/')} />
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl },
  iconWrap: {
    width: 76,
    height: 76,
    borderRadius: 38,
    backgroundColor: colors.primarySoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.lg,
  },
  title: { ...font.title, fontSize: 24, marginBottom: spacing.sm },
  text: { textAlign: 'center', color: colors.textMuted, marginBottom: spacing.sm },
  button: { marginTop: spacing.xl, alignSelf: 'stretch' },
});
