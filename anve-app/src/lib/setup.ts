// First-time setup over the box's own Wi-Fi hotspot (anve-device/anve_device/provision.py).
//
//   findBoxes()            look for hotspots named "ANVE-xxxx" (the phone stays on its Wi-Fi)
//   joinHotspot(ssid)      this app, and only this app, talks through the box's hotspot
//   hello(), networks()    who the box is, which Wi-Fi networks it can see
//   provision(...)         send Wi-Fi, name and ANVE key, sealed; the box answers with the
//                          token for later, then leaves the hotspot and joins the Wi-Fi
//   leaveHotspot()         the phone goes back to its own Wi-Fi
//
// The hotspot is an open network, so everything secret is sealed for the box's public
// key before it is sent (lib/crypto.ts).
import { PermissionsAndroid, Platform } from 'react-native';
import Wifi from '../../modules/anve-wifi';
import { openReply, seal } from './crypto';

export const SETUP_BASE = 'http://10.42.0.1';

// Rehearsing setup without a box in setup mode: the stand-in ANVE server used for testing
// (scripts/mock_anve.py --setup-base ...) may name a stand-in box. The real ANVE never
// does, so on a real account this stays null and nothing below changes.
let rehearsal: string | null = null;
export const setRehearsal = (url: string | null | undefined) => { rehearsal = url || null; };
const base = () => rehearsal ?? SETUP_BASE;
export const PREFIX = 'ANVE-';

export type Network = { ssid: string; signal: number; secure: boolean; sec: string; supported: boolean };
export type SetupHello = { id: string; name: string; version: string; setup: boolean; recovery: boolean; busy: boolean; error: string; pub: string; has_key: boolean; trial?: boolean };

export class SetupError extends Error {
  constructor(public code: string) {
    super(code);
  }
}

/** Android needs the location permission to see Wi-Fi names. Asked once, with the reason. */
export async function askWifiPermission(): Promise<boolean> {
  if (rehearsal || Platform.OS !== 'android') return true;
  const wanted = [PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION];
  if (Number(Platform.Version) >= 33) wanted.push('android.permission.NEARBY_WIFI_DEVICES' as any);
  const res = await PermissionsAndroid.requestMultiple(wanted);
  return res[PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION] === PermissionsAndroid.RESULTS.GRANTED;
}

/** Can this phone look for the box and join its hotspot from inside the app? */
export const canJoinFromApp = () => !!rehearsal || (Platform.OS === 'android' && !!Wifi && Number(Platform.Version) >= 29);

/** Hotspots of boxes waiting to be set up, strongest first. */
export async function findBoxes(): Promise<{ ssid: string; level: number }[]> {
  if (rehearsal) return [{ ssid: 'ANVE-TEST', level: -40 }];
  if (!Wifi) return []; // iPhone: apps cannot list Wi-Fi networks, the person joins by hand
  let list;
  try {
    list = await Wifi.scan();
  } catch (e: any) {
    throw new SetupError(e?.code === 'wifi_off' ? 'wifi_off' : 'scan_failed');
  }
  return list.filter((w) => w.ssid.startsWith(PREFIX)).sort((a, b) => b.level - a.level);
}

/** The Wi-Fi this phone is on now: offered as the first choice for the box. */
export function currentWifi(): string {
  try {
    const s = Wifi?.currentSsid() ?? '';
    return s.startsWith(PREFIX) ? '' : s;
  } catch {
    return '';
  }
}

async function get(path: string, timeoutMs = 5000): Promise<any> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(base() + path, { signal: ctl.signal, credentials: 'omit', cache: 'no-store' });
    return await r.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function hello(): Promise<SetupHello> {
  const h = await get('/api/hello');
  if (!h?.id || !h?.pub) throw new SetupError('not_a_box');
  return h;
}

/** Join the box's hotspot and wait until the box answers on it. With `byHand`, the
 *  person has joined it in the phone's Wi-Fi settings and we only wait for the box. */
export async function joinHotspot(ssid: string, byHand = false): Promise<SetupHello> {
  if (rehearsal) return hello();
  if (!byHand) {
    if (!Wifi) throw new SetupError('join_by_hand');
    try {
      await Wifi.join(ssid, ''); // an open network; Android shows its own sheet to approve it
    } catch (e: any) {
      throw new SetupError(e?.code === 'unavailable' ? 'join_denied' : e?.code === 'unsupported' ? 'join_by_hand' : 'join_failed');
    }
  } else {
    // joined in the phone's settings: Android must still be told to use that Wi-Fi for
    // this app, or it sends the requests over mobile data and the box is never reached
    try {
      Wifi?.useWifi();
    } catch {}
  }
  for (let i = 0; i < (byHand ? 8 : 15); i++) {
    try {
      return await hello();
    } catch {
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  throw new SetupError('box_silent');
}

export function leaveHotspot(): void {
  try {
    Wifi?.leave();
  } catch {}
}

export async function networks(): Promise<Network[]> {
  return (await get('/api/networks'))?.networks ?? [];
}

export type ProvisionInput = {
  wifi: { ssid: string; password: string; hidden?: boolean; sec?: string };
  portal?: { username: string; password: string };
  name: string;
  room: string;
  key?: string;
};

/** Send everything, sealed. -> the token and network name the app uses from now on. */
export async function provision(pub: string, input: ProvisionInput): Promise<{ id: string; token: string; host: string; port: number }> {
  const { message, key } = seal(pub, input);
  let r: Response;
  try {
    r = await fetch(base() + '/api/provision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(message),
      credentials: 'omit',
    });
  } catch {
    throw new SetupError('box_silent');
  }
  const json = await r.json().catch(() => null);
  if (r.status !== 200) throw new SetupError(json?.field ? 'bad_' + json.field : json?.error || 'refused');
  const rep = openReply(key, json);
  if (!rep?.token) throw new SetupError('refused');
  return rep;
}

/** What went wrong, in a few plain words. */
export function explain(code: string): string {
  const m: Record<string, string> = {
    wrong_password: 'Wrong Wi-Fi password. Try again.',
    not_found: 'Can’t find that Wi-Fi. Move your box closer to the router.',
    timeout: 'That Wi-Fi didn’t respond. Try again.',
    unsupported: 'This type of Wi-Fi isn’t supported.',
    failed: 'Couldn’t join that Wi-Fi. Try again.',
    internal: 'Something went wrong. Unplug your box, plug it back in, and try again.',
    join_denied: 'Tap Connect when your phone asks.',
    box_not_found: 'Can’t find your box. Is it plugged in and nearby?',
    join_failed: 'Couldn’t connect to your box. Try again.',
    join_by_hand: 'Join the Wi-Fi that starts with ANVE-, then come back.',
    box_silent: 'Your box isn’t answering. Stay close and try again.',
    scan_failed: 'Turn on Wi-Fi and Location, then try again.',
    wifi_off: 'Turn on Wi-Fi to find your box.',
    busy: 'Your box is busy. Try again in a minute.',
    trial: 'Test passed. Nothing was changed.',
    not_in_setup: 'Your box is busy. Try again in a minute.',
    'bad_wifi.password': 'Wi-Fi passwords have at least 8 characters.',
    'bad_wifi.ssid': 'That Wi-Fi name isn’t valid.',
    bad_key: 'Sign in again, then retry.',
    bad_portal: 'Check the sign-in name and password.',
  };
  return m[code] || 'That didn’t work. Try again.';
}
