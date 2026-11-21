// Where the status bar and the navigation bar are, so content stays clear of them.
//
// The usual library for this (react-native-safe-area-context) is left out on purpose:
// its long name pushed the Android C++ build over Windows' path-length limit even from
// the shortest possible folder. The app's own native module answers the same question.
import React, { useEffect, useState } from 'react';
import { Dimensions, Platform, StatusBar } from 'react-native';
import Native from '../modules/anve-wifi';

export type Insets = { top: number; bottom: number };

/** Nothing to set up on Android. (On iPhone, insets.ios.ts, this is the library's provider.) */
export function InsetsProvider({ children }: { children: React.ReactNode }) {
  return children as React.ReactElement;
}

function read(): Insets {
  const fallback = Platform.OS === 'ios' ? { top: 54, bottom: 34 } : { top: StatusBar.currentHeight ?? 28, bottom: 24 };
  try {
    const i = Native?.insets();
    if (i && i.top >= 0) return { top: i.top, bottom: Math.max(0, i.bottom) };
  } catch {}
  return fallback;
}

export function useSafeAreaInsets(): Insets {
  const [insets, setInsets] = useState<Insets>(read);
  useEffect(() => {
    // the window may not be laid out at the very first render: look again shortly after
    const timers = [150, 800].map((ms) => setTimeout(() => setInsets(read()), ms));
    const sub = Dimensions.addEventListener('change', () => setInsets(read()));
    return () => {
      timers.forEach(clearTimeout);
      sub.remove();
    };
  }, []);
  return insets;
}
