import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, View } from 'react-native';

import { colors } from '@/constants/theme';

export function Logo({ size = 44 }: { size?: number }) {
  return (
    <View style={[styles.box, { width: size, height: size, borderRadius: size * 0.3 }]}>
      <Ionicons name="eye" size={size * 0.55} color={colors.textOnPrimary} />
    </View>
  );
}

const styles = StyleSheet.create({
  box: { backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
});
