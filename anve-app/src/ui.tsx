// The look of the app: ANVE's own design system (anve-platform/web/src/styles.css),
// carried over to the phone.
//
//   Type    IBM Plex Sans, which ANVE ships as "ANVE Sans"; Plex Mono for numbers and codes.
//   Tint    one: the violet of ANVE's mark (#4A2BC2). It marks what you can act on and what
//           is chosen. Status is said in words; hue does not carry it. Red is for errors.
//   Shape   ANVE's scale: controls 12, panels 16, the box's own panel 20. Docked surfaces
//           cast no shadow; only what floats (the tab bar) does.
//   Voice   the box itself is drawn in "ink" (#1C1C1E), ANVE's colour for the appliance.
//   Signature  the box's LIGHT RING. The firmware (anve_device/ring.py) defines what the
//           LEDs do in each state; the app shows the same ring in the same colours.
//
// Contrast against the surface, computed from the hex values (small text needs 4.5:1):
// see the table printed by scripts/contrast.py; every text colour here is 4.6:1 or better.
import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo, ActivityIndicator, Animated, Easing, GestureResponderEvent, KeyboardAvoidingView, Platform, Pressable,
  ScrollView, StyleProp, StyleSheet, Text as RNText, TextInput, TextInputProps, TextProps, TextStyle, View, ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from './insets';

const light = {
  dark: false,
  surface: '#F5F5F7', // the page
  card: '#FFFFFF', // content
  fill: '#EEEEF0', // fields, quiet buttons
  tint: '#F0EEFA', // the violet, very soft
  line: '#E8E8ED',
  ink: '#1D1D1F',
  sub: '#505055',
  faint: '#66666B',
  accent: '#4A2BC2',
  accent2: '#4A2BC2',
  onAccent: '#FFFFFF',
  primary: '#4A2BC2', // the one main button on a screen
  onPrimary: '#FFFFFF',
  // "good" and "warn" are not hues in ANVE's system: done is said in the tint, and a
  // warning is something that asks you to act, which is also the tint.
  good: '#4A2BC2',
  goodBg: '#F0EEFA',
  warn: '#4A2BC2',
  warnBg: '#F0EEFA',
  bad: '#D70015',
  badBg: '#FDECEA',
  shadow: '#000000',
  box: '#1C1C1E', // the appliance
  onBox: '#F5F5F7',
  onBox2: '#A1A1A8',
  boxLine: 'rgba(255,255,255,0.14)',
};

const darkT: typeof light = {
  dark: true,
  surface: '#0F0F11',
  card: '#1C1C1E',
  fill: '#2C2C2E',
  tint: '#2A2350',
  line: '#38383C',
  ink: '#F5F5F7',
  sub: '#A6A6AE',
  faint: '#9A9AA2',
  accent: '#B4A5FF',
  accent2: '#B4A5FF',
  onAccent: '#14101F',
  primary: '#5B3FD6',
  onPrimary: '#FFFFFF',
  good: '#B4A5FF',
  goodBg: '#2A2350',
  warn: '#B4A5FF',
  warnBg: '#2A2350',
  bad: '#FF6961',
  badBg: '#3A1418',
  shadow: '#000000',
  box: '#1C1C1E',
  onBox: '#F5F5F7',
  onBox2: '#A1A1A8',
  boxLine: 'rgba(255,255,255,0.14)',
};

/** The colours in use. Every screen reads them while it renders; `applyScheme` swaps them. */
export const T = { ...light, r: 16 };

export function applyScheme(scheme: string | null | undefined) {
  Object.assign(T, scheme === 'dark' ? darkT : light);
}

export type IconName = React.ComponentProps<typeof Ionicons>['name'];
export const Icon = Ionicons;

// ---------- type ----------

export const FONTS = {
  regular: 'IBMPlexSans_400Regular',
  medium: 'IBMPlexSans_500Medium',
  semibold: 'IBMPlexSans_600SemiBold',
  bold: 'IBMPlexSans_700Bold',
  mono: 'IBMPlexMono_500Medium',
};

