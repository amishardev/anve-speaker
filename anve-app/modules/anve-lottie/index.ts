// The native Lottie player (Android). See android/.../AnveLottieModule.kt.
import { requireNativeView } from 'expo';
import { ComponentType } from 'react';
import { Platform, ViewProps } from 'react-native';

export type LottieProps = ViewProps & {
  /** the animation, as text */
  json: string;
  /** a name for this animation in these colours, so it is parsed once */
  cacheKey: string;
  loop?: boolean;
  speed?: number;
  /** where it ends, 0..1 */
  end?: number;
  /** show this moment (0..1) as a still picture instead of playing */
  still?: number | null;
  /** room round the drawing, for files that draw a little outside their own frame */
  inset?: number;
};

let view: ComponentType<LottieProps> | null = null;
if (Platform.OS === 'android') {
  try {
    view = requireNativeView<LottieProps>('AnveLottie');
  } catch {
    view = null; // Expo Go, or a build without the module: the app shows a plain icon instead
  }
}
export default view;
