// The app's own box client (src/lib/box.ts + crypto.ts) against a REAL box, from this
// computer:   npx tsx scripts/box_live_test.ts <address> <6-digit pairing code>
// Get a code on the box with  ~/anve-device/tools/pair_app.py . Changes nothing lasting
// (the volume is set to what it already is). The token is never printed.
import { Box, pair } from '../src/lib/box';

const [address, code] = process.argv.slice(2);
let pass = 0, fail = 0;
const check = (name: string, ok: boolean, note = '') => {
  ok ? pass++ : fail++;
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name}${!ok && note ? ' - ' + note : ''}`);
};

(async () => {
  let wrong = '';
  try {
    await pair(address, code === '000000' ? '111111' : '000000', 'test');
  } catch (e: any) {
    wrong = e.code;
  }
  check('a wrong pairing code is refused', wrong === 'wrong_code', wrong);
  const saved = await pair(address, code, 'laptop test');
  check('pairing with the right code returns a 32-byte token (sealed)', /^[0-9a-f]{64}$/.test(saved.token));
  const box = new Box(saved);
  const { state, seq } = await box.state();
  check('signed + encrypted state', typeof state.volume === 'number' && state.id === saved.id, JSON.stringify(state));
  console.log(`       ${state.name} (${state.id}): ${state.state}, online ${state.online}, volume ${state.volume} %, ` +
    `speaker ${state.speaker}, mic ${state.mic}, playing ${state.playing ? state.playing.title : 'nothing'}`);
  const v = await box.volume(state.volume);
  check('volume request with an encrypted body', v.volume === state.volume, JSON.stringify(v));
  const t0 = Date.now();
  const ev = await box.events(seq, 2);
  check('events long poll', ev.seq >= seq && Date.now() - t0 < 6000, JSON.stringify(ev).slice(0, 120));
  const info = await box.info();
  check('info', !!info.address && typeof info.temperature === 'number', JSON.stringify(info));
  console.log(`       Wi-Fi "${info.wifi}", ${info.address}, ${info.temperature} C, power ok ${info.power_ok}`);
  const byName = new Box({ ...saved, address: undefined });
  let named = '';
  try {
    named = (await byName.state()).state.id;
  } catch (e: any) {
    named = 'error ' + e.code;
  }
  check(`the box is found by its network name (${saved.host})`, named === saved.id, named);
  console.log(`\n${pass}/${pass + fail} passed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.error('test crashed:', e?.code || e?.message || e);
  process.exit(2);
});