function family(weight: TextStyle['fontWeight']): string {
  const w = Number(weight === 'bold' ? 700 : weight === 'normal' || weight == null ? 400 : weight);
  return w >= 700 ? FONTS.bold : w >= 600 ? FONTS.semibold : w >= 500 ? FONTS.medium : FONTS.regular;
}

/** Text in ANVE's typeface. Use this instead of React Native's Text everywhere: a custom
 *  face on Android needs its own file per weight, so `fontWeight` is turned into the
 *  right file here. `mono` sets figures and codes in Plex Mono. */
export function Text({ style, mono, ...rest }: TextProps & { mono?: boolean }) {
  const flat = (StyleSheet.flatten(style) || {}) as TextStyle;
  return <RNText {...rest} style={[flat, { fontFamily: mono ? FONTS.mono : family(flat.fontWeight), fontWeight: undefined }]} />;
}

const type = {
  large: { fontSize: 30, lineHeight: 36, fontWeight: '600', letterSpacing: -0.6 } as TextStyle,
  title: { fontSize: 22, lineHeight: 28, fontWeight: '600', letterSpacing: -0.3 } as TextStyle,
  headline: { fontSize: 17, lineHeight: 23, fontWeight: '600', letterSpacing: -0.1 } as TextStyle,
  body: { fontSize: 16, lineHeight: 23, fontWeight: '400' } as TextStyle,
  foot: { fontSize: 13.5, lineHeight: 19, fontWeight: '400' } as TextStyle,
};

export const H1 = ({ children, style }: any) => <Text accessibilityRole="header" maxFontSizeMultiplier={1.5} style={[type.large, { color: T.ink }, style]}>{children}</Text>;
export const H2 = ({ children, style, numberOfLines }: any) => <Text numberOfLines={numberOfLines} maxFontSizeMultiplier={1.7} style={[type.headline, { color: T.ink }, style]}>{children}</Text>;
export const Title = ({ children, style, numberOfLines }: any) => <Text numberOfLines={numberOfLines} maxFontSizeMultiplier={1.5} style={[type.title, { color: T.ink }, style]}>{children}</Text>;
export const P = ({ children, style }: any) => <Text style={[type.body, { color: T.sub }, style]}>{children}</Text>;
export const Small = ({ children, style, numberOfLines }: any) => <Text numberOfLines={numberOfLines} style={[type.foot, { color: T.sub }, style]}>{children}</Text>;
/** A group's caption: plain words, in sentence case. */
export const Section = ({ children }: any) => <Text accessibilityRole="header" style={{ color: T.ink, fontSize: 17, lineHeight: 23, fontWeight: '600', marginTop: 10, marginBottom: 10, marginLeft: 2 }}>{children}</Text>;

// ---------- motion ----------

let reduceMotion = false;
AccessibilityInfo.isReduceMotionEnabled().then((v) => { reduceMotion = v; }).catch(() => {});
AccessibilityInfo.addEventListener('reduceMotionChanged', (v) => { reduceMotion = v; });

export function useReduceMotion(): boolean {
  const [on, setOn] = useState(reduceMotion);
  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setOn).catch(() => {});
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setOn);
    return () => sub.remove();
  }, []);
  return on;
}

/** Feedback the moment the finger lands: the control gives a little. */
const pressed = (p: boolean, off = false): ViewStyle => (off ? { opacity: 0.4 } : p ? { transform: [{ scale: 0.97 }], opacity: 0.86 } : {});

// ---------- layout ----------

export function Screen({ children, scroll = true, bottom = 0 }: { children: React.ReactNode; scroll?: boolean; bottom?: number }) {
  const insets = useSafeAreaInsets();
  const inner: ViewStyle = { paddingTop: insets.top + 14, paddingHorizontal: 20, paddingBottom: insets.bottom + 20 + bottom, flexGrow: 1 };
  return (
    <View style={{ flex: 1, backgroundColor: T.surface }}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        {scroll ? (
          <ScrollView contentContainerStyle={inner} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
            {children}
          </ScrollView>
        ) : (
          <View style={[inner, { flex: 1 }]}>{children}</View>
        )}
      </KeyboardAvoidingView>
    </View>
  );
}

