// First-time setup, shown only until a box has been set up or added on this phone:
//   welcome -> sign in -> find the box -> name it -> its Wi-Fi -> done
// and the side door "I already set mine up" (add a box with a 6-digit code).
//
// One idea per screen, a few plain words, one main button at the bottom. Anything a
// person does not need in order to act is left out.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { BackHandler, Linking, Platform, Pressable, Switch, View } from 'react-native';
import { AnveError, DEFAULT_SERVER } from '../lib/anve';
import { baseOf, Box, BoxError, finder, hello as boxHello, pair, SavedBox } from '../lib/box';
import * as Setup from '../lib/setup';
import { Motion } from '../motion';
import { useApp } from '../state';
import { Appear, Back, Button, Card, Chip, Field, H1, Hero, Icon, Notice, Orb, P, Screen, Section, Small, Steps, T, Text } from '../ui';

type Step = 'welcome' | 'signin' | 'find' | 'name' | 'wifi' | 'waiting' | 'done' | 'pair';
const ROOMS = ['Living room', 'Bedroom', 'Kitchen', 'Office', 'Dining room', 'Kids’ room'];
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function signInError(e: unknown): string {
  const c = e instanceof AnveError ? e.code : '';
  return c === 'wrong_login' ? 'Wrong username or password.' : c === 'too_many' ? 'Too many tries. Wait a minute.'
    : c === 'offline' ? 'No internet. Check your connection.' : 'Couldn’t sign in. Try again.';
}

