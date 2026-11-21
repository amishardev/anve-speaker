// Plays one Lottie animation. This is the Android player: the app's own small module
// (modules/anve-lottie). The iPhone player is player.ios.tsx; both take the same props.
import React from 'react';
import Lottie from '../modules/anve-lottie';

export type PlayerProps = {
  /** the animation, already in the app's colours */
  data: { ip?: number; op?: number } & object;
  /** a name for this animation in these colours */
  id: string;
  size: number;
  loop: boolean;
  speed: number;
  /** where it ends, 0..1 */
  end: number;
  /** show this moment (0..1) as a still picture instead of playing */
  still: number | null;
  /** room round the drawing, in points */
  inset: number;
};

export const available = !!Lottie;

const text = new Map<string, string>();

export function Player({ data, id, size, loop, speed, end, still, inset }: PlayerProps) {
  if (!Lottie) return null;
  let json = text.get(id);
  if (!json) { json = JSON.stringify(data); text.set(id, json); }
  return <Lottie json={json} cacheKey={id} loop={loop} speed={speed} end={end} still={still} inset={inset} style={{ width: size, height: size }} />;
}
