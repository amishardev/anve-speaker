"""A control page for the speaker, in a browser: what is playing, pause / play / next,
volume, mute, play a song or a radio station, stop talking.

It listens on the Pi's own loopback address ONLY (127.0.0.1, port 8739), never on the
network: on a shared Wi-Fi anyone could otherwise change the volume or play things. To use
it from a computer, forward the port over SSH (the SSH key is the lock):

    ssh -N -L 8739:127.0.0.1:8739 <user>@<the Pi>
    then open http://127.0.0.1:8739/

It uses the same handlers as the phone app's API (appapi.py), without the app's sealed
channel, which is why it must stay on loopback. Not offered here on purpose: renaming the
box and the factory reset.

Two guards against a web page in the same browser poking at it: the Host header must be a
loopback name, and every change must carry the header `X-Anve-Panel: 1` (a plain form on
another site cannot send that)."""
import logging

from .httpd import HttpError, Raw, Server
from .skills import RADIO

log = logging.getLogger("anve.panel")
HOSTS = ("127.0.0.1", "localhost", "[::1]")


class Panel:
    def __init__(self, api, port: int = 8739):
        self.api, self.port = api, port
        self.http = Server()
        r, g, p = self.http.route, self._get, self._post
        r("GET", "/", g(self.h_page))
        r("GET", "/api/state", g(self.h_state))
        r("POST", "/api/volume", p(api.h_volume))
        r("POST", "/api/media", p(api.h_media))
        r("POST", "/api/play", p(api.h_play))
        r("POST", "/api/mute", p(api.h_mute))
        r("POST", "/api/stop", p(api.h_stop))
        r("POST", "/api/meeting", p(self.h_meeting))

    @staticmethod
    def _local(q):
        host = q.headers.get("host", "").rsplit(":", 1)[0].lower()
        if q.peer not in ("127.0.0.1", "::1") or host not in HOSTS:
            raise HttpError(403, "local_only")

    def _get(self, handler):
        async def h(q):
            self._local(q)
            return await handler(q)
        return h

    def _post(self, handler):
        async def h(q):
            self._local(q)
            if q.headers.get("x-anve-panel") != "1":
                raise HttpError(403, "header_needed")
            return await handler(q.json(), q)
        return h

    async def h_page(self, q):
        return Raw(200, PAGE.encode(), "text/html; charset=utf-8")

    async def h_state(self, q):
        return {"ok": True, "state": self.api.snapshot(),
                "radio": {k: v[0] for k, v in RADIO.items()}}

    async def h_meeting(self, body, q):
        """Record a meeting in the room: {"do": "start" | "mark" | "stop"}. The same three
        things the button and the voice do (main.Device.meeting_*)."""
        d = self.api.dev
        do = str(body.get("do") or "")
        if do == "start":
            why = await d.meeting_start(None, "from the control page")
        elif do == "mark":
            why = await d.meeting_mark()
        elif do == "stop":
            why = await d.meeting_stop("from the control page")
        else:
            raise HttpError(400, "do_start_mark_or_stop")
        return {"ok": not why, "note": why, "recording": bool(d.meeting)}

    async def start(self):
        try:
            await self.http.start("127.0.0.1", self.port)
            log.info("control page on http://127.0.0.1:%s/ (this device only)", self.port)
        except OSError as e:
            log.warning("the control page cannot listen on port %s (%s)", self.port, e)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ANVE Speaker</title>
