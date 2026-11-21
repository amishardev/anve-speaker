// Profile: the ANVE account, the box's details, notifications, and starting over.
import React, { useEffect, useState } from 'react';
import { Alert, Switch, View } from 'react-native';
import { askNotificationPermission } from '../lib/notify';
import { useApp } from '../state';
import { Button, Card, Field, H1, H2, Notice, P, Row, Screen, Section, Small, T, Text } from '../ui';

function uptime(s?: number): string {
  if (!s) return '';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

export default function Profile({ openApprovals }: { openApprovals: () => void }) {
  const app = useApp();
  const { saved, box, boxState: s, reachable } = app;
  const [info, setInfo] = useState<any>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [room, setRoom] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');

  useEffect(() => {
    let on = true;
    if (box && reachable) box.info().then((i) => on && setInfo(i)).catch(() => {});
    return () => { on = false; };
  }, [box, reachable]);

  const saveName = async () => {
    if (!box) return;
    setBusy(true);
    try {
      const r = await box.rename(name, room);
      await app.update({ box: { ...saved.box!, name: r.name, room: r.room } });
      if (s) app.setBoxState({ ...s, name: r.name, room: r.room });
      setEditing(false);
    } catch {
      setNote('Your box didn’t answer.');
    } finally {
      setBusy(false);
    }
  };

  const toggleNotify = async (on: boolean) => {
    if (on && !(await askNotificationPermission())) {
      setNote('Allow notifications for ANVE Home in your phone’s settings.');
      return;
    }
    await app.update({ notify: on });
  };

  const reset = () =>
    Alert.alert('Reset this box?', 'It forgets everything and has to be set up again.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Reset', style: 'destructive',
        onPress: async () => {
          if (!box) return;
          setBusy(true);
          try {
            await box.factoryReset();
            if (saved.deviceId && app.signedIn) await app.anve.revokeDevice(saved.deviceId); // its old key is of no use now
            await app.forgetBox();
          } catch {
            setNote('Your box didn’t answer. Join its Wi-Fi and try again.');
          } finally {
            setBusy(false);
          }
        },
      },
    ]);

  const forget = () =>
    Alert.alert('Remove from this phone?', 'Your box keeps working.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Remove', style: 'destructive', onPress: () => app.forgetBox() },
    ]);

  const user = saved.user;
  return (
    <Screen bottom={104}>
      <H1>Profile</H1>
      <View style={{ height: 16 }} />
      {note ? <Notice kind="warn" text={note} /> : null}

      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <View style={{ width: 52, height: 52, borderRadius: 26, backgroundColor: T.fill, alignItems: 'center', justifyContent: 'center', marginRight: 14 }}>
            <Text style={{ fontSize: 20, fontWeight: '600', color: T.ink }}>{(user?.name || '?')[0].toUpperCase()}</Text>
          </View>
          <View style={{ flex: 1 }}>
            <H2>{app.signedIn && user ? user.name : 'Not signed in'}</H2>
            <Small>{app.signedIn && user ? user.username : 'Sign in to approve things'}</Small>
          </View>
        </View>
        {app.signedIn ? (
          <Button title="Sign out" kind="quiet" style={{ marginTop: 14 }}
            onPress={() => Alert.alert('Sign out?', 'Your box keeps working. Approvals stop on this phone.', [
              { text: 'Cancel', style: 'cancel' }, { text: 'Sign out', onPress: () => app.signOut() }])} />
        ) : <Button title="Sign in" style={{ marginTop: 14 }} onPress={openApprovals} />}
      </Card>

      <Section>My box</Section>
      <Card style={{ paddingVertical: 8 }}>
        {editing ? (
          <View style={{ marginTop: 10, marginBottom: 8 }}>
            <Field label="Name" value={name} onChangeText={setName} autoCapitalize="words" maxLength={40} />
            <Field label="Room" value={room} onChangeText={setRoom} autoCapitalize="words" maxLength={40} />
            <View style={{ flexDirection: 'row', gap: 10 }}>
              <Button title="Cancel" kind="quiet" style={{ flex: 1 }} onPress={() => setEditing(false)} />
              <Button title="Save" style={{ flex: 1 }} busy={busy} disabled={!name.trim()} onPress={saveName} />
            </View>
          </View>
        ) : (
          <>
            <Row icon="radio" title={s?.name || saved.box?.name || 'ANVE Box'} value={s?.room || saved.box?.room || 'No room'}
              onPress={reachable ? () => { setName(s?.name || saved.box?.name || ''); setRoom(s?.room || saved.box?.room || ''); setEditing(true); } : undefined} />
            <Row icon="pulse" title="Status" value={reachable === null ? 'Looking…' : !reachable ? 'Not reachable' : s?.online ? 'Online' : 'No ANVE connection'} />
            <Row icon="wifi" title="Wi-Fi" value={info ? `${info.wifi}${info.signal != null ? ` · ${info.signal} %` : ''}` : '—'} />
            <Row mono title="Address" value={info?.address || saved.box?.address || saved.box?.host || '—'} />
            <Row icon="thermometer" title="Temperature" value={info?.temperature != null ? `${Math.round(info.temperature)} °C` : '—'} />
            <Row icon="time" title="Running for" value={uptime(info?.uptime_s) || '—'} />
            <Row mono title="Software" value={s?.version || info?.version || '—'} last />
          </>
        )}
        {info && info.power_ok === false ? <View style={{ marginTop: 10, marginBottom: -4 }}><Notice kind="warn" icon="flash" text="Weak power supply. Your box may restart. Use a 5 A charger." /></View> : null}
      </Card>

      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <View style={{ flex: 1, paddingRight: 12 }}>
            <H2>Notifications</H2>
            <Small style={{ marginTop: 3 }}>When something needs your OK.</Small>
          </View>
          <Switch value={saved.notify} onValueChange={toggleNotify} trackColor={{ true: T.accent, false: T.fill }} thumbColor="#FFFFFF" accessibilityLabel="Notifications" />
        </View>
      </Card>

      <Card>
        <H2>Add another phone</H2>
        <P style={{ marginTop: 6, fontSize: 15 }}>On the other phone, tap “I already set mine up”.</P>
      </Card>

      <Card style={{ paddingVertical: 6 }}>
        <Row icon="phone-portrait" title="Remove from this phone" onPress={forget} />
        <Row icon="refresh-circle" title="Reset box" danger onPress={reachable ? reset : () => setNote('Join the same Wi-Fi as your box first.')} last />
      </Card>
      <Small style={{ textAlign: 'center', marginTop: 6 }}>ANVE Home 0.1.0</Small>
    </Screen>
  );
}
