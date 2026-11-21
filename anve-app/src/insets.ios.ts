// iPhone: the notch, the Dynamic Island and the home bar, from the standard library.
// (Android has its own version in insets.ts; the two files have the same exports.)
export { SafeAreaProvider as InsetsProvider, useSafeAreaInsets } from 'react-native-safe-area-context';

export type Insets = { top: number; bottom: number };
