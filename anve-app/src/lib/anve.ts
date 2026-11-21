// The ANVE platform, with the person's own account. Approvals are answered HERE and
// nowhere else: a room speaker may never approve anything by itself, so the app signs in
// as the person and uses the same endpoints as ANVE's own web pages. The platform keeps
// the session in a cookie (anve_session, 14 days); React Native stores it for us.
export const DEFAULT_SERVER = 'https://app.anveai.com';

export class AnveError extends Error {
  constructor(public code: string, public status = 0, public detail = '') {
    super(detail || code);
  }
}

export type Confirmation = {
  kind: 'confirmation';
  id: string;
  action: string;
  readback: string;
  status: string;
  created_at?: number | string;
  expires_at?: number | string;
};

export type Approval = {
  kind: 'approval';
  run_id: string;
  agent: string;
  step?: string | number;
  what: string;
  message: string;
  args?: string;
  mode?: string;
  trigger?: string;
};

export type Pending = Confirmation | Approval;

export const pendingId = (p: Pending) => (p.kind === 'confirmation' ? 'c:' + p.id : 'a:' + p.run_id);

export class Anve {
  constructor(public server: string = DEFAULT_SERVER) {}

  private async req(method: string, path: string, body?: unknown, timeoutMs = 15000): Promise<any> {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    let r: Response;
    try {
      r = await fetch(this.server.replace(/\/+$/, '') + path, {
        method,
        credentials: 'include',
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctl.signal,
      });
    } catch {
      throw new AnveError('offline');
    } finally {
      clearTimeout(timer);
    }
    let json: any = null;
    try {
      json = await r.json();
    } catch {}
    if (r.ok) return json;
    const detail = typeof json?.detail === 'string' ? json.detail : '';
    if (r.status === 401) throw new AnveError(path.endsWith('/login') ? 'wrong_login' : 'signed_out', 401, detail);
    if (r.status === 429) throw new AnveError('too_many', 429, detail);
    throw new AnveError(r.status === 409 ? 'conflict' : r.status === 404 ? 'gone' : 'failed', r.status, detail);
  }

  async login(username: string, password: string) {
    await this.req('POST', '/api/auth/login', { username: username.trim(), password });
    return this.me();
  }

  async me(): Promise<{ id?: string | number; username?: string; name?: string; display_name?: string; role?: string }> {
    return (await this.req('GET', '/api/auth/me')).user ?? {};
  }

  logout = () => this.req('POST', '/api/auth/logout', {}).catch(() => {});

  async pending(): Promise<Pending[]> {
    const p = await this.req('GET', '/api/assistant/pending');
    return [
      ...(p?.confirmations ?? []).filter((c: any) => c.status === 'pending').map((c: any) => ({ ...c, kind: 'confirmation' as const })),
      ...(p?.approvals ?? []).map((a: any) => ({ ...a, kind: 'approval' as const })),
    ];
  }

  /** Approve or decline. Declining never does anything; approving does what the card says. */
  answer(p: Pending, approved: boolean) {
    return p.kind === 'confirmation'
      ? this.req('POST', `/api/assistant/confirmations/${encodeURIComponent(p.id)}`, { approved })
      : this.req('POST', `/api/agents/runs/${encodeURIComponent(p.run_id)}/decision`, { approved });
  }

  /** A new key for a room speaker. The key is shown by the platform once, here. */
  async createDevice(name: string): Promise<{ id: string; key: string }> {
    const r = await this.req('POST', '/api/assistant/devices', { name, kind: 'room' });
    if (!r?.key) throw new AnveError('failed', 0, 'The platform returned no key.');
    return { id: String(r.device?.id ?? ''), key: r.key };
  }

  /** Revoke a speaker's key (after a factory reset, or when setup was abandoned). */
  revokeDevice = (id: string) => this.req('DELETE', `/api/assistant/devices/${encodeURIComponent(id)}`).catch(() => {});

  async devices(): Promise<any[]> {
    const r = await this.req('GET', '/api/assistant/devices');
    return Array.isArray(r) ? r : r?.devices ?? [];
  }
}