export function Card({ children, style, onPress, label }: { children: React.ReactNode; style?: StyleProp<ViewStyle>; onPress?: () => void; label?: string }) {
  const base: ViewStyle = { backgroundColor: T.card, borderRadius: T.r, padding: 16, marginBottom: 12 };
  if (onPress) {
    return (
      <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={(st) => [base, style, pressed(st.pressed)]}>
        {children}
      </Pressable>
    );
  }
  return <View style={[base, style]}>{children}</View>;
}

/** One idea per setup screen: the box's light ring, a short line in large type, and the one
 *  thing to do at the bottom. The words sit left, where reading starts. */
export function Hero({ top, title, text, children }: { top?: React.ReactNode; title: string; text?: string; children?: React.ReactNode }) {
  return (
    <View style={{ flex: 1, justifyContent: 'center', paddingVertical: 12 }}>
      {top ? <View style={{ alignItems: 'center', marginBottom: 44 }}>{top}</View> : null}
      <Text accessibilityRole="header" maxFontSizeMultiplier={1.4} style={{ fontSize: 34, lineHeight: 39, fontWeight: '600', letterSpacing: -0.9, color: T.ink }}>{title}</Text>
      {text ? <Text style={{ fontSize: 17, lineHeight: 25, color: T.sub, marginTop: 10 }}>{text}</Text> : null}
      {children}
    </View>
  );
}

/** A screen's content arrives softly: it fades in and rises a little, once, with no bounce. */
export function Appear({ children, id }: { children: React.ReactNode; id: string }) {
  const still = useReduceMotion();
  const v = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    v.setValue(still ? 1 : 0);
    if (!still) Animated.timing(v, { toValue: 1, duration: 280, easing: Easing.bezier(0.32, 0.72, 0, 1), useNativeDriver: true }).start();
  }, [id, still, v]);
  return (
    <Animated.View style={{ flex: 1, opacity: v, transform: [{ translateY: v.interpolate({ inputRange: [0, 1], outputRange: [12, 0] }) }] }}>
      {children}
    </Animated.View>
  );
}

// ---------- controls ----------

