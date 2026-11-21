// The iPhone player: the standard lottie-react-native. Same props as player.tsx.
import LottieView from 'lottie-react-native';
import React, { useEffect, useRef } from 'react';
import { View } from 'react-native';
import type { PlayerProps } from './player';

export const available = true;

export function Player({ data, id, size, loop, speed, end, still, inset }: PlayerProps) {
  const ref = useRef<LottieView>(null);
  const first = data.ip ?? 0;
  const last = first + ((data.op ?? 0) - first) * end;

  useEffect(() => {
    if (still != null) return;
    // an animation that must stop early (the check mark draws itself away again after
    // its middle) is played by frame numbers; the others just run
    if (end < 1) ref.current?.play(first, last);
    else ref.current?.play();
  }, [id, still, end, first, last]);

  return (
    <View style={{ width: size, height: size, padding: inset }}>
      <LottieView ref={ref} key={id} source={data as any} loop={loop} speed={speed} autoPlay={false}
        progress={still ?? undefined} resizeMode="contain" style={{ flex: 1 }} />
    </View>
  );
}
