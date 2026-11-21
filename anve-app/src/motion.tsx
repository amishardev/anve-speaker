// Small animations from LottieFiles (see assets/lottie/SOURCES.md), redrawn in the app's own
// colours before they are played, so they look like part of ANVE and follow light and dark.
//
// Each one stands where an icon would: searching, joining Wi-Fi, done, and music playing.
// With "reduce motion" on a still is shown; where no player exists, a plain icon.
// The player itself differs by platform: player.tsx (Android), player.ios.tsx (iPhone).
import React, { useMemo } from 'react';
import { View } from 'react-native';
import { available, Player } from './player';
import { Icon, IconName, T, useReduceMotion } from './ui';

type Clip = {
  data: object;
  loop: boolean;
  /** where it ends, 0..1: "done" draws itself away again after this, so it stops here */
  end?: number;
  /** the moment shown when nothing may move */
  still: number;
  speed?: number;
  /** room round the drawing, as a share of its size (this file draws outside its frame) */
  inset?: number;
  icon: IconName;
};

const CLIPS = {
  search: { data: require('../assets/lottie/search.json'), loop: true, still: 0.12, speed: 0.6, inset: 0.06, icon: 'search' },
  wifi: { data: require('../assets/lottie/wifi.json'), loop: true, still: 0.3, speed: 1.3, icon: 'wifi' },
  done: { data: require('../assets/lottie/done.json'), loop: false, end: 0.27, still: 0.27, icon: 'checkmark' },
  playing: { data: require('../assets/lottie/playing.json'), loop: true, still: 0.5, icon: 'musical-notes' },
} satisfies Record<string, Clip>;

export type MotionName = keyof typeof CLIPS;

type RGB = [number, number, number];
const rgb = (hex: string): RGB => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as RGB;
const lum = (c: number[]) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];

/** Every fill and stroke colour in a Lottie file, fixed or changing over time. */
function eachColour(o: any, f: (c: number[]) => number[]): void {
  if (Array.isArray(o)) { o.forEach((v) => eachColour(v, f)); return; }
  if (!o || typeof o !== 'object') return;
  if ((o.ty === 'fl' || o.ty === 'st') && o.c && Array.isArray(o.c.k)) {
    const k = o.c.k;
    if (typeof k[0] === 'number') o.c.k = f(k);
    else k.forEach((frame: any) => { for (const key of ['s', 'e']) if (Array.isArray(frame?.[key])) frame[key] = f(frame[key]); });
  }
  Object.values(o).forEach((v) => eachColour(v, f));
}

/**
 * Redraw an animation in one colour. Its darkest colour becomes `strong`; lighter ones fade
 * towards the surface it sits on, which keeps "lit" and "not yet lit" parts apart (the
 * Wi-Fi arcs, the radar's sweep) without bringing a second hue into the app.
 */
function tint(data: object, strong: RGB, surface: RGB): object {
  const a = JSON.parse(JSON.stringify(data));
  let lo = 1, hi = 0;
  eachColour(a, (c) => { const l = lum(c); lo = Math.min(lo, l); hi = Math.max(hi, l); return c; });
  const span = hi - lo;
  eachColour(a, (c) => {
    const t = span > 0.12 ? ((lum(c) - lo) / span) * 0.62 : 0;
    return [0, 1, 2].map((i) => strong[i] + (surface[i] - strong[i]) * t).concat([c[3] ?? 1]);
  });
  return a;
}

const made = new Map<string, object>();

/**
 * tone "onBox": drawn in the box's light colour, for the middle of the light ring.
 * tone "accent": drawn in the app's violet, on a card.
 */
export function Motion({ name, size = 96, tone = 'onBox', paused }: { name: MotionName; size?: number; tone?: 'onBox' | 'accent'; paused?: boolean }) {
  const reduce = useReduceMotion();
  const clip: Clip = CLIPS[name];
  const strong = tone === 'onBox' ? T.onBox : T.accent;
  const surface = tone === 'onBox' ? T.box : T.card;
  const key = `${name}|${strong}|${surface}`;
  const data = useMemo(() => {
    if (!available) return null;
    let d = made.get(key);
    if (!d) { d = tint(clip.data, rgb(strong), rgb(surface)); made.set(key, d); }
    return d;
  }, [key, clip, strong, surface]);

  if (!data) return <Icon name={clip.icon} size={size * 0.46} color={strong} />;
  return (
    <View pointerEvents="none" accessible={false} style={{ width: size, height: size }}>
      <Player data={data} id={key} size={size} loop={clip.loop} speed={clip.speed ?? 1} end={clip.end ?? 1}
        still={reduce || paused ? clip.still : null} inset={(clip.inset ?? 0) * size} />
    </View>
  );
}
