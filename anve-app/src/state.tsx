// One place that knows what the app knows: the saved box, the ANVE session, what the
// box is doing right now and what waits for approval.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AppState, useColorScheme } from 'react-native';
import { Anve, AnveError, Pending } from './lib/anve';
import Wifi from '../modules/anve-wifi';
import { Box, BoxState, finder, SavedBox } from './lib/box';
import { announceNew, announceNotice, askNotificationPermission, initNotifications, startBackgroundCheck } from './lib/notify';
import { EMPTY, load, save, Saved } from './lib/store';
import { setRehearsal } from './lib/setup';
import { applyScheme } from './ui';

type Ctx = {
  scheme: 'light' | 'dark';
  ready: boolean;
  saved: Saved;
  update: (patch: Partial<Saved>) => Promise<void>;
  anve: Anve;
  signedIn: boolean;
  signIn: (server: string, username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  box: Box | null;
  boxState: BoxState | null;
  reachable: boolean | null; // null: still looking
  setBoxState: (s: BoxState) => void;
  adoptBox: (b: SavedBox, deviceId?: string) => Promise<void>;
  forgetBox: () => Promise<void>;
  pending: Pending[];
  pendingError: string;
  refreshPending: () => Promise<void>;
  answer: (p: Pending, approved: boolean) => Promise<void>;
};

const wifi = Wifi;
finder.discover = wifi ? (ms) => wifi.discover(ms) : null;

const C = createContext<Ctx>(null as any);
export const useApp = () => useContext(C);

export function AppProvider({ children }: { children: React.ReactNode }) {
  // Light or dark, as the phone is set. The colours are switched here, before anything
  // below draws; every screen reads this context, so every screen redraws with them.
  const scheme = useColorScheme() === 'dark' ? 'dark' : 'light';
  applyScheme(scheme);
  const [ready, setReady] = useState(false);
  const [saved, setSaved] = useState<Saved>(EMPTY);
  const savedRef = useRef(saved);
  const [signedIn, setSignedIn] = useState(false);
  const [boxState, setBoxState] = useState<BoxState | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [pendingError, setPendingError] = useState('');

  const update = useCallback(async (patch: Partial<Saved>) => {
    const next = { ...savedRef.current, ...patch };
    savedRef.current = next;
    setSaved(next);
    await save(next).catch(() => {});
  }, []);

  const anve = useMemo(() => new Anve(saved.server), [saved.server]);

  const box = useMemo(
    () => (saved.box ? new Box(saved.box, (address) => update({ box: { ...savedRef.current.box!, address } })) : null),
    // a new Box only when it is a different box or token, not when its address is noted
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [saved.box?.id, saved.box?.token],
  );

  useEffect(() => {
    (async () => {
      const s = await load();
      savedRef.current = s;
      setSaved(s);
      // Show the app at once. Whether the ANVE session is still good is checked in the
      // background: waiting for it kept the app on a spinner for 15 s whenever ANVE could
      // not be reached (seen 18 Nov 2026). Until ANVE says otherwise, a saved sign-in counts.
      setSignedIn(!!s.user);
      setReady(true);
      initNotifications().catch(() => {});
      if (s.user) {
        new Anve(s.server).me().catch((e) => {
          // offline is not "signed out": keep the session and try again later
          if (e instanceof AnveError && e.code === 'signed_out') setSignedIn(false);
        });
      }
    })();
  }, []);

  const refreshPending = useCallback(async () => {
    if (!savedRef.current.user) return;
    try {
      const list = await anve.pending();
      setPending(list);
      setPendingError('');
      setSignedIn(true);
      // no notifications in the middle of first-time setup
      const seen = await announceNew(list, savedRef.current.seen, savedRef.current.notify && !!savedRef.current.box);
      if (seen.join() !== savedRef.current.seen.join()) await update({ seen });
    } catch (e) {
      if (e instanceof AnveError && e.code === 'signed_out') {
        setSignedIn(false);
        setPending([]);
      } else {
        setPendingError('offline');
      }
    }
  }, [anve, update]);

  // ask ANVE what waits: every 8 s while the app is open, and whenever it comes back
  useEffect(() => {
    if (!ready || !signedIn) return;
    refreshPending();
    const t = setInterval(() => AppState.currentState === 'active' && refreshPending(), 8000);
    const sub = AppState.addEventListener('change', (s) => s === 'active' && refreshPending());
    return () => {
      clearInterval(t);
      sub.remove();
    };
  }, [ready, signedIn, refreshPending]);

  useEffect(() => {
    if (signedIn && saved.notify) {
      askNotificationPermission().catch(() => {});
      startBackgroundCheck();
    }
  }, [signedIn, saved.notify]);

  // follow the box: one long request that returns the moment something changes
  useEffect(() => {
    if (!box) {
      setBoxState(null);
      setReachable(null);
      return;
    }
    let alive = true;
    let seq = 0;
    let misses = 0;
    (async () => {
      while (alive) {
        try {
          const r = await box.events(seq, 25);
          if (!alive) return;
          misses = 0;
          seq = r.seq;
          setBoxState(r.state);
          setReachable(true);
          for (const e of r.events) {
            if (e.kind === 'approval' || e.kind === 'waiting' || e.kind === 'approval_done') refreshPending();
            else if (e.kind === 'notice' && e.text && savedRef.current.notify) announceNotice(e.text).catch(() => {});
          }
        } catch {
          if (!alive) return;
          misses += 1;
          if (misses >= 2) setReachable(false);
          await new Promise((r) => setTimeout(r, misses < 2 ? 1200 : AppState.currentState === 'active' ? 4000 : 20000));
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [box, refreshPending]);

  const value: Ctx = {
    scheme,
    ready,
    saved,
    update,
    anve,
    signedIn,
    signIn: async (server, username, password) => {
      const a = new Anve(server);
      const me = await a.login(username, password);
      setRehearsal((me as any).rehearsal_box);
      await update({ server, user: { username: me.username || username.trim(), name: me.name || me.display_name || username.trim() } });
      setSignedIn(true);
    },
    signOut: async () => {
      await anve.logout();
      setSignedIn(false);
      setPending([]);
      await update({ user: null, seen: [] });
    },
    box,
    boxState,
    reachable,
    setBoxState,
    adoptBox: async (b, deviceId) => update({ box: b, ...(deviceId !== undefined ? { deviceId } : {}) }),
    forgetBox: async () => update({ box: null, deviceId: '' }),
    pending,
    pendingError,
    refreshPending,
    answer: async (p, approved) => {
      await anve.answer(p, approved);
      setPending((list) => list.filter((x) => x !== p));
      refreshPending();
    },
  };
  return <C.Provider value={value}>{children}</C.Provider>;
}