export function Button({ title, onPress, kind = 'primary', icon, busy, disabled, style }: {
  title: string; onPress?: () => void; kind?: 'primary' | 'quiet' | 'plain' | 'danger'; icon?: IconName; busy?: boolean; disabled?: boolean; style?: StyleProp<ViewStyle>;
}) {
  const bg = kind === 'primary' ? T.primary : kind === 'quiet' ? T.fill : kind === 'danger' ? T.badBg : 'transparent';
  const fg = kind === 'primary' ? T.onPrimary : kind === 'danger' ? T.bad : kind === 'plain' ? T.accent : T.ink;
  const off = !!(disabled || busy);
  return (
    <Pressable
      onPress={off ? undefined : onPress}
      accessibilityRole="button"
      accessibilityLabel={title}
      accessibilityState={{ disabled: off, busy: !!busy }}
      style={(st) => [{ minHeight: 54, borderRadius: 14, paddingHorizontal: 22, paddingVertical: 13, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', backgroundColor: bg }, style, pressed(st.pressed, off && !busy)]}>
      {busy ? <ActivityIndicator color={fg} /> : (
        <>
          {icon ? <Ionicons name={icon} size={19} color={fg} style={{ marginRight: 8 }} /> : null}
          <Text maxFontSizeMultiplier={1.6} style={{ fontSize: 17, fontWeight: '600', color: fg, textAlign: 'center' }}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

/** An icon button. Never smaller than 44 x 44: a finger needs that much. */
export function RoundButton({ icon, onPress, size = 48, kind = 'quiet', disabled, label }: { icon: IconName; onPress?: () => void; size?: number; kind?: 'quiet' | 'primary' | 'onBox'; disabled?: boolean; label: string }) {
  const d = Math.max(44, size);
  const bg = kind === 'primary' ? T.primary : kind === 'onBox' ? 'rgba(255,255,255,0.12)' : T.fill;
  const fg = kind === 'primary' ? T.onPrimary : kind === 'onBox' ? T.onBox : T.ink;
  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled }}
      hitSlop={8}
      style={(st) => [{ width: d, height: d, borderRadius: d / 2, alignItems: 'center', justifyContent: 'center', backgroundColor: bg }, pressed(st.pressed, disabled)]}>
      <Ionicons name={icon} size={d * 0.44} color={fg} />
    </Pressable>
  );
}

export function Field({ label, hint, secret, style, mono, ...rest }: TextInputProps & { label: string; hint?: string; secret?: boolean; mono?: boolean }) {
  const [show, setShow] = useState(false);
  const [focus, setFocus] = useState(false);
  return (
    <View style={[{ marginBottom: 14 }, style as any]}>
      <Text style={{ fontSize: 13.5, color: T.sub, fontWeight: '500', marginBottom: 7, marginLeft: 2 }}>{label}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: T.card, borderRadius: 12, paddingHorizontal: 14, minHeight: 54, borderWidth: 1.5, borderColor: focus ? T.accent : T.line }}>
        <TextInput
          accessibilityLabel={label}
          placeholderTextColor={T.faint}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry={secret && !show}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          style={{ flex: 1, fontSize: 17, color: T.ink, paddingVertical: 13, fontFamily: mono ? FONTS.mono : FONTS.regular, letterSpacing: mono ? 2 : 0 }}
          {...rest}
        />
        {secret ? (
          <Pressable onPress={() => setShow(!show)} hitSlop={12} accessibilityRole="button" accessibilityLabel={show ? 'Hide password' : 'Show password'} style={{ minWidth: 32, minHeight: 44, alignItems: 'center', justifyContent: 'center' }}>
            <Ionicons name={show ? 'eye-off-outline' : 'eye-outline'} size={21} color={T.sub} />
          </Pressable>
        ) : null}
      </View>
      {hint ? <Text style={{ fontSize: 13.5, lineHeight: 19, color: T.sub, marginTop: 6, marginLeft: 2 }}>{hint}</Text> : null}
    </View>
  );
}

export function Chip({ title, active, onPress, icon }: { title: string; active?: boolean; onPress?: () => void; icon?: IconName }) {
  const fg = active ? T.accent : T.ink;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: !!active }}
      style={(st) => [{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 15, paddingVertical: 9, minHeight: 44, borderRadius: 12, backgroundColor: active ? T.tint : T.card, borderWidth: 1.5, borderColor: active ? T.accent : 'transparent', marginRight: 8, marginBottom: 8 }, pressed(st.pressed)]}>
      {icon ? <Ionicons name={icon} size={16} color={fg} style={{ marginRight: 7 }} /> : null}
      <Text maxFontSizeMultiplier={1.5} style={{ color: fg, fontWeight: active ? '600' : '500', fontSize: 15 }}>{title}</Text>
    </Pressable>
  );
}

export function Notice({ text, kind = 'info', icon }: { text: string; kind?: 'info' | 'warn' | 'bad' | 'good'; icon?: IconName }) {
  const bad = kind === 'bad';
  const name: IconName = icon ?? (bad ? 'alert-circle' : kind === 'good' ? 'checkmark-circle' : 'information-circle');
  return (
    <View accessibilityRole={bad ? 'alert' : undefined} style={{ flexDirection: 'row', borderRadius: 12, padding: 14, marginBottom: 12, backgroundColor: bad ? T.badBg : T.tint }}>
      <Ionicons name={name} size={20} color={bad ? T.bad : T.accent} style={{ marginRight: 10, marginTop: 1 }} />
      <Text style={{ color: T.ink, flex: 1, fontSize: 15, lineHeight: 21 }}>{text}</Text>
    </View>
  );
}

/** A settings row: words and a value. `mono` sets the value as a figure. */
export function Row({ title, value, onPress, danger, last, mono }: { icon?: IconName; title: string; value?: string; onPress?: () => void; danger?: boolean; last?: boolean; mono?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={!onPress} accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={value ? `${title}, ${value}` : title}
      style={(st) => [{ flexDirection: 'row', alignItems: 'center', minHeight: 52, paddingVertical: 9 }, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: T.line }, st.pressed && { opacity: 0.6 }]}>
      <Text style={{ flex: 1, fontSize: 16.5, color: danger ? T.bad : T.ink }}>{title}</Text>
      {value ? <Text mono={mono} style={{ color: T.sub, fontSize: mono ? 14.5 : 16, maxWidth: '58%' }} numberOfLines={1}>{value}</Text> : null}
      {onPress ? <Ionicons name="chevron-forward" size={17} color={T.faint} style={{ marginLeft: 6 }} /> : null}
    </Pressable>
  );
}