<style>
:root{--bg:#f4f6f8;--card:#fff;--ink:#16202a;--mute:#5d6b78;--line:#d9e0e6;--acc:#0b6e99;--acc-ink:#fff;--bad:#b3261e;--ok:#1b7f4b}
@media (prefers-color-scheme:dark){:root{--bg:#10151a;--card:#1a222a;--ink:#e8eef3;--mute:#9aa8b5;--line:#2c3742;--acc:#4db4e0;--acc-ink:#06202c;--bad:#ff8a80;--ok:#6fd39b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:20px;margin:0}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
.pill{font-size:13px;padding:3px 10px;border-radius:999px;border:1px solid var(--line);color:var(--mute);white-space:nowrap}
.pill.ok{color:var(--ok);border-color:var(--ok)}.pill.bad{color:var(--bad);border-color:var(--bad)}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:0 0 10px}
.now{display:flex;gap:14px;align-items:center}
.now img{width:112px;height:63px;object-fit:cover;border-radius:8px;background:var(--line);flex:none}
.title{font-weight:600;overflow-wrap:anywhere}
.sub{color:var(--mute);font-size:14px}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
button{font:inherit;padding:10px 16px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;min-height:44px}
button.main{background:var(--acc);color:var(--acc-ink);border-color:var(--acc);font-weight:600}
button:disabled{opacity:.45;cursor:default}
button:focus-visible,input:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
input[type=range]{width:100%;accent-color:var(--acc);height:32px}
input[type=text]{font:inherit;flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink);min-height:44px}
.vol{display:flex;align-items:center;gap:12px}
.vol output{font-variant-numeric:tabular-nums;font-weight:600;min-width:3.2em;text-align:right}
progress{width:100%;height:6px;margin-top:10px;accent-color:var(--acc)}
#msg{min-height:1.4em;font-size:14px;color:var(--mute)}
#msg.bad{color:var(--bad)}
</style></head><body><main>
<header><h1 id="name">ANVE Speaker</h1><span class="pill" id="link">connecting</span></header>

<section aria-labelledby="h-now"><h2 id="h-now">Now playing</h2>
 <div class="now"><img id="art" alt="" hidden><div><div class="title" id="title">Nothing is playing</div><div class="sub" id="sub"></div></div></div>
 <progress id="prog" max="1" value="0" hidden></progress>
 <div class="row">
  <button class="main" id="toggle" disabled>Pause</button>
  <button id="next" disabled>Next</button>
  <button id="quiet">Stop talking</button>
 </div>
</section>

<section aria-labelledby="h-vol"><h2 id="h-vol">Volume</h2>
 <div class="vol"><input type="range" id="vol" min="0" max="100" step="5" aria-labelledby="h-vol"><output id="volv">-</output></div>
 <div class="row"><button id="mute">Mute microphone</button></div>
</section>

<section aria-labelledby="h-play"><h2 id="h-play">Play something</h2>
 <form class="row" id="playf" style="margin-top:0"><input type="text" id="query" maxlength="120" placeholder="Song and artist" aria-label="Song and artist"><button class="main">Play</button></form>
 <div class="row" id="radio"></div>
</section>

<section aria-labelledby="h-meet"><h2 id="h-meet">Meeting</h2>
 <div class="sub" id="meet">Not recording</div>
 <div class="row"><button class="main" id="mstart">Record a meeting</button><button id="mmark" disabled>Mark this moment</button><button id="mstop" disabled>Stop recording</button></div>
</section>

<section aria-labelledby="h-st"><h2 id="h-st">Speaker</h2><div class="sub" id="status">-</div><div id="msg" role="status"></div></section>
</main>
<script>
const $ = id => document.getElementById(id);
let st = null, dragging = false, radioDone = false;
const say = (t, bad) => { $("msg").textContent = t || ""; $("msg").className = bad ? "bad" : ""; };
const mmss = s => Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
async function call(path, body) {
  try {
    const r = await fetch(path, body === undefined ? {} : {method: "POST",
      headers: {"Content-Type": "application/json", "X-Anve-Panel": "1"}, body: JSON.stringify(body)});
    const j = await r.json();
    if (!j.ok) say(j.note || j.error || "That did not work.", true); else if (body !== undefined) say("");
    return j;
  } catch (e) { $("link").textContent = "no connection"; $("link").className = "pill bad"; return null; }
}
function show(j) {
  st = j.state; const p = st.playing;
  $("name").textContent = st.name ? st.name + (st.room ? " · " + st.room : "") : "ANVE Speaker";
  $("link").textContent = st.online ? "online" : "offline"; $("link").className = "pill " + (st.online ? "ok" : "bad");
  $("title").textContent = p ? p.title : "Nothing is playing";
  $("sub").textContent = p ? (p.source === "radio" ? "Radio" : p.source === "youtube" ? "YouTube" : "Web") +
    (p.paused ? " · paused" : "") + (p.ducked ? " · turned down while you talk" : "") +
    (p.duration ? " · " + mmss(p.position) + " / " + mmss(p.duration) : "") : "";
  $("art").hidden = !(p && p.art); if (p && p.art && $("art").src !== p.art) $("art").src = p.art;
  $("prog").hidden = !(p && p.duration); if (p && p.duration) { $("prog").max = p.duration; $("prog").value = p.position; }
  $("toggle").disabled = !p; $("toggle").textContent = p && p.paused ? "Play" : "Pause";
  $("next").disabled = !p || p.source === "radio";
  if (!dragging) { $("vol").value = st.volume; $("volv").textContent = st.volume + " %"; }
  $("mute").textContent = st.muted ? "Unmute microphone" : "Mute microphone";
  $("meet").textContent = st.recording ? "Recording this room now" : "Not recording";
  $("mstart").disabled = !!st.recording; $("mmark").disabled = !st.recording; $("mstop").disabled = !st.recording;
  $("status").textContent = "State: " + st.state + " · loudspeaker " + (st.speaker ? "connected" : "NOT connected") +
    " · microphone " + (st.mic ? "ok" : "missing") + (st.approvals ? " · " + st.approvals + " approval(s) waiting in ANVE" : "");
  if (!radioDone && j.radio) { radioDone = true;
    for (const [k, n] of Object.entries(j.radio)) { const b = document.createElement("button");
      b.textContent = n; b.onclick = async () => { say("Starting " + n + " ..."); await call("/api/play", {radio: k}); }; $("radio").append(b); } }
}
async function poll() { const j = await call("/api/state"); if (j && j.ok) show(j); }
$("toggle").onclick = async () => { await call("/api/media", {do: st && st.playing && st.playing.paused ? "resume" : "pause"}); poll(); };
$("next").onclick = async () => { await call("/api/media", {do: "next"}); poll(); };
$("quiet").onclick = () => call("/api/stop", {});
const meet = async what => { const j = await call("/api/meeting", {do: what}); if (j && j.ok) say(what === "start" ? "Recording the room. The speaker says so out loud." : what === "mark" ? "Marked." : "Stopped. The minutes will be in your ANVE chats in a few minutes."); poll(); };
$("mstart").onclick = () => meet("start"); $("mmark").onclick = () => meet("mark"); $("mstop").onclick = () => meet("stop");
$("mute").onclick = async () => { await call("/api/mute", {on: !(st && st.muted)}); poll(); };
$("vol").oninput = () => { dragging = true; $("volv").textContent = $("vol").value + " %"; };
$("vol").onchange = async () => { await call("/api/volume", {level: +$("vol").value}); dragging = false; poll(); };
$("playf").onsubmit = async e => { e.preventDefault(); const q = $("query").value.trim(); if (!q) return;
  say("Looking for “" + q + "” ..."); await call("/api/play", {query: q}); $("query").value = ""; };
poll(); setInterval(poll, 1500);
</script></body></html>
"""
