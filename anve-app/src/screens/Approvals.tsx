// Approvals: everything ANVE or one of the person's agents will not do without a yes.
// The answer goes straight to ANVE with the person's own sign-in, never through the box.
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, RefreshControl, ScrollView, View } from 'react-native';
import { useSafeAreaInsets } from '../insets';
import { AnveError, DEFAULT_SERVER, Pending, pendingId } from '../lib/anve';
import { Motion } from '../motion';
import { useApp } from '../state';
import { Button, Card, Field, H1, H2, Icon, Notice, Orb, P, Small, T, Text } from '../ui';

function title(p: Pending): string {
  if (p.kind === 'approval') return p.what || 'An agent step';
  const a = (p.action || 'action').replace(/_/g, ' ');
  return a.charAt(0).toUpperCase() + a.slice(1);
}

function left(expires: Pending extends infer _ ? any : never, now: number): string {
  const t = typeof expires === 'number' ? expires * (expires < 1e12 ? 1000 : 1) : expires ? Date.parse(expires) : NaN;
  if (!isFinite(t)) return '';
  const s = Math.round((t - now) / 1000);
  if (s <= 0) return 'expired';
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s left` : `${s} s left`;
}

function Item({ p, now }: { p: Pending; now: number }) {
  const { answer } = useApp();
  const [busy, setBusy] = useState<'yes' | 'no' | ''>('');
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const agent = p.kind === 'approval';
  const body = agent ? p.message : p.readback;
  const remaining = agent ? '' : left(p.expires_at, now);

  const go = async (approved: boolean) => {
    setBusy(approved ? 'yes' : 'no');
    setError('');
    try {
      await answer(p, approved);
    } catch (e) {
      const c = e instanceof AnveError ? e.code : '';
      setError(c === 'conflict' ? 'Already answered, or expired.' : c === 'gone' ? 'This is no longer waiting.'
        : c === 'offline' ? 'No internet. Nothing was sent.' : 'That didn’t go through. Nothing was done.');
      setBusy('');
    }
  };

  const approve = () => {
    // a live agent step really does the thing: one more look before it goes
    if (agent && p.mode === 'live') {
      Alert.alert('Approve?', `${p.agent}: ${p.what}`, [
        { text: 'Not yet', style: 'cancel' },
        { text: 'Approve', onPress: () => go(true) },
      ]);
    } else go(true);
  };

  return (
    <Card>
      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        <View style={{ flex: 1 }}>
          <Small style={{ fontWeight: '600' }}>{agent ? `${p.agent || 'Agent'}${p.mode ? ` · ${p.mode} run` : ''}` : 'Asked by voice'}</Small>
          <H2 numberOfLines={2}>{title(p)}</H2>
        </View>
      </View>
      {body ? <Text style={{ color: T.ink, fontSize: 15.5, lineHeight: 22, marginTop: 12 }}>{body}</Text> : null}
      {agent && p.args ? (
        <>
          <Text onPress={() => setOpen(!open)} accessibilityRole="button" style={{ color: T.accent, fontWeight: '600', fontSize: 15, paddingVertical: 10, marginTop: 2 }}>{open ? 'Hide details' : 'See details'}</Text>
          {open ? (
            <View style={{ backgroundColor: T.fill, borderRadius: 14, padding: 12 }}>
              <Text mono style={{ fontSize: 12.5, lineHeight: 18, color: T.ink }}>{String(p.args).slice(0, 4000)}</Text>
            </View>
          ) : null}
        </>
      ) : null}
      {remaining ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 10 }}>
          <Icon name="time-outline" size={15} color={remaining === 'expired' ? T.bad : T.sub} />
          <Small style={{ marginLeft: 5, color: remaining === 'expired' ? T.bad : T.sub }}>{remaining}</Small>
        </View>
      ) : null}
      {error ? <View style={{ marginTop: 12 }}><Notice kind="bad" text={error} /></View> : null}
      <View style={{ flexDirection: 'row', gap: 10, marginTop: 14 }}>
        <Button title="Decline" kind="quiet" style={{ flex: 1 }} busy={busy === 'no'} disabled={!!busy} onPress={() => go(false)} />
        <Button title="Approve" icon="checkmark" style={{ flex: 1.3 }} busy={busy === 'yes'} disabled={!!busy || remaining === 'expired'} onPress={approve} />
      </View>
    </Card>
  );
}

function SignIn() {
  const app = useApp();
  const [username, setUsername] = useState(app.saved.user?.username ?? '');
  const [password, setPassword] = useState('');
  const [server, setServer] = useState(app.saved.server || DEFAULT_SERVER);
  const [showServer, setShowServer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const go = async () => {
    setBusy(true);
    setError('');
    try {
      await app.signIn(server.trim() || DEFAULT_SERVER, username, password);
    } catch (e) {
      const c = e instanceof AnveError ? e.code : '';
      setError(c === 'wrong_login' ? 'Wrong username or password.' : c === 'too_many' ? 'Too many tries. Wait a minute.' : c === 'offline' ? 'Cannot reach ANVE.' : 'Sign-in did not work.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <H2>Sign in</H2>
      <P style={{ marginTop: 6, marginBottom: 14 }}>Use your ANVE account.</P>
      {error ? <Notice kind="bad" text={error} /> : null}
      <Field label="Username" value={username} onChangeText={setUsername} autoComplete="username" />
      <Field label="Password" value={password} onChangeText={setPassword} secret autoComplete="password" onSubmitEditing={go} />
      {showServer ? <Field label="ANVE address" value={server} onChangeText={setServer} keyboardType="url" /> : (
        <Text onPress={() => setShowServer(true)} accessibilityRole="button" style={{ color: T.sub, fontSize: 13, paddingVertical: 10, marginBottom: 4, marginLeft: 4 }}>
          Using {server.replace(/^https?:\/\//, '')} · change
        </Text>
      )}
      <Button title="Sign in" onPress={go} busy={busy} disabled={!username.trim() || !password} />
    </Card>
  );
}

export default function Approvals() {
  const { signedIn, pending, pendingError, refreshPending, saved } = useApp();
  const insets = useSafeAreaInsets();
  const [pulling, setPulling] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (signedIn) refreshPending();
  }, [signedIn, refreshPending]);

  return (
    <View style={{ flex: 1, backgroundColor: T.surface }}>
      <ScrollView
        contentContainerStyle={{ paddingTop: insets.top + 14, paddingHorizontal: 20, paddingBottom: insets.bottom + 128, flexGrow: 1 }}
        refreshControl={<RefreshControl refreshing={pulling} tintColor={T.accent} onRefresh={async () => { setPulling(true); await refreshPending(); setPulling(false); }} />}>
        <H1>Approvals</H1>
        <P style={{ marginTop: 6, marginBottom: 18 }}>
          {!signedIn ? 'Sign in to see what needs your OK.' : pending.length ? 'Nothing happens until you approve.' : 'Things that need your OK show up here.'}
        </P>
        {!signedIn ? <SignIn /> : (
          <>
            {pendingError ? <Notice kind="warn" text="Cannot reach ANVE right now. Pull down to try again." /> : null}
            {pending.map((p) => <Item key={pendingId(p)} p={p} now={now} />)}
            {!pending.length && !pendingError ? (
              <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: 60 }}>
                <Orb state="done" size={120}><Motion name="done" size={66} /></Orb>
                <H2 style={{ marginTop: 18 }}>All caught up</H2>
                <Small style={{ marginTop: 6, textAlign: 'center' }}>{saved.notify ? 'We’ll notify you when something needs you.' : 'Notifications are off.'}</Small>
              </View>
            ) : null}
            {pulling ? <ActivityIndicator color={T.accent} /> : null}
          </>
        )}
      </ScrollView>
    </View>
  );
}