export function IconTile({ icon, color, bg, size = 44 }: { icon: IconName; color?: string; bg?: string; size?: number }) {
  return (
    <View style={{ width: size, height: size, borderRadius: 12, backgroundColor: bg ?? T.tint, alignItems: 'center', justifyContent: 'center' }}>
      <Ionicons name={icon} size={size * 0.5} color={color ?? T.accent} />
    </View>
  );
}

// ---------- the signature: the box's light ring ----------

export type Presence = 'ready' | 'listening' | 'thinking' | 'speaking' | 'muted' | 'trouble' | 'away' | 'searching' | 'done';

const LEDS = 24;
// The colours of anve_device/ring.py, lifted for a screen:
//   idle (0,40,34) teal · listening (0,200,255) · thinking (190,60,255) · speaking (0,255,70)
//   muted and errors red · nothing lit when the box cannot be reached
const LED: Record<Presence, string> = {
  ready: '#35C4AE', listening: '#3CC8FF', searching: '#3CC8FF', thinking: '#BE6BFF', speaking: '#3DF07A',
  muted: '#FF453A', trouble: '#FF453A', away: '#5A5A60', done: '#FFFFFF',
};

/**
 * The box, drawn as what it is: a dark puck with a ring of lights. The lights do what the
 * real ones do (anve_device/ring.py):
 *   ready      a calm teal ring              listening  the ring breathes, cyan
 *   thinking   a violet light runs round     speaking   the ring pulses green with the voice
 *   mic off / trouble   red                  away       unlit
 *   searching  a cyan light runs round (setup)          done   the whole ring, white
 * It is the only thing in the app that moves by itself. With "reduce motion" on it is
 * still: the colour and the icon in the middle carry the state.
 */
export function Orb({ state, size = 84, children, calm }: { state: Presence; size?: number; children?: React.ReactNode; /** the lights hold still: what is inside does the moving */ calm?: boolean }) {
  const still = useReduceMotion();
  const a = useRef(new Animated.Value(0)).current;
  const runs = !calm && (state === 'thinking' || state === 'searching');
  const breathes = !calm && (state === 'listening' || state === 'speaking');
  const moving = !still && (runs || breathes);

  useEffect(() => {
    a.setValue(0);
    if (!moving) return;
    const loop = runs
      ? Animated.loop(Animated.timing(a, { toValue: 1, duration: state === 'thinking' ? 1300 : 2000, easing: Easing.linear, useNativeDriver: true }))
      : Animated.loop(Animated.sequence([
          Animated.timing(a, { toValue: 1, duration: state === 'speaking' ? 420 : 900, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
          Animated.timing(a, { toValue: 0, duration: state === 'speaking' ? 420 : 900, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        ]));
    loop.start();
    return () => loop.stop();
  }, [state, moving, runs, a]);

  const color = LED[state];
  const r = size * 0.4;
  const d = Math.max(3, size * 0.05);
  const icon: IconName = state === 'away' ? 'cloud-offline' : state === 'muted' ? 'mic-off' : state === 'trouble' ? 'alert' : state === 'done' ? 'checkmark' : state === 'searching' ? 'wifi' : 'mic';
  const dots = Array.from({ length: LEDS }, (_, i) => {
    const t = (i / LEDS) * Math.PI * 2;
    // a light that runs round: a bright head with a fading tail, twice round the ring
    const tail = (LEDS - (i % (LEDS / 2))) % (LEDS / 2);
    const o = runs ? (tail < 7 ? 1 - tail * 0.13 : 0.1) : state === 'away' ? 0.55 : state === 'ready' ? 0.75 : 1;
    return (
      <View key={i} style={{ position: 'absolute', width: d, height: d, borderRadius: d / 2, backgroundColor: color, opacity: o,
        left: size / 2 + Math.sin(t) * r - d / 2, top: size / 2 - Math.cos(t) * r - d / 2 }} />
    );
  });
  return (
    <View accessible={false} style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: T.box, alignItems: 'center', justifyContent: 'center',
      borderWidth: T.dark ? StyleSheet.hairlineWidth : 0, borderColor: T.boxLine }}>
      <Animated.View pointerEvents="none" style={{ position: 'absolute', width: size, height: size,
        opacity: moving && breathes ? a.interpolate({ inputRange: [0, 1], outputRange: [0.4, 1] }) : 1,
        transform: [{ rotate: moving && runs ? a.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] }) : '0deg' }] }}>
        {dots}
      </Animated.View>
      {children ?? <Ionicons name={icon} size={size * 0.24} color={state === 'away' ? T.onBox2 : T.onBox} />}
    </View>
  );
}

