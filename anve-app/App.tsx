// ANVE Home. Setup is shown only until this phone has a box; after that the app opens
// on Home, with Approvals and Profile one tap away in the bar at the bottom.
import * as Notifications from 'expo-notifications';
import { StatusBar } from 'expo-status-bar';
import React, { useEffect, useState } from 'react';
import { IBMPlexMono_500Medium } from '@expo-google-fonts/ibm-plex-mono';
import { IBMPlexSans_400Regular, IBMPlexSans_500Medium, IBMPlexSans_600SemiBold, IBMPlexSans_700Bold } from '@expo-google-fonts/ibm-plex-sans';
import { useFonts } from 'expo-font';
import { ActivityIndicator, BackHandler, Pressable, View } from 'react-native';
import { InsetsProvider, useSafeAreaInsets } from './src/insets';
import Approvals from './src/screens/Approvals';
import Home from './src/screens/Home';
import Onboarding from './src/screens/Onboarding';
import Profile from './src/screens/Profile';
import { AppProvider, useApp } from './src/state';
import { Icon, IconName, T, Text } from './src/ui';

type Tab = 'home' | 'approvals' | 'profile';
const TABS: { id: Tab; icon: IconName; iconOn: IconName; label: string }[] = [
  { id: 'home', icon: 'home-outline', iconOn: 'home', label: 'Home' },
  { id: 'approvals', icon: 'shield-checkmark-outline', iconOn: 'shield-checkmark', label: 'Approvals' },
  { id: 'profile', icon: 'person-outline', iconOn: 'person', label: 'Profile' },
];

/** Three places, each with its icon AND its name; the one you are in is filled and tinted. */
function TabBar({ tab, setTab, badge }: { tab: Tab; setTab: (t: Tab) => void; badge: number }) {
  const insets = useSafeAreaInsets();
  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, right: 0, bottom: Math.max(insets.bottom, 10) + 8, alignItems: 'center' }}>
      <View accessibilityRole="tablist" style={{
        flexDirection: 'row', backgroundColor: T.card, borderRadius: 32, paddingHorizontal: 8, paddingVertical: 6,
        borderWidth: T.dark ? 1 : 0, borderColor: T.line, shadowColor: T.shadow, shadowOpacity: 0.12, shadowRadius: 16, shadowOffset: { width: 0, height: 6 }, elevation: T.dark ? 0 : 6,
      }}>
        {TABS.map((t) => {
          const on = t.id === tab;
          const n = t.id === 'approvals' ? badge : 0;
          return (
            <Pressable key={t.id} onPress={() => setTab(t.id)} accessibilityRole="tab" accessibilityState={{ selected: on }}
              accessibilityLabel={n ? `${t.label}, ${n} waiting` : t.label}
              style={({ pressed }) => [{ minWidth: 92, minHeight: 52, paddingHorizontal: 10, alignItems: 'center', justifyContent: 'center' }, pressed && { opacity: 0.7, transform: [{ scale: 0.97 }] }]}>
              {/* keyed: Android kept square corners when only the colour of this view changed */}
              <View key={on ? 'on' : 'off'} style={{ width: 56, height: 30, borderRadius: 15, overflow: 'hidden', backgroundColor: on ? T.tint : 'transparent', alignItems: 'center', justifyContent: 'center' }}>
                <Icon name={on ? t.iconOn : t.icon} size={21} color={on ? T.accent : T.sub} />
                {n > 0 ? (
                  <View style={{ position: 'absolute', top: -4, right: 2, minWidth: 18, height: 18, borderRadius: 9, backgroundColor: T.bad, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4, borderWidth: 2, borderColor: T.card }}>
                    <Text maxFontSizeMultiplier={1.2} style={{ color: T.dark ? '#101014' : '#FFFFFF', fontSize: 10.5, fontWeight: '700' }}>{n > 9 ? '9+' : n}</Text>
                  </View>
                ) : null}
              </View>
              <Text maxFontSizeMultiplier={1.3} style={{ color: on ? T.accent : T.sub, fontWeight: on ? '600' : '500', fontSize: 12, marginTop: 3 }}>{t.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

function Root() {
  const { ready, saved, pending } = useApp();
  const [tab, setTab] = useState<Tab>('home');
  // ANVE's typeface (IBM Plex Sans; Plex Mono for figures), bundled with the app
  const [fontsReady] = useFonts({ IBMPlexSans_400Regular, IBMPlexSans_500Medium, IBMPlexSans_600SemiBold, IBMPlexSans_700Bold, IBMPlexMono_500Medium });

  // a tap on a notification opens the place it is about
  useEffect(() => {
    const open = (r: Notifications.NotificationResponse | null) => {
      const where = r?.notification.request.content.data?.open;
      if (where === 'approvals' || where === 'home') setTab(where);
    };
    Notifications.getLastNotificationResponseAsync().then(open).catch(() => {});
    const sub = Notifications.addNotificationResponseReceivedListener(open);
    return () => sub.remove();
  }, []);

  // a box was just set up or added: the app opens on Home, whatever tab was open before
  const boxId = saved.box?.id;
  useEffect(() => {
    if (boxId) setTab('home');
  }, [boxId]);

  // Android's back: from Approvals or Profile to Home first; from Home it leaves the app
  useEffect(() => {
    if (!saved.box) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (tab === 'home') return false;
      setTab('home');
      return true;
    });
    return () => sub.remove();
  }, [tab, saved.box]);

  if (!ready || !fontsReady) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: T.surface }}>
        <ActivityIndicator size="large" color={T.accent} />
      </View>
    );
  }
  if (!saved.box) return <Onboarding />;
  return (
    <View style={{ flex: 1, backgroundColor: T.surface }}>
      {tab === 'home' ? <Home openApprovals={() => setTab('approvals')} /> : tab === 'approvals' ? <Approvals /> : <Profile openApprovals={() => setTab('approvals')} />}
      <TabBar tab={tab} setTab={setTab} badge={pending.length} />
    </View>
  );
}

export default function App() {
  return (
    <InsetsProvider>
      <AppProvider>
        <StatusBar style="auto" />
        <Root />
      </AppProvider>
    </InsetsProvider>
  );
}