export default function Onboarding() {
  const app = useApp();
  const [step, setStep] = useState<Step>('welcome');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // sign in
  const [server, setServer] = useState(app.saved.server || DEFAULT_SERVER);
  const [showServer, setShowServer] = useState(false);
  const [username, setUsername] = useState(app.saved.user?.username ?? '');
  const [password, setPassword] = useState('');
  // the box
  const [found, setFound] = useState<{ ssid: string; level: number }[]>([]);
  const [findState, setFindState] = useState<'looking' | 'permission' | 'hand'>('looking');
  const [slow, setSlow] = useState(false);
  const [scanNote, setScanNote] = useState('');
  const [ssid, setSsid] = useState('');
  const [byHand, setByHand] = useState(false);
  const [name, setName] = useState('');
  const [room, setRoom] = useState('Living room');
  const [boxHelloInfo, setBoxHello] = useState<Setup.SetupHello | null>(null);
  const [nets, setNets] = useState<Setup.Network[]>([]);
  const device = useRef<{ id: string; key: string } | null>(null);
  // its Wi-Fi
  const [phase, setPhase] = useState<'joining' | 'hand' | 'pick'>('joining');
  const [phoneWifi, setPhoneWifi] = useState('');
  const [wifi, setWifi] = useState('');
  const [other, setOther] = useState(false);
  const [wifiPass, setWifiPass] = useState('');
  const [portal, setPortal] = useState(false);
  const [portalUser, setPortalUser] = useState('');
  const [portalPass, setPortalPass] = useState('');
  // waiting for it to come online
  const [result, setResult] = useState<SavedBox | null>(null);
  const [stuck, setStuck] = useState(false);
  // adding a box that is already set up
  const [address, setAddress] = useState('');
  const [code, setCode] = useState('');
  const [nearby, setNearby] = useState<{ id: string; address: string }[]>([]);
  const [typeAddress, setTypeAddress] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const boxName = name.trim() || 'ANVE Box';
  const go = (s: Step) => { setError(''); setStep(s); };

  // Android's back button: one step back, as the arrow at the top does
  const backRef = useRef<() => boolean>(() => false);
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => backRef.current());
    return () => sub.remove();
  }, []);

  // ---------- sign in ----------
  const signIn = async () => {
    setBusy(true);
    setError('');
    try {
      await app.signIn(server.trim() || DEFAULT_SERVER, username, password);
      setPassword('');
      go('find');
    } catch (e) {
      setError(signInError(e));
    } finally {
      setBusy(false);
    }
  };

  // ---------- find the box ----------
  const [scanRound, setScanRound] = useState(0);
  useEffect(() => {
    if (step !== 'find') return;
    let on = true;
    setSlow(false);
    const slowTimer = setTimeout(() => on && setSlow(true), 25000);
    (async () => {
      if (!Setup.canJoinFromApp()) {
        // an iPhone or an old Android cannot join from inside the app: the person joins
        // the box's Wi-Fi in the phone's settings, later, when the app asks
        setByHand(true);
        setFindState('hand');
        return;
      }
      if (!(await Setup.askWifiPermission())) {
        setFindState('permission');
        return;
      }
      setFindState('looking');
      setPhoneWifi(Setup.currentWifi());
      while (on) {
        try {
          const list = await Setup.findBoxes();
          if (!on) return;
          setFound(list);
          setScanNote('');
        } catch (e: any) {
          if (on) setScanNote(Setup.explain(e?.code));
        }
        await sleep(5000);
      }
    })();
    return () => {
      on = false;
      clearTimeout(slowTimer);
    };
  }, [step, scanRound]);

  // ---------- connect to the box, then choose its Wi-Fi ----------
  const connectToBox = useCallback(async (joinedByHand = false) => {
    setPhase('joining');
    setError('');
    try {
      if (!device.current) {
        // The box's key is made now, while the phone still has internet: the box's own
        // Wi-Fi has none. An earlier try may have left this app tied to that Wi-Fi, so
        // let go of it first; and one lost request is not worth a red message.
        Setup.leaveHotspot();
        const label = room ? `${boxName} (${room})` : boxName;
        try {
          device.current = await app.anve.createDevice(label);
        } catch (e) {
          if (!(e instanceof AnveError) || e.code !== 'offline') throw e;
          await sleep(2500);
          device.current = await app.anve.createDevice(label);
        }
      }
      if (byHand && !joinedByHand) {
        setPhase('hand');
        return;
      }
      const h = await Setup.joinHotspot(ssid, byHand);
      if (!alive.current) return;
      if (!h.setup) {
        setError(h.busy ? Setup.explain('busy') : 'This box is already set up.');
        return;
      }
      setBoxHello(h);
      if (h.error) setError(Setup.explain(h.error));
      const list = (await Setup.networks()).filter((n) => n.supported);
      setNets(list);
      if (!wifi) setWifi(list.find((n) => n.ssid === phoneWifi)?.ssid ?? (list.length === 1 ? list[0].ssid : ''));
      setPhase('pick');
    } catch (e: any) {
      if (!alive.current) return;
      const c = e instanceof AnveError ? e.code : e?.code;
      setError(c === 'signed_out' ? 'Please sign in again.' : c === 'offline' ? 'No internet. Check your connection.'
        : e instanceof AnveError ? 'Couldn’t register your box. Try again.' : Setup.explain(c));
    }
  }, [app.anve, boxName, byHand, phoneWifi, room, ssid, wifi]);

  // coming back to the Wi-Fi step after a failed try: the phone is already on the box's Wi-Fi
  const alreadyJoined = useRef(false);
  useEffect(() => {
    if (step !== 'wifi') return;
    if (alreadyJoined.current) alreadyJoined.current = false;
    else connectToBox();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const chosen = nets.find((n) => n.ssid === wifi);
  const needsPass = other ? wifiPass.length > 0 : !!chosen?.secure;
  const canSend = !!wifi.trim() && (!needsPass || wifiPass.length >= 8) && (!portal || (!!portalUser && !!portalPass)) && !!boxHelloInfo;

  const send = async () => {
    if (!boxHelloInfo) return;
    setBusy(true);
    setError('');
    try {
      const rep = await Setup.provision(boxHelloInfo.pub, {
        wifi: { ssid: wifi.trim(), password: wifiPass, hidden: other, sec: other ? (wifiPass ? 'psk' : '') : chosen?.sec ?? '' },
        ...(portal ? { portal: { username: portalUser.trim(), password: portalPass } } : {}),
        name: boxName,
        room: room.trim(),
        key: device.current?.key,
      });
      Setup.leaveHotspot(); // the phone's own network again
      const box: SavedBox = { id: rep.id, name: boxName, room: room.trim(), token: rep.token, host: rep.host };
      setResult(box);
      setStuck(false);
      setStep('waiting');
      waitForBox(box);
    } catch (e: any) {
      setError(Setup.explain(e?.code));
    } finally {
      setBusy(false);
    }
  };

  // ---------- wait until the box is on the Wi-Fi ----------
  const waitForBox = async (box: SavedBox) => {
    const started = Date.now();
    while (alive.current && Date.now() - started < 100_000) {
      await sleep(3000);
      // "Set up" means more than "a box with this id answers": it must accept the token it
      // just gave us AND carry the name we just sent. (A box that fell back to its old
      // Wi-Fi after a wrong password also answers, with its old settings.)
      try {
        const h = await boxHello(baseOf(box.host), 3000);
        if (h.id === box.id && !h.setup && (await took(box))) {
          setStep('done');
          return;
        }
      } catch {}
      try {
        // by its announcement on the Wi-Fi (works where the .local name does not)
        const hit = (await finder.discover?.(2500))?.find((b) => b.id === box.id);
        if (hit && (await took({ ...box, address: hit.address }))) {
          setResult({ ...box, address: hit.address });
          setStep('done');
          return;
        }
      } catch {}
      // did it give up and reopen its own Wi-Fi? Then it could not join: find out why.
      if (Date.now() - started > 40_000 && Setup.canJoinFromApp()) {
        try {
          if ((await Setup.findBoxes()).some((b) => b.ssid === ssid)) {
            const h = await Setup.joinHotspot(ssid, false);
            if (h.setup) {
              setBoxHello(h);
              setNets((await Setup.networks()).filter((n) => n.supported));
              setPhase('pick');
              setError(Setup.explain(h.error || 'failed'));
              alreadyJoined.current = true;
              setStep('wifi');
              return;
            }
          }
        } catch {}
      }
    }
    if (alive.current) setStuck(true);
  };

  /** Did this box really take what we sent? */
  const took = async (b: SavedBox): Promise<boolean> => {
    try {
      const { state } = await new Box(b).state();
      return state.name === b.name;
    } catch {
      return false;
    }
  };

  const finish = async () => {
    if (result) await app.adoptBox(result, device.current?.id ?? '');
  };

  const cancelSetup = async () => {
    Setup.leaveHotspot();
    if (device.current) {
      // Do not leave an unused key behind. The phone needs a moment to get its own
      // network back after leaving the box's Wi-Fi, so try a few times.
      const id = device.current.id;
      device.current = null;
      (async () => {
        for (let i = 0; i < 5; i++) {
          await sleep(i ? 4000 : 1500);
          try {
            if ((await app.anve.devices()).every((d: any) => String(d.id) !== id || d.revoked_at)) return;
            await app.anve.revokeDevice(id);
          } catch {}
        }
      })();
    }
    setBoxHello(null);
    go('name');
  };

  // ---------- add a box that is already set up ----------
  useEffect(() => {
    if (step !== 'pair') return;
    let on = true;
    (async () => {
      while (on && finder.discover) {
        const list = (await finder.discover(4000).catch(() => [])).filter((b) => /^\d+\.\d+\.\d+\.\d+$/.test(b.address));
        if (!on) return;
        setNearby(list);
        if (list.length === 1) setAddress((a) => a || list[0].address);
        await sleep(3000);
      }
    })();
    return () => { on = false; };
  }, [step]);

  const doPair = async () => {
    setBusy(true);
    setError('');
    try {
      const box = await pair(address, code, Platform.OS === 'ios' ? 'iPhone' : 'Android phone');
      await app.adoptBox(box);
    } catch (e) {
      const c = e instanceof BoxError ? e.code : '';
      setError(c === 'unreachable' || c === 'not_a_box' ? 'Can’t find your box. Join the same Wi-Fi as it.'
        : c === 'pairing_closed' ? 'That code has expired. Get a new one from your box.'
        : c === 'wrong_code' ? 'Wrong code. Try again.' : c === 'in_setup' ? 'This box isn’t set up yet. Go back and tap Get started.'
        : 'That didn’t work. Try again.');
    } finally {
      setBusy(false);
    }
  };

  const openWifiSettings = () =>
    (Platform.OS === 'android' ? Linking.sendIntent('android.settings.WIFI_SETTINGS') : Linking.openURL('App-Prefs:WIFI')).catch(() => Linking.openSettings());

  backRef.current = () => {
    if (step === 'signin' || step === 'find' || step === 'pair') go('welcome');
    else if (step === 'name') go('find');
    else if (step === 'wifi') cancelSetup();
    else return false; // welcome, waiting, done
    return true;
  };

  // ================= screens =================

  if (step === 'welcome') {
    return (
      <Screen>
        <Appear id="welcome">
          <Hero top={<Orb state="ready" size={220} />} title="Meet your ANVE box" text="Let’s get it set up." />
          <Button title="Set up my box" onPress={() => go(app.signedIn ? 'find' : 'signin')} />
          <Button title="I already set mine up" kind="plain" style={{ marginTop: 4 }} onPress={() => go('pair')} />
        </Appear>
      </Screen>
    );
  }

  if (step === 'signin') {
    return (
      <Screen>
        <Back onPress={() => go('welcome')} />
        <Steps at={0} total={4} />
        <Appear id="signin">
          <H1>Sign in</H1>
          <P style={{ marginTop: 6, marginBottom: 24 }}>Use your ANVE account.</P>
          <Field label="Username" value={username} onChangeText={setUsername} autoComplete="username" returnKeyType="next" />
          <Field label="Password" value={password} onChangeText={setPassword} secret autoComplete="password" onSubmitEditing={signIn} />
          {showServer ? <Field label="Server" value={server} onChangeText={setServer} keyboardType="url" /> : null}
          <View style={{ flex: 1, minHeight: 16 }} />
          {error ? <Notice kind="bad" text={error} /> : null}
          <Button title="Sign in" onPress={signIn} busy={busy} disabled={!username.trim() || !password} />
          {!showServer ? (
            <Pressable onPress={() => setShowServer(true)} accessibilityRole="button" accessibilityLabel="Change server" style={{ paddingVertical: 14, alignItems: 'center' }}>
              <Small style={{ color: T.faint }}>{server.replace(/^https?:\/\//, '')}</Small>
            </Pressable>
          ) : null}
        </Appear>
      </Screen>
    );
  }

  if (step === 'find') {
    const one = found.length === 1 ? found[0] : null;
    const pick = (s: string) => { setSsid(s); setByHand(false); go('name'); };
    return (
      <Screen>
        <Back onPress={() => go('welcome')} />
        <Steps at={1} total={4} />
        <Appear id={`find-${findState}-${found.length ? 'found' : 'none'}`}>
          {findState === 'permission' ? (
            <>
              <Hero top={<Orb state="away" size={190}><Icon name="location" size={44} color={T.onBox2} /></Orb>} title="Allow location" text="Your phone needs it to find your box." />
              <Button title="Allow" onPress={() => setScanRound((n) => n + 1)} />
              <Button title="Open settings" kind="plain" style={{ marginTop: 4 }} onPress={() => Linking.openSettings()} />
            </>
          ) : findState === 'hand' ? (
            <>
              <Hero top={<Orb state="ready" size={190}><Icon name="wifi" size={44} color={T.onBox} /></Orb>} title="Plug in your box" text="Then tap Continue." />
              <Button title="Continue" onPress={() => { setSsid(''); go('name'); }} />
            </>
          ) : found.length === 0 ? (
            <>
              <Hero top={<Orb state="searching" calm size={190}><Motion name="search" size={104} /></Orb>} title="Looking for your box"
                text={scanNote || (slow ? 'Unplug it, plug it back in, and wait a minute.' : 'Plug it in and stay close.')} />
            </>
          ) : (
            <>
              <Hero top={<Orb state="done" size={190}><Motion name="done" size={104} /></Orb>} title={one ? 'Found your box' : 'Which box?'} text={one ? undefined : 'Pick the one you’re setting up.'}>
                {!one ? (
                  <View style={{ alignSelf: 'stretch', marginTop: 20 }}>
                    {found.map((b) => (
                      <Card key={b.ssid} onPress={() => pick(b.ssid)} label={`Box ${b.ssid}`} style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 10 }}>
                        <Icon name="radio" size={22} color={T.accent} />
                        <Text style={{ flex: 1, marginLeft: 12, fontSize: 17, color: T.ink, fontWeight: '500' }}>{b.ssid.replace('ANVE-', 'Box ')}</Text>
                        <Icon name="chevron-forward" size={18} color={T.faint} />
                      </Card>
                    ))}
                  </View>
                ) : null}
              </Hero>
              {one ? <Button title="Set up this box" onPress={() => pick(one.ssid)} /> : null}
            </>
          )}
        </Appear>
      </Screen>
    );
  }

  if (step === 'name') {
    return (
      <Screen>
        <Back onPress={() => go('find')} />
        <Steps at={2} total={4} />
        <Appear id="name">
          <H1>Name your box</H1>
          <View style={{ height: 22 }} />
          <Field label="Name" value={name} onChangeText={setName} autoCapitalize="words" maxLength={40} placeholder="ANVE Box" />
          <Section>Room</Section>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {ROOMS.map((r) => <Chip key={r} title={r} active={room === r} onPress={() => setRoom(r)} />)}
          </View>
          <View style={{ flex: 1, minHeight: 16 }} />
          <Button title="Next: Wi-Fi" onPress={() => go('wifi')} />
        </Appear>
      </Screen>
    );
  }

  if (step === 'wifi') {
    return (
      <Screen>
        <Back onPress={cancelSetup} />
        <Steps at={3} total={4} />
        <Appear id={`wifi-${phase}`}>
          {phase === 'hand' ? (
            <>
              <Hero top={<Orb state="ready" size={190}><Icon name="wifi" size={44} color={T.onBox} /></Orb>} title="Join your box’s Wi-Fi"
                text="In Wi-Fi settings, pick the one starting with ANVE-. Then come back." />
              <Button title="Open Wi-Fi settings" kind="quiet" onPress={openWifiSettings} />
              <Button title="I’ve joined" style={{ marginTop: 10 }} onPress={() => connectToBox(true)} />
            </>
          ) : phase === 'joining' ? (
            error ? (
              <>
                <Hero top={<Orb state="trouble" size={190} />} title="Couldn’t connect" text={error} />
                <Button title="Try again" onPress={() => connectToBox(byHand && !!device.current)} />
              </>
            ) : (
              <Hero top={<Orb state="thinking" calm size={190}><Motion name="wifi" size={104} /></Orb>} title="Connecting" text={byHand ? undefined : 'Tap Connect when your phone asks.'} />
            )
          ) : (
            <>
              <H1>Choose your Wi-Fi</H1>
              <View style={{ height: 18 }} />
              {boxHelloInfo?.trial ? <Notice kind="info" icon="flask" text="Test mode. Nothing will change." /> : null}
              <Card style={{ paddingVertical: 4, paddingHorizontal: 16 }}>
                {nets.map((n, i) => {
                  const on = !other && wifi === n.ssid;
                  return (
                    <Pressable key={n.ssid} onPress={() => { setOther(false); setWifi(n.ssid); setWifiPass(''); setPortal(false); }}
                      accessibilityRole="radio" accessibilityState={{ selected: on }} accessibilityLabel={n.secure ? `${n.ssid}, needs a password` : n.ssid}
                      style={{ flexDirection: 'row', alignItems: 'center', minHeight: 56, borderTopWidth: i ? 1 : 0, borderTopColor: T.line }}>
                      <Icon name={n.signal > 55 ? 'wifi' : 'wifi-outline'} size={22} color={on ? T.accent : T.sub} style={{ marginRight: 14 }} />
                      <Text style={{ flex: 1, fontSize: 17, fontWeight: on ? '600' : '400', color: T.ink }} numberOfLines={1}>{n.ssid}</Text>
                      {n.secure ? <Icon name="lock-closed" size={15} color={T.faint} style={{ marginRight: 10 }} /> : null}
                      {on ? <Icon name="checkmark" size={22} color={T.accent} /> : null}
                    </Pressable>
                  );
                })}
                <Pressable onPress={() => { setOther(true); setWifi(''); setWifiPass(''); }} accessibilityRole="radio" accessibilityState={{ selected: other }}
                  style={{ flexDirection: 'row', alignItems: 'center', minHeight: 56, borderTopWidth: nets.length ? 1 : 0, borderTopColor: T.line }}>
                  <Icon name="add" size={22} color={other ? T.accent : T.sub} style={{ marginRight: 14 }} />
                  <Text style={{ flex: 1, fontSize: 17, fontWeight: other ? '600' : '400', color: T.ink }}>Other…</Text>
                  {other ? <Icon name="checkmark" size={22} color={T.accent} /> : null}
                </Pressable>
              </Card>
              {other ? <Field label="Wi-Fi name" value={wifi} onChangeText={setWifi} maxLength={32} /> : null}
              {other || chosen?.secure ? <Field label="Password" value={wifiPass} onChangeText={setWifiPass} secret /> : null}
              {/* only a Wi-Fi without a password can have a sign-in page (college, hotel) */}
              {wifi && !needsPass ? (
                <Card style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 10 }}>
                  <Text style={{ flex: 1, fontSize: 16, color: T.ink }}>Needs a sign-in page</Text>
                  <Switch value={portal} onValueChange={setPortal} trackColor={{ true: T.accent, false: T.fill }} thumbColor="#FFFFFF" accessibilityLabel="This Wi-Fi needs a sign-in page" />
                </Card>
              ) : null}
              {portal && wifi && !needsPass ? (
                <>
                  <Field label="Sign-in name" value={portalUser} onChangeText={setPortalUser} />
                  <Field label="Sign-in password" value={portalPass} onChangeText={setPortalPass} secret />
                </>
              ) : null}
              <View style={{ flex: 1, minHeight: 16 }} />
              {error ? <Notice kind={error.startsWith('Test passed') ? 'good' : 'bad'} text={error} /> : null}
              <Button title="Connect to Wi-Fi" onPress={send} busy={busy} disabled={!canSend} />
            </>
          )}
        </Appear>
      </Screen>
    );
  }

  if (step === 'waiting') {
    return (
      <Screen>
        <Appear id={`waiting-${stuck}`}>
          {stuck ? (
            <>
              <Hero top={<Orb state="away" size={190} />} title="Can’t find your box" text="Make sure your phone is on the same Wi-Fi." />
              <Button title="Try again" onPress={() => { setStuck(false); result && waitForBox(result); }} />
              <Button title="Continue anyway" kind="plain" style={{ marginTop: 4 }} onPress={() => setStep('done')} />
            </>
          ) : (
            <Hero top={<Orb state="thinking" calm size={190}><Motion name="wifi" size={104} /></Orb>} title="Almost there" text={`${boxName} is joining Wi-Fi.`} />
          )}
        </Appear>
      </Screen>
    );
  }

  if (step === 'done') {
    return (
      <Screen>
        <Appear id="done">
          <Hero
            top={<Orb state="done" size={190}><Motion name="done" size={104} /></Orb>}
            title="You’re all set" text="Just say “Hey ANVE”." />
          <Button title="Open home" onPress={finish} />
        </Appear>
      </Screen>
    );
  }

  // step === 'pair'
  const showAddress = typeAddress || (nearby.length === 0 && !finder.discover);
  return (
    <Screen>
      <Back onPress={() => go('welcome')} />
      <Appear id="pair">
        <H1>Add your box</H1>
        <P style={{ marginTop: 6, marginBottom: 22 }}>Enter the code from your box.</P>
        {nearby.length > 1 ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 6 }}>
            {nearby.map((b) => <Chip key={b.address} title={`Box ${b.id.toUpperCase()}`} icon="radio" active={address === b.address} onPress={() => setAddress(b.address)} />)}
          </View>
        ) : null}
        <Field mono label="Code" value={code} onChangeText={(t) => setCode(t.replace(/\D/g, '').slice(0, 6))} placeholder="6 digits" keyboardType="number-pad" maxLength={6} />
        {showAddress ? <Field label="Box address" value={address} onChangeText={setAddress} placeholder="192.168.1.20" keyboardType="url" /> : null}
        {!showAddress && nearby.length === 0 ? <Small style={{ marginLeft: 4 }}>Looking for your box on this Wi-Fi…</Small> : null}
        <View style={{ flex: 1, minHeight: 16 }} />
        {error ? <Notice kind="bad" text={error} /> : null}
        <Button title="Add box" onPress={doPair} busy={busy} disabled={!address.trim() || code.length !== 6} />
        {!showAddress ? <Button title="Enter address instead" kind="plain" style={{ marginTop: 4 }} onPress={() => setTypeAddress(true)} /> : null}
      </Appear>
    </Screen>
  );
}