/** A slider drawn by hand (no extra native library): drag or tap anywhere on the track.
 *  `onBox` is its look on the box's dark panel. */
export function Slider({ value, onChange, onDone, disabled, label = 'Volume', onBox }: { value: number; onChange?: (v: number) => void; onDone?: (v: number) => void; disabled?: boolean; label?: string; onBox?: boolean }) {
  const [w, setW] = useState(1);
  const [drag, setDrag] = useState<number | null>(null);
  const shown = drag ?? value;
  const at = (e: GestureResponderEvent) => Math.max(0, Math.min(100, Math.round((e.nativeEvent.locationX / w) * 100)));
  const step = (d: number) => onDone?.(Math.max(0, Math.min(100, value + d)));
  return (
    <View
      onLayout={(e) => setW(e.nativeEvent.layout.width)}
      onStartShouldSetResponder={() => !disabled}
      onMoveShouldSetResponder={() => !disabled}
      onResponderGrant={(e) => { const v = at(e); setDrag(v); onChange?.(v); }}
      onResponderMove={(e) => { const v = at(e); setDrag(v); onChange?.(v); }}
      onResponderRelease={(e) => { const v = at(e); setDrag(null); onDone?.(v); }}
      onResponderTerminate={() => setDrag(null)}
      accessible
      accessibilityRole="adjustable"
      accessibilityLabel={label}
      accessibilityValue={{ min: 0, max: 100, now: shown, text: `${shown} percent` }}
      accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
      onAccessibilityAction={(e) => step(e.nativeEvent.actionName === 'increment' ? 10 : -10)}
      style={{ flex: 1, height: 44, justifyContent: 'center', opacity: disabled ? 0.4 : 1 }}>
      <View pointerEvents="none" style={{ height: 6, borderRadius: 3, backgroundColor: onBox ? 'rgba(255,255,255,0.18)' : T.fill, overflow: 'hidden' }}>
        <View style={{ width: `${shown}%`, height: 6, backgroundColor: onBox ? T.onBox : T.accent }} />
      </View>
      <View pointerEvents="none" style={{ position: 'absolute', left: Math.max(0, Math.min(w - 22, (shown / 100) * w - 11)), width: 22, height: 22, borderRadius: 11, backgroundColor: '#FFFFFF', elevation: 3, shadowColor: '#000', shadowOpacity: 0.25, shadowRadius: 4, shadowOffset: { width: 0, height: 1 } }} />
    </View>
  );
}

export function Steps({ at, total }: { at: number; total: number }) {
  return (
    <View accessibilityRole="progressbar" accessibilityLabel={`Step ${at + 1} of ${total}`} style={{ flexDirection: 'row', gap: 5, marginBottom: 26 }}>
      {Array.from({ length: total }, (_, i) => (
        <View key={i} style={{ flex: 1, height: 3, borderRadius: 2, backgroundColor: i <= at ? T.ink : T.line }} />
      ))}
    </View>
  );
}

export function Back({ onPress }: { onPress: () => void }) {
  return (
    <Pressable onPress={onPress} hitSlop={10} accessibilityRole="button" accessibilityLabel="Back"
      style={(st) => [{ width: 44, height: 44, borderRadius: 12, backgroundColor: T.card, alignItems: 'center', justifyContent: 'center', marginBottom: 18 }, pressed(st.pressed)]}>
      <Ionicons name="chevron-back" size={22} color={T.ink} />
    </Pressable>
  );
}
