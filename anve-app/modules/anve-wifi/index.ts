// The phone's side of joining the ANVE box's setup hotspot (Android). See
// android/src/main/java/expo/modules/anvewifi/AnveWifiModule.kt for what each call does.
import { requireOptionalNativeModule } from 'expo';

export type SeenNetwork = { ssid: string; level: number };
export type FoundBox = { name: string; address: string; port: number; id: string };

type Native = {
  /** Wi-Fi networks in range. Needs the location permission, and Location switched on. */
  scan(): Promise<SeenNetwork[]>;
  /** The Wi-Fi the phone is on ('' when unknown). */
  currentSsid(): string;
  /** Join a hotspot for this app only; resolves once this app's traffic goes through it. */
  join(ssid: string, password: string): Promise<boolean>;
  /** Give the hotspot up: the app's traffic goes back to the phone's normal network. */
  leave(): void;
  /** The person joined the hotspot in the phone's settings: use Wi-Fi for this app. */
  useWifi(): boolean;
  /** What the status bar (top) and navigation bar (bottom) cover; -1 while not known yet. */
  insets(): { top: number; bottom: number };
  /** Set-up ANVE boxes on the Wi-Fi the phone is on (they announce themselves). */
  discover(timeoutMs: number): Promise<FoundBox[]>;
};

// null on iOS and in Expo Go: the app then asks the person to join the box's Wi-Fi by hand
export default requireOptionalNativeModule<Native>('AnveWifi');
