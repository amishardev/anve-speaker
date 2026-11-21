// What the app remembers between launches. The box token is a secret: it lives in the
// phone's keystore (expo-secure-store), never in plain storage. The ANVE password is
// never stored at all; the platform's session cookie is kept by the system.
import * as SecureStore from 'expo-secure-store';
import type { SavedBox } from './box';

const KEY = 'anve.home.v1';

export type Saved = {
  box: SavedBox | null; // null: first-time setup has not been done on this phone
  server: string;
  user: { username: string; name: string } | null;
  deviceId: string; // the box's id in ANVE's device list (to revoke its key on a reset)
  notify: boolean;
  seen: string[]; // approvals already announced, so a notification comes once
};

export const EMPTY: Saved = { box: null, server: 'https://app.anveai.com', user: null, deviceId: '', notify: true, seen: [] };

export async function load(): Promise<Saved> {
  try {
    const raw = await SecureStore.getItemAsync(KEY);
    return raw ? { ...EMPTY, ...JSON.parse(raw) } : EMPTY;
  } catch {
    return EMPTY;
  }
}

export async function save(s: Saved): Promise<void> {
  await SecureStore.setItemAsync(KEY, JSON.stringify({ ...s, seen: s.seen.slice(-30) }));
}
