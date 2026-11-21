// Talking to the ANVE box on the local network (anve-device/anve_device/appapi.py).
import { Channel, openReply, seal } from './crypto';

export const BOX_PORT = 8738;

export type Playing = {
  source: 'youtube' | 'radio' | 'web';
  title: string;
  paused: boolean;
  ducked?: boolean;
  position?: number;
  duration?: number;
  art?: string;
};

export type BoxState = {
  id: string;
  name: string;
  room: string;
  version: string;
  online: boolean;
  halted: boolean;
  state: string; // idle | listening | thinking | speaking | muted | error | recording | alarm
  muted: boolean;
  recording: boolean;
  volume: number;
  speaker: boolean;
  mic: boolean;
  playing: Playing | null;
  approvals: number;
};

export type BoxEvent = { seq: number; t: number; kind: string; [k: string]: any };

export type SavedBox = { id: string; name: string; room: string; token: string; host: string; address?: string };

export class BoxError extends Error {
  constructor(public code: string, public status = 0) {
    super(code);
  }
}

/** One request, tried twice: on some networks an answer now and then arrives cut short. */
async function http(url: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<{ status: number; json: any }> {
  try {
    const first = await once(url, init);
    if (first.json !== null) return first;
  } catch {}
  return once(url, init);
}

async function once(url: string, init: RequestInit & { timeoutMs?: number }): Promise<{ status: number; json: any }> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), init.timeoutMs ?? 8000);
  try {
    const r = await fetch(url, { ...init, signal: ctl.signal, credentials: 'omit', cache: 'no-store' });
    let json: any = null;
    try {
      json = await r.json();
    } catch {}
    return { status: r.status, json };
  } catch {
    throw new BoxError('unreachable');
  } finally {
    clearTimeout(timer);
  }
}

/** Who is at this address? Works without a token. */
export async function hello(base: string, timeoutMs = 4000): Promise<any> {
  const { status, json } = await http(base + '/api/hello', { timeoutMs });
  if (status !== 200 || !json?.id) throw new BoxError('not_a_box', status);
  return json;
}

/** Pair with a box that is already set up, using the 6-digit code from tools/pair_app.py. */
export async function pair(address: string, code: string, phone: string): Promise<SavedBox> {
  const base = baseOf(address);
  const h = await hello(base);
  if (h.setup) throw new BoxError('in_setup');
  if (!h.pairing) throw new BoxError('pairing_closed');
  const { message, key } = seal(h.pub, { code: code.replace(/\D/g, ''), phone });
  const { status, json } = await http(base + '/api/pair', { method: 'POST', body: JSON.stringify(message) });
  if (status !== 200) throw new BoxError(json?.error || 'failed', status);
  const rep = openReply(key, json);
  return { id: rep.id, name: rep.name, room: rep.room, token: rep.token, host: `anve-${rep.id}.local`, address: hostOf(address) };
}

export function hostOf(address: string): string {
  return address.trim().replace(/^https?:\/\//, '').replace(/[:/].*$/, '');
}

export function baseOf(address: string): string {
  return `http://${hostOf(address)}:${BOX_PORT}`;
}

/** How to look for boxes on the Wi-Fi without an address (set by the app at start; it
 *  needs the phone's own network service, which a plain script does not have). */
export const finder: { discover: null | ((timeoutMs: number) => Promise<{ id: string; address: string }[]>) } = { discover: null };

export class Box {
  private chan: Channel;
  private base: string | null = null;
  private clock = 0;

  constructor(public saved: SavedBox, private onAddress?: (address: string) => void) {
    this.chan = new Channel(saved.token);
  }

  /** Find the box: the address that worked last time, then its name on the network. */
  async locate(): Promise<string> {
    if (this.base) return this.base;
    const tries = [this.saved.address, this.saved.host].filter(Boolean) as string[];
    for (const host of tries) {
      try {
        const h = await hello(baseOf(host), 3500);
        if (h.id === this.saved.id && !h.setup) {
          this.base = baseOf(host);
          if (host !== this.saved.address && /^\d+\.\d+\.\d+\.\d+$/.test(host)) this.onAddress?.(host);
          return this.base;
        }
      } catch {}
    }
    // not where it was: ask the network who is there (the box announces itself)
    try {
      const hit = (await finder.discover?.(3500))?.find((b) => b.id === this.saved.id);
      if (hit) {
        const h = await hello(baseOf(hit.address), 3500);
        if (h.id === this.saved.id && !h.setup) {
          this.base = baseOf(hit.address);
          this.onAddress?.(hit.address);
          return this.base;
        }
      }
    } catch {}
    throw new BoxError('unreachable');
  }

  setAddress(address: string) {
    this.saved = { ...this.saved, address: hostOf(address) };
    this.base = null;
  }

  async call(method: 'GET' | 'POST', target: string, body?: unknown, timeoutMs = 8000): Promise<any> {
    for (let attempt = 0; attempt < 2; attempt++) {
      const base = await this.locate();
      const req = this.chan.request(method, target, method === 'POST' ? body ?? {} : undefined, this.clock);
      let res;
      try {
        res = await http(base + target, {
          method,
          headers: { 'X-ANVE-Auth': req.auth, ...(req.body ? { 'Content-Type': 'application/json' } : {}) },
          body: req.body || undefined,
          timeoutMs,
        });
      } catch (e) {
        this.base = null; // it may have a new address: look again next time
        throw e;
      }
      if (res.status === 200) return this.chan.open(res.json, req.nonce);
      if (res.status === 401 && res.json?.now && attempt === 0) {
        // this phone's clock is off: use the box's time and try once more
        const off = res.json.now - Math.floor(Date.now() / 1000);
        if (Math.abs(off) > 30) {
          this.clock = off;
          continue;
        }
      }
      throw new BoxError(res.json?.error || 'failed', res.status);
    }
    throw new BoxError('auth', 401);
  }

  state = async (): Promise<{ seq: number; state: BoxState }> => this.call('GET', '/api/state');
  events = async (since: number, wait = 25): Promise<{ seq: number; reset?: boolean; events: BoxEvent[]; state: BoxState }> =>
    this.call('GET', `/api/events?since=${since}&wait=${wait}`, undefined, (wait + 8) * 1000);
  volume = (level: number) => this.call('POST', '/api/volume', { level: Math.round(level) });
  media = (action: 'pause' | 'resume' | 'next') => this.call('POST', '/api/media', { do: action });
  play = (query: string) => this.call('POST', '/api/play', { query });
  radio = (station = 'default') => this.call('POST', '/api/play', { radio: station });
  mute = (on: boolean) => this.call('POST', '/api/mute', { on });
  stop = () => this.call('POST', '/api/stop', {});
  rename = (name: string, room: string) => this.call('POST', '/api/name', { name, room });
  info = () => this.call('GET', '/api/info');
  factoryReset = () => this.call('POST', '/api/reset', { confirm: 'reset' });
}
