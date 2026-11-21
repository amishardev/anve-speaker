// The LUMINA dashboard: one WebSocket carries the live strip, the state and the commands.
(() => {
  const $ = (id) => document.getElementById(id);
  const NAMES = {
    STARTING: 'Starting', SETUP_REQUIRED: 'Setup needed', SETUP_IN_PROGRESS: 'Setting up', CONNECTING: 'Connecting',
    READY: 'Ready', WAKE_WORD_DETECTED: 'Wake word', LISTENING: 'Listening', RECORDING: 'Recording',
    PROCESSING: 'Processing', THINKING: 'Thinking', SEARCHING: 'Searching', TOOL_EXECUTING: 'Running a tool',
    SPEAKING: 'Speaking', MUSIC_PLAYING: 'Music playing', MUSIC_PAUSED: 'Music paused', MUTED: 'Microphone off',
    DISCONNECTED: 'Disconnected', ERROR: 'Error', ALARM: 'Alarm', SHUTTING_DOWN: 'Shutting down', OFF: 'Off',
  };
  const TESTS = ['SETUP_REQUIRED', 'SETUP_IN_PROGRESS', 'CONNECTING', 'READY', 'WAKE_WORD_DETECTED', 'LISTENING', 'PROCESSING',
    'THINKING', 'SEARCHING', 'TOOL_EXECUTING', 'SPEAKING', 'MUSIC_PLAYING', 'MUSIC_PAUSED', 'MUTED', 'DISCONNECTED', 'ERROR'];
  const DIMS = [['connection', 'Connection'], ['setup', 'Setup'], ['microphone', 'Microphone'], ['voice', 'Listening'],
    ['processing', 'Processing'], ['speech', 'Speaking'], ['media', 'Music'], ['fault', 'Fault']];
  const PERCENT = (v) => Math.round(v * 100) + ' %';
  const BRIGHTNESS = ['brightness', 'active_brightness', 'music_brightness'];
  const SLIDERS = [['brightness', 'Brightness at rest', 0, 1, 0.005, PERCENT],
    ['active_brightness', 'Brightness after “Hey ANVE”', 0, 1, 0.005, PERCENT],
    ['music_brightness', 'Brightness with music', 0, 1, 0.005, PERCENT],
    ['speed', 'Animation speed', 0.2, 3, 0.05, (v) => v.toFixed(2) + '×'],
    ['transition_s', 'Transition', 0, 2, 0.05, (v) => v.toFixed(2) + ' s'],
    ['wave_intensity', 'Wave intensity', 0.1, 3, 0.05, (v) => v.toFixed(2)],
    ['listening_sensitivity', 'Listening sensitivity', 0.1, 3, 0.05, (v) => v.toFixed(2)],
    ['speaking_sensitivity', 'Speaking sensitivity', 0.1, 3, 0.05, (v) => v.toFixed(2)],
    ['voice_delay_s', 'Lights wait for the voice', 0, 1.5, 0.05, (v) => v.toFixed(2) + ' s'],
    ['music_delay_s', 'Lights wait for the music', 0, 1.5, 0.05, (v) => v.toFixed(2) + ' s'],
    ['active', 'LEDs used', 1, 60, 1, (v) => String(Math.round(v))]];
  const ROLES = [['ready', 'Ready'], ['wake', 'Wake word'], ['listen_high', 'Listening'], ['think_b', 'Thinking'],
    ['search', 'Searching'], ['tool', 'Tool'], ['speak_high', 'Speaking'], ['setup', 'Setup needed'], ['connect', 'Connecting'],
    ['muted', 'Microphone off'], ['error', 'Error'], ['disconnected', 'Disconnected']];

  let ws = null, key = '', status = null, px = '', busy = {}, opened = false, tried = false, retry = 1500;
  try { key = localStorage.getItem('lumina.key') || ''; } catch (e) { /* private window */ }

  const send = (m) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };
  const setting = (patch) => send(Object.assign({ type: 'settings' }, patch));

  // ---------- the strip ----------
  const canvas = $('strip'), g = canvas.getContext('2d');
  function draw() {
    const n = px.length / 6, w = canvas.width, h = canvas.height;
    g.clearRect(0, 0, w, h);
    if (!n) return;
    const step = w / n, r = Math.min(step * 0.36, h * 0.3);
    for (let i = 0; i < n; i++) {
      const x = step * (i + 0.5);
      // an LED is a light, not a patch of paint: even a low value looks bright. The curve
      // makes the picture on a screen closer to what the strip looks like in a room.
      const v = [0, 2, 4].map((o) => Math.round(255 * Math.pow(parseInt(px.substr(i * 6 + o, 2), 16) / 255, 0.55)));
      const c = 'rgb(' + v.join(',') + ')';
      if (v[0] + v[1] + v[2] < 12) { g.shadowBlur = 0; g.fillStyle = '#17171a'; }
      else { g.shadowColor = c; g.shadowBlur = 16; g.fillStyle = c; }
      g.beginPath(); g.arc(x, h / 2, r, 0, 7); g.fill();
    }
    g.shadowBlur = 0;
  }

  // ---------- building the controls once ----------
  function el(tag, props, kids) {
    const e = Object.assign(document.createElement(tag), props || {});
    (kids || []).forEach((k) => e.append(k));
    return e;
  }
  function build() {
    TESTS.forEach((s) => $('tests').append(el('button', { textContent: NAMES[s], id: 't-' + s,
      onclick: () => send({ type: 'demo', state: s, seconds: 20 }) })));
    SLIDERS.forEach(([k, label, min, max, step, fmt]) => {
      const out = el('output', { id: 'o-' + k });
      const input = el('input', { type: 'range', id: 's-' + k, min, max, step });
      input.setAttribute('aria-label', label);
      input.oninput = () => { busy[k] = Date.now(); out.textContent = fmt(+input.value); setting({ [k]: +input.value }); };
      $('sliders').append(el('div', { className: 'row' }, [el('label', { textContent: label, htmlFor: 's-' + k }), input, out]));
    });
    [['theme', 'Theme', {}], ['music_mode', 'Music', { smooth: 'Smooth', festival: 'Festival', ambient: 'Ambient', rainbow: 'Rainbow' }],
      ['direction', 'Direction', { forward: 'Forward', reverse: 'Reverse' }]].forEach(([k, label, opts]) => {
      const sel = el('select', { id: 'c-' + k, onchange: () => setting({ [k]: sel.value }) });
      sel.setAttribute('aria-label', label);
      Object.entries(opts).forEach(([v, t]) => sel.append(el('option', { value: v, textContent: t })));
      $('choices').append(el('div', { className: 'row' }, [el('label', { textContent: label, htmlFor: 'c-' + k }), sel]));
    });
    ROLES.forEach(([role, label]) => {
      const input = el('input', { type: 'color', id: 'k-' + role });
      input.setAttribute('aria-label', label + ' colour');
      input.onchange = () => setting({ colours: { [role]: input.value } });
      $('colours').append(el('div', { className: 'colour' }, [el('label', { textContent: label, htmlFor: 'k-' + role }), input,
        el('button', { textContent: 'Reset', onclick: () => setting({ colours: { [role]: '' } }) })]));
    });
    $('demoOff').onclick = () => send({ type: 'demo', off: true });
    $('gateForm').onsubmit = (e) => {
      e.preventDefault();
      key = $('key').value.trim(); tried = true;
      try { localStorage.setItem('lumina.key', key); } catch (err) { /* fine */ }
      send({ type: 'auth', key });
    };
  }

  // ---------- showing the state ----------
  function show(s) {
    status = s;
    $('gate').hidden = true;
    $('visual').textContent = NAMES[s.visual] || s.visual;
    $('detail').textContent = (s.animation && s.animation !== 'default' ? s.animation + ' · ' : '') +
      (s.visual === 'SETUP_IN_PROGRESS' ? (s.progress == null ? 'no percentage reported' : Math.round(s.progress * 100) + ' %') :
        s.error && s.visual === 'ERROR' ? [s.error.code, s.error.message].filter(Boolean).join(': ') :
          s.alive ? 'reported by the assistant' : 'the assistant is not reporting');
    const real = s.strip.driver === 'spi';
    $('output').textContent = (real ? 'LEDs on' : 'Preview only: LEDs off') + '\n' + s.power_ma + ' mA' + (s.limited ? ' (limited)' : '');
    $('demo').hidden = !s.demo;
    if (s.demo) $('demoText').textContent = 'Showing “' + (NAMES[s.visual] || s.visual) + '” for ' + Math.ceil(s.demo_left) +
      ' s. The assistant is really: ' + (NAMES[s.real] || s.real) + '.';
    const trouble = [s.strip.problem, s.levels.loopback].filter(Boolean).join(' · ');
    $('trouble').hidden = !trouble; $('trouble').textContent = trouble;
    TESTS.forEach((t) => $('t-' + t).classList.toggle('on', s.demo && s.visual === t));

    const dims = $('dims'); dims.textContent = '';
    DIMS.forEach(([k, label]) => {
      const v = s.dims[k];
      const hot = !['idle', 'none', 'stopped', 'enabled', 'connected', 'complete', 'off', 'on'].includes(v);
      const bad = ['muted', 'disconnected', 'error', 'hardware', 'required'].includes(v);
      dims.append(el('div', { className: 'dim' + (bad ? ' bad' : hot ? ' hot' : '') }, [el('small', { textContent: label }),
        el('b', { textContent: String(v).replace('_', ' ') })]));
    });

    const set = s.settings, now = Date.now();
    SLIDERS.forEach(([k, , , , , fmt]) => {
      const input = $('s-' + k), v = k === 'active' ? s.strip.active : set[k];
      if (BRIGHTNESS.includes(k)) input.max = set.max_brightness;
      if (k === 'active') input.max = s.strip.count;
      if (now - (busy[k] || 0) > 1200) { input.value = v; $('o-' + k).textContent = fmt(+v); }
    });
    const theme = $('c-theme');
    if (!theme.options.length) Object.entries(s.themes).forEach(([v, t]) => theme.append(el('option', { value: v, textContent: t })));
    theme.value = set.theme; $('c-music_mode').value = set.music_mode; $('c-direction').value = set.direction;

    const vr = $('variants');
    if (!vr.children.length) Object.entries(s.variants).forEach(([state, names]) => {
      const sel = el('select', { id: 'v-' + state, onchange: () => setting({ animations: { [state]: sel.value } }) });
      sel.setAttribute('aria-label', (NAMES[state] || state) + ' animation');
      names.forEach((nm) => sel.append(el('option', { value: nm, textContent: nm })));
      vr.append(el('div', { className: 'row' }, [el('label', { textContent: NAMES[state] || state, htmlFor: 'v-' + state }), sel]));
    });
    Object.entries(s.variants).forEach(([state, names]) => { $('v-' + state).value = set.animations[state] || names[0]; });
    ROLES.forEach(([role]) => { const c = set.colours[role] || s.theme_colours[role]; if (c && document.activeElement !== $('k-' + role)) $('k-' + role).value = c; });

    const log = $('log'); log.textContent = '';
    s.log.slice().reverse().forEach((r) => {
      const t = new Date(r.time * 1000).toLocaleTimeString([], { hour12: false });
      const li = el('li', {}, [el('time', { textContent: t }), el('span', { textContent: r.what + '  ← ' + r.source })]);
      if (r.remark) li.append(el('em', { textContent: r.remark }));
      log.append(li);
    });
  }

  // ---------- the connection ----------
  function connect() {
    opened = false;
    ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    ws.onopen = () => {
      if (key) send({ type: 'auth', key });
      setTimeout(() => { if (!opened && ws.readyState === 1) { $('gate').hidden = false; $('key').focus(); } }, 900);
    };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.t === 'f') { px = m.px; draw(); }
      else if (m.t === 's') { opened = true; retry = 1500; $('link').textContent = 'Live'; $('link').className = 'pill on'; show(m); }
      else if (m.t === 'auth' && !m.ok) { $('gate').hidden = false; $('gateNote').hidden = !tried; }
    };
    // try again, a little more patiently each time (browsers slow down a page that hammers)
    ws.onclose = () => { $('link').textContent = 'Reconnecting'; $('link').className = 'pill off'; setTimeout(connect, retry); retry = Math.min(retry * 1.6, 12000); };
  }

  build();
  connect();
})();
