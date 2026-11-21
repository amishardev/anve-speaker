// Home: the box, what it is doing, what is playing, and its volume.
//
// The box is drawn as the appliance it is: a dark panel with its ring of lights, in ANVE's
// "ink". Everything else on the page is quiet content around it.
import React, { useEffect, useState } from 'react';
import { Image, Pressable, Switch, TextInput, View } from 'react-native';
import { BoxState } from '../lib/box';
import { Motion } from '../motion';
import { useApp } from '../state';
import { Button, Card, FONTS, H1, H2, Icon, Notice, Orb, Presence, RoundButton, Screen, Section, Slider, Small, T, Text } from '../ui';

function greeting(): string {
  const h = new Date().getHours();
  return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

/** The box's state as one word for the light ring and a few words for people. */
function presence(s: BoxState | null, reachable: boolean | null): { ring: Presence; text: string; trouble?: boolean } {
  if (reachable === null) return { ring: 'away', text: 'Looking for your box…' };
  if (!reachable || !s) return { ring: 'away', text: 'Not on this Wi-Fi' };
  if (!s.online) return { ring: 'trouble', text: s.halted ? 'Needs setting up again' : 'No internet', trouble: true };
  if (!s.mic) return { ring: 'trouble', text: 'Microphone unplugged', trouble: true };
  if (!s.speaker) return { ring: 'trouble', text: 'Speaker is off', trouble: true };
  if (s.muted || s.state === 'muted') return { ring: 'muted', text: 'Microphone off' };
  if (s.state === 'listening') return { ring: 'listening', text: 'Listening' };
  if (s.state === 'thinking') return { ring: 'thinking', text: 'Thinking' };
  if (s.state === 'speaking') return { ring: 'speaking', text: 'Speaking' };
  if (s.state === 'recording') return { ring: 'listening', text: 'Recording a meeting' };
  if (s.state === 'alarm') return { ring: 'speaking', text: 'Ringing' };
  if (s.state === 'error') return { ring: 'trouble', text: 'Something is wrong', trouble: true };
  return { ring: 'ready', text: 'Ready' };
}

const clock = (n = 0) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;

export default function Home({ openApprovals }: { openApprovals: () => void }) {
  const { saved, box, boxState: s, reachable, setBoxState, pending } = useApp();
  const [vol, setVol] = useState<number | null>(null); // while the finger is on the slider
  const [query, setQuery] = useState('');
  const [note, setNote] = useState('');
  const ok = !!box && reachable === true && !!s;
  const now = presence(s, reachable);
  const volume = vol ?? s?.volume ?? 0;
  const first = (saved.user?.name || '').split(' ')[0];
  const talking = !!s && (s.state === 'speaking' || s.state === 'alarm');

  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(''), 4000);
    return () => clearTimeout(t);
  }, [note]);

  const run = async (f: () => Promise<any>, optimistic?: Partial<BoxState>) => {
    if (!box || !s) return;
    if (optimistic) setBoxState({ ...s, ...optimistic });
    try {
      const r = await f();
      if (r && r.ok === false && r.note) setNote(r.note);
    } catch {
      setNote('Your box didn’t answer.');
    }
  };

  // The box is told once, when the finger lifts (it plays a soft blip at the new volume);
  // while dragging only the number on screen moves.
  const setVolume = (v: number, final: boolean) => {
    const level = Math.max(0, Math.min(100, Math.round(v)));
    setVol(final ? null : level);
    if (final) run(() => box!.volume(level), { volume: level });
  };

  const play = () => {
    const q = query.trim();
    if (!q || !ok) return;
    run(() => box!.play(q));
    setNote(`Playing “${q}”…`);
    setQuery('');
  };

  const p = s?.playing ?? null;
  const room = s?.room || saved.box?.room;

  return (
    <Screen bottom={104}>
      <H1 style={{ marginBottom: 18 }}>{first ? `${greeting()}, ${first}` : greeting()}</H1>

      {pending.length ? (
        <Card onPress={openApprovals} label={`${pending.length} waiting for your approval. Open approvals.`} style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: T.tint }}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 16.5, fontWeight: '600', color: T.accent }}>{pending.length === 1 ? 'One thing needs your OK' : `${pending.length} things need your OK`}</Text>
          </View>
          <Icon name="arrow-forward" size={20} color={T.accent} />
        </Card>
      ) : null}

      {/* the box */}
      <View style={{ backgroundColor: T.box, borderRadius: 20, padding: 20, marginBottom: 12, borderWidth: T.dark ? 1 : 0, borderColor: T.boxLine }}>
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Orb state={now.ring} size={92} />
          <View style={{ flex: 1, marginLeft: 18 }}>
            <Text numberOfLines={1} maxFontSizeMultiplier={1.4} style={{ color: T.onBox, fontSize: 24, lineHeight: 30, fontWeight: '600', letterSpacing: -0.4 }}>{s?.name || saved.box?.name}</Text>
            <Text accessibilityLiveRegion="polite" style={{ color: now.trouble ? '#FF8A80' : T.onBox2, fontSize: 16, lineHeight: 22, marginTop: 3 }}>
              {[room, now.text].filter(Boolean).join(' · ')}
            </Text>
          </View>
        </View>
        <View style={{ height: 1, backgroundColor: T.boxLine, marginTop: 18, marginBottom: 10 }} />
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Text style={{ color: T.onBox2, fontSize: 14, flex: 1 }}>Volume</Text>
          <Text mono style={{ color: T.onBox, fontSize: 15 }}>{ok ? `${volume}%` : '—'}</Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 4 }}>
          <RoundButton icon="remove" kind="onBox" size={44} label="Volume down" disabled={!ok} onPress={() => setVolume(volume - 10, true)} />
          <Slider onBox value={volume} disabled={!ok} onChange={(v) => setVolume(v, false)} onDone={(v) => setVolume(v, true)} />
          <RoundButton icon="add" kind="onBox" size={44} label="Volume up" disabled={!ok} onPress={() => setVolume(volume + 10, true)} />
        </View>
      </View>

      {talking ? <Button title="Stop talking" kind="quiet" style={{ marginBottom: 12 }} onPress={() => run(() => box!.stop())} /> : null}
      {note ? <Notice text={note} /> : null}
      {reachable === false ? <Notice icon="wifi" text="Join the same Wi-Fi as your box to control it." /> : null}

      <Section>{p ? 'Now playing' : 'Music'}</Section>
      {p ? (
        <Card>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            {p.art ? <Image source={{ uri: p.art }} accessibilityIgnoresInvertColors style={{ width: 64, height: 64, borderRadius: 12, backgroundColor: T.fill }} /> : (
              <View style={{ width: 64, height: 64, borderRadius: 12, backgroundColor: T.fill, alignItems: 'center', justifyContent: 'center' }}>
                <Icon name={p.source === 'radio' ? 'radio' : 'musical-notes'} size={28} color={T.sub} />
              </View>
            )}
            <View style={{ flex: 1, marginLeft: 14 }}>
              <H2 numberOfLines={2}>{p.title}</H2>
              <Small style={{ marginTop: 2 }}>{p.source === 'radio' ? 'Radio' : p.source === 'youtube' ? 'YouTube' : 'Browser'}{p.paused ? ' · paused' : ''}</Small>
            </View>
            <Motion name="playing" tone="accent" size={30} paused={!!p.paused} />
          </View>
          {p.duration ? (
            <View style={{ marginTop: 14 }}>
              <View style={{ height: 3, borderRadius: 2, backgroundColor: T.fill, overflow: 'hidden' }}>
                <View style={{ width: `${Math.min(100, ((p.position ?? 0) / p.duration) * 100)}%`, height: 3, backgroundColor: T.ink }} />
              </View>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 }}>
                <Text mono style={{ color: T.sub, fontSize: 12.5 }}>{clock(p.position)}</Text>
                <Text mono style={{ color: T.sub, fontSize: 12.5 }}>{clock(p.duration)}</Text>
              </View>
            </View>
          ) : null}
          <View style={{ flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 26, marginTop: 10 }}>
            <RoundButton icon="stop" label="Stop" disabled={!ok} onPress={() => run(() => box!.media('pause'), { playing: { ...p, paused: true } })} />
            <RoundButton icon={p.paused ? 'play' : 'pause'} size={60} kind="primary" label={p.paused ? 'Play' : 'Pause'} disabled={!ok || (p.source === 'radio' && p.paused)}
              onPress={() => run(() => box!.media(p.paused ? 'resume' : 'pause'), { playing: { ...p, paused: !p.paused } })} />
            <RoundButton icon="play-skip-forward" label="Next" disabled={!ok || p.source !== 'youtube'} onPress={() => run(() => box!.media('next'))} />
          </View>
        </Card>
      ) : null}

      {/* ask for a song: one field, no card around it */}
      <View style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: T.card, borderRadius: 12, paddingLeft: 14, minHeight: 54, marginBottom: 12, opacity: ok ? 1 : 0.5 }}>
        <Icon name="search" size={19} color={T.sub} />
        <TextInput value={query} onChangeText={setQuery} placeholder={p ? 'Play something else' : 'Play a song or artist'} placeholderTextColor={T.faint} returnKeyType="go" editable={ok}
          accessibilityLabel="Play a song or artist" onSubmitEditing={play}
          style={{ flex: 1, marginLeft: 10, fontSize: 16.5, color: T.ink, paddingVertical: 12, fontFamily: FONTS.regular }} />
        {query.trim() ? (
          <Pressable disabled={!ok} onPress={play} accessibilityRole="button" accessibilityLabel="Play it" hitSlop={6}
            style={{ width: 40, height: 40, borderRadius: 10, margin: 7, backgroundColor: T.primary, alignItems: 'center', justifyContent: 'center' }}>
            <Icon name="arrow-up" size={20} color={T.onPrimary} />
          </Pressable>
        ) : null}
      </View>

      <Card style={{ paddingVertical: 4 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', minHeight: 54, borderBottomWidth: 1, borderBottomColor: T.line }}>
          <Text style={{ flex: 1, fontSize: 16.5, color: T.ink }}>Microphone</Text>
          <Switch value={!!s && !s.muted} disabled={!ok} onValueChange={(on) => run(() => box!.mute(!on), { muted: !on })}
            trackColor={{ true: T.accent, false: T.fill }} thumbColor="#FFFFFF" accessibilityLabel="Microphone" />
        </View>
        <Pressable disabled={!ok} onPress={() => run(() => box!.radio())} accessibilityRole="button"
          style={({ pressed }) => [{ flexDirection: 'row', alignItems: 'center', minHeight: 54, opacity: ok ? 1 : 0.5 }, pressed && { opacity: 0.6 }]}>
          <Text style={{ flex: 1, fontSize: 16.5, color: T.ink }}>Play radio</Text>
          <Icon name="play" size={17} color={T.accent} />
        </Pressable>
      </Card>
    </Screen>
  );
}
