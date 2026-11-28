"""Things the speaker does by itself, on the Pi, without the ANVE platform.

The platform has no tool for playing media on a device, and the platform is not changed.
So the speaker watches what ANVE reports it heard (`transcript.in`), and when the request
is one of these it acts locally and stops ANVE's own answer:

    "play <something> [on YouTube]"   opens YouTube in the speaker's OWN Chromium, picks
                                      the first result and plays it (free, with ads)
    "play some music" / "play radio"  a free internet radio stream (no browser)
    "pause" / "stop the music", "resume", "next song"
    "volume up" / "volume down"
    "open <site>", "search for <x> in the browser"

The browser is a separate Chromium profile (~/.anve-browser) driven over the Chrome
DevTools Protocol on 127.0.0.1. Its sound is sent to the echo-cancel sink, so the
speaker's microphone can subtract it like any other sound it plays.

Also here: short spoken fillers ("Hmm, let me check") for while ANVE is working.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

log = logging.getLogger("anve.skills")
SINK = "anve_ec_sink"
RADIO = {  # free streams that allow direct listening
    "default": ("SomaFM Groove Salad", "https://ice1.somafm.com/groovesalad-128-mp3"),
    "lofi": ("SomaFM Fluid", "https://ice1.somafm.com/fluid-128-mp3"),
    "jazz": ("SomaFM Sonic Universe", "https://ice1.somafm.com/sonicuniverse-128-mp3"),
    "classical": ("Radio Swiss Classic", "https://stream.srg-ssr.ch/m/rsc_de/mp3_128"),
    "indie": ("SomaFM Indie Pop Rocks", "https://ice1.somafm.com/indiepop-128-mp3"),
}
SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com",
         "wikipedia": "https://en.wikipedia.org", "gmail": "https://mail.google.com",
         "anve": "https://app.anveai.com", "spotify": "https://open.spotify.com",
         "maps": "https://www.google.com/maps", "news": "https://news.google.com"}


# ---------- understanding the request ----------

_LEAD = re.compile(r"^(hey |hi |ok |okay )?(anve[, ]+)?(please |can you |could you |will you |"
                   r"would you |i want you to |i want to |let's )*", re.I)
_PLATFORM = r"(?: (?:on|in|from|using|with) (?:the )?(youtube|yt|you tube|browser|chromium|chrome|web|internet|any platform|radio))?"


_TENS = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_WORDS = {"zero": 0, "five": 5, "fifteen": 15, "half": 50, "full": 100, "max": 100,
          "maximum": 100, "hundred": 100, "a hundred": 100, "one hundred": 100, **_TENS}


_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9}
_TEENS = {"eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_WORDS.update(_UNITS)
_WORDS.update(_TEENS)
#: the words a spoken command must start with, once a greeting and a (possibly misheard)
#: name are taken off: "Hey Anvi, set volume ..." reaches the speaker as often as "Hey ANVE"
_VERBS = ("play", "pause", "stop", "resume", "continue", "skip", "next", "open", "search", "look",
          "volume", "set", "change", "put", "make", "turn", "increase", "decrease", "raise",
          "lower", "reduce", "go", "show", "launch", "start", "listen", "google", "louder",
          "quieter", "softer", "be", "keep", "carry", "unpause")


def _number(s: str) -> int | None:
    """30, "30", "thirty", "thirty five", "eighty two", "half", "full" -> a percentage; else None."""
    s = s.strip().replace("-", " ")
    if s.isdigit():
        return int(s)
    if s in _WORDS:
        return _WORDS[s]
    parts = s.split()
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS:
        return _TENS[parts[0]] + _UNITS[parts[1]]
    return None


def _volume(t: str) -> dict | None:
    """A volume order anywhere in a short sentence. The server's transcript of the same
    words varies ("Set the volume to 80%", "Hey Anvi set volume at eighty percent",
    "increase the volume to 80"), so this looks for the word and the number, not a shape."""
    if "volume" not in t.split() or len(t.split()) > 14:
        return None
    m = re.search(r"\bvolume(?: level)?(?: (?:up |down )?(?:to|at|is|of|by))? (\d{1,3}|[a-z]+(?: [a-z]+)?)\b", t)
    if m:
        words = m.group(1).split()
        level = _number(m.group(1))
        if level is None and len(words) == 2:
            level = _number(words[0])
        if level is not None:
            return {"do": "volume", "level": max(5, min(100, level)) / 100}
    m = re.search(r"\b(\d{1,3}) (?:percent|per cent) volume\b", t)
    if m:
        return {"do": "volume", "level": max(5, min(100, int(m.group(1)))) / 100}
    if re.search(r"\b(?:volume (?:up|higher)|(?:increase|raise|turn up) (?:the )?(?:speaker )?volume)\b", t):
        return {"do": "volume", "step": 0.1}
    if re.search(r"\b(?:volume (?:down|lower)|(?:decrease|lower|reduce|turn down) (?:the )?(?:speaker )?volume)\b", t):
        return {"do": "volume", "step": -0.1}
    return None


# "What time is it?" is answered from the speaker's own clock. The cloud answered "4 PM" at
# 5:15 PM (23 Nov 2026): it does not know this device's local time, and the device has no
# way to tell it. Narrow on purpose: the sentence must END with the question, so "what time
# is it in London", timers and alarms still go to ANVE.
_TIME = re.compile(r"\b(?:what(?:'s| is) (?:the )?(?:current )?time|what time is it|what's the time|"
                   r"tell me (?:the )?(?:current )?time|(?:the )?current time|time is it)"
                   r"(?: right)?(?: now)?$")


def is_time_question(text: str) -> bool:
    t = re.sub(r"[^\w\s']", " ", (text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r" (please|now|right now)$", "", t).strip()
    return 0 < len(t.split()) <= 9 and bool(_TIME.search(t))


def time_line() -> str:
    """'It is 5:15 PM.' from the Pi's clock (kept right by NTP, zone set at setup)."""
    return "It is " + time.strftime("%I:%M %p").lstrip("0") + "."


def parse(text: str) -> dict | None:
    """{'do': ..., ...} if the sentence is one the speaker handles itself, else None."""
    t = re.sub(r"[^\w\s'&.+-]", " ", (text or "").lower())
    t = re.sub(r"\s+", " ", t).strip(" .")
    # "hey anvi, set ..." / "hi and we play ...": a greeting, then a misheard name of one
    # or two words, then a command word. The name goes; "hey play ..." is left alone.
    m = re.match(r"(?:hey|hi|ok|okay|hello)[, ]+((?:[\w']+[, ]+){1,2}?)(\w+)", t)
    if m and m.group(2) in _VERBS and m.group(1).split()[0].strip(",") not in _VERBS:
        t = t[m.start(2):]
    t = _LEAD.sub("", t).strip()
    t = re.sub(r" (please|now|for me|right now)$", "", t).strip()
    if not t or len(t) > 120:
        return None
    if is_time_question(t):
        return {"do": "time"}
    vol = _volume(t)
    if vol:
        return vol
    if re.fullmatch(r"(stop|pause|stop it|pause it)( the)?( music| song| video| radio| playing| playback)?", t) \
            or t in ("stop playing", "be quiet", "turn it off", "turn off the music"):
        return {"do": "pause"}
    if re.fullmatch(r"(resume|continue|unpause|play|play it|play again|keep playing|carry on)"
                    r"( the| that)?( music| song| video)?( again)?", t):
        return {"do": "resume"}
    if re.fullmatch(r"(next|skip)( the| this)?( song| track| video| one)?|play (the )?next( song| track| one)?", t):
        return {"do": "next"}
    m = re.fullmatch(r"(?:turn (?:the )?)?volume (up|down)|(?:make it |turn it )?(louder|quieter|softer)|turn it (up|down)", t)
    if m:
        w = next(g for g in m.groups() if g)
        return {"do": "volume", "step": 0.1 if w in ("up", "louder") else -0.1}
    # "set volume to 30 percent", "volume 50", "make the volume sixty", "volume to half"
    m = re.fullmatch(r"(?:set |change |put |make |turn )?(?:the )?(?:speaker )?volume(?: level)?"
                     r"(?: to| at| is)? ([\w -]+?)(?: percent| per cent)?", t)
    if m:
        level = _number(m.group(1))
        if level is not None:
            return {"do": "volume", "level": max(5, min(100, level)) / 100}
    m = re.fullmatch(r"(?:play|put on|start playing|start|listen to)(?: me)?"
                     r"(?: (?:some|the|a|any)(?= |$))?(?: (.*?))?" + _PLATFORM, t)
    if m and t.split()[0] in ("play", "put", "start", "listen"):
        q, where = (m.group(1) or "").strip(), (m.group(2) or "")
        q = re.sub(r"^(some|the|a|any) ", "", q)
        generic = re.fullmatch(r"(music|songs?|something|anything|radio|a song|some music|tunes)?", q)
        kind = next((k for k in RADIO if k != "default" and re.fullmatch(rf"{k}( music| radio| songs)?", q)), None)
        if where == "radio" or (generic and where not in ("youtube", "yt", "you tube")) or \
                (kind and not where):
            return {"do": "radio", "station": kind or "default"}
        if generic:
            q = "music mix"
        return {"do": "youtube", "query": re.sub(r" (songs?|music)$", r" \1", q)}
    m = re.fullmatch(r"(?:open|go to|show me|launch) (.+?)(?: (?:on|in) (?:the )?(?:browser|chromium|chrome))?", t)
    if m:
        name = m.group(1).strip()
        if name in SITES:
            return {"do": "open", "url": SITES[name], "name": name}
        if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?", name):
            return {"do": "open", "url": "https://" + name, "name": name}
        return None
    m = re.fullmatch(r"(?:search|look up|google)(?: for)? (.+?) (?:on|in) (?:the )?(?:browser|chromium|chrome|web|internet|google)", t)
    if m:
        return {"do": "search", "query": m.group(1).strip()}
    return None


# ---------- a voice of its own, for fillers and confirmations ----------

def render(text: str, gain_db: float = -9.0) -> bytes:
    """`text` as 24 kHz mono PCM16 (espeak-ng through sox). Empty if not installed."""
    if not shutil.which("espeak-ng"):
        return b""
    try:
        wav = subprocess.run(["espeak-ng", "-v", "en-gb", "-s", "155", "--stdout", text],
                             capture_output=True, check=True, timeout=10).stdout
        return subprocess.run(["sox", "-t", "wav", "-", "-r", "24000", "-c", "1", "-b", "16",
                               "-e", "signed-integer", "-t", "raw", "-", "norm", f"{gain_db}"],
                              input=wav, capture_output=True, check=True, timeout=10).stdout
    except Exception:                                        # noqa: BLE001
        return b""


class Fillers:
    """Pre-rendered short phrases, so playing one costs nothing at the moment."""

    FIRST = ("Hmm.", "Let me think.", "One moment.", "Hmm, let me see.")
    TOOL = ("Let me check.", "Looking that up.", "Let me find out.")
    LONG = ("Still working on it.", "Almost there.")

    def __init__(self):
        self.clips: dict[str, list[bytes]] = {}
        self._i = 0

    def load(self):
        for kind in ("FIRST", "TOOL", "LONG"):
            self.clips[kind] = [c for c in (render(p) for p in getattr(self, kind)) if c]
        return self

    def pick(self, kind: str) -> bytes:
        clips = self.clips.get(kind) or []
        if not clips:
            return b""
        self._i += 1
        return clips[self._i % len(clips)]


# ---------- the speaker's own browser ----------

def _title(t) -> str:
    """A page title as a song title: without " - YouTube" and the "(3) " unread count."""
    return re.sub(r"^\(\d+\)\s*", "", str(t or "")).replace(" - YouTube", "").strip()


_SMALL = {"the", "a", "an", "of", "and", "to", "in", "on", "song", "songs", "music"}


def split_request(query: str) -> tuple:
    """("how you feel", "amit shah") for "how you feel by amit shah"; no "by": (query, "")."""
    q = re.sub(r"\s+", " ", query.strip().lower())
    m = re.match(r"(.+?) by (.+)$", q)
    return (m.group(1), m.group(2)) if m and len(m.group(1)) > 2 else (q, "")


def title_fits(wanted: str, title: str) -> int:
    """How well a song's title holds what was asked for: 2 the words in that order
    ("How You Feel (feat. ...)" for "how you feel"), 1 every word but not as a phrase
    ("Feel How You Want"), 0 not all of the words."""
    words = lambda s: [w for w in re.findall(r"[a-z0-9']+", s.lower().replace("\u2019", "'")) if w not in _SMALL]  # noqa: E731
    want, have = words(wanted), words(title)
    if not want or not all(w in have or w.rstrip("s") in have or w + "s" in have for w in want):
        return 0
    return 2 if any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1)) else 1


def best_fit(wanted: str, songs: list) -> tuple:
    """(the first song that fits best, how well: 2, 1 or 0 with None)"""
    how = [title_fits(wanted, s["title"]) for s in songs]
    top = max(how, default=0)
    return (songs[how.index(top)], top) if top else (None, 0)


class Browser:
    """One Chromium, with its own profile, driven over the DevTools Protocol."""

    def __init__(self, port: int = 9223, profile: str | None = None):
        self.port = port
        self.profile = profile or str(Path.home() / ".anve-browser")
        self.proc = None
        self.playing = False
        self.title = ""
        self.url = ""
        self.has_media = False                        # a video or song is loaded (maybe paused)
        self.pos = self.dur = 0.0
        self._id = 0

    def _targets(self) -> list:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
                return json.load(r)
        except Exception:                                    # noqa: BLE001
            return []

    @staticmethod
    def _extensions() -> list[str]:
        """Unpacked extensions for this browser only (tools/install_adblock.sh)."""
        dirs = sorted(str(p.parent) for p in
                      (Path.home() / ".anve-browser-ext").glob("*/manifest.json"))
        return [f"--load-extension={','.join(dirs)}"] if dirs else []

    async def ensure(self) -> bool:
        if await asyncio.to_thread(self._targets):
            return True
        exe = shutil.which("chromium") or shutil.which("chromium-browser")
        if not exe:
            log.error("Chromium is not installed")
            return False
        run = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        way = sorted(p.name for p in Path(run).glob("wayland-[0-9]") if p.is_socket())
        env = dict(os.environ, XDG_RUNTIME_DIR=run, PULSE_SINK=SINK,
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path={run}/bus")
        # say which display system outright: with only a "hint", Chromium started from a
        # service (no desktop environment variables) picked X11 and exited
        if way:
            env.update(WAYLAND_DISPLAY=way[0], XDG_SESSION_TYPE="wayland")
            platform = "--ozone-platform=wayland"
        elif Path("/tmp/.X11-unix/X0").exists():
            env["DISPLAY"] = ":0"
            platform = "--ozone-platform=x11"
        else:
            platform = "--headless=new"            # no desktop at all: sound still works
        self.proc = await asyncio.create_subprocess_exec(
            exe, f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1", "--autoplay-policy=no-user-gesture-required",
            "--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble",
            # after a power cut Chromium offers to "restore pages" in a bubble over the page
            "--hide-crash-restore-bubble",
            # Without this Chromium asks the desktop keyring to unlock and waits for a
            # dialog nobody sees: every page load hung forever (found 16 Nov 2026).
            "--password-store=basic",
            "--disable-features=Translate,DisableLoadExtensionCommandLineSwitch",
            *self._extensions(), platform, "--window-size=1000,700",
            "about:blank", env=env, stdout=asyncio.subprocess.DEVNULL,
            # keep what Chromium says when it dies: it once vanished mid-song with no trace
            stderr=open(Path(self.profile).parent / ".anve-browser.log", "ab"),
            start_new_session=True)
        for _ in range(40):
            await asyncio.sleep(0.5)
            if await asyncio.to_thread(self._targets):
                log.info("browser started (profile %s)", self.profile)
                return True
        log.error("the browser did not start")
        return False

    async def cdp(self, method: str, params: dict | None = None, timeout: float = 15):
        page = next((t for t in await asyncio.to_thread(self._targets) if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("no browser page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024,
                                      open_timeout=5, ping_interval=None) as ws:
            self._id += 1
            mid = self._id
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if msg.get("id") == mid:
                    return msg.get("result", {})

    async def goto(self, url: str, settle: float = 3.0):
        await self.cdp("Page.navigate", {"url": url})
        await asyncio.sleep(settle)

    async def js(self, expr: str):
        r = await self.cdp("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                "awaitPromise": True, "userGesture": True})
        return (r.get("result") or {}).get("value")

    # privacy: if a consent wall appears, take the option that refuses optional cookies
    _REJECT = """(() => { const b=[...document.querySelectorAll('button, tp-yt-paper-button, a')]
      .find(e => /^(reject all|reject the use|decline|no,? thanks)/i.test((e.innerText||'').trim()));
      if (b) { b.click(); return true } return false })()"""

    # YouTube Music's search page, in the page itself: press the "Songs" filter and read the
    # first rows. Songs only, because the plain search (and plain YouTube) happily returns
    # an interview or a news clip for a song request.
    _SONGS_JS = """(async () => {
      const wait = ms => new Promise(r => setTimeout(r, ms));
      const chips = () => [...document.querySelectorAll('ytmusic-chip-cloud-chip-renderer')];
      const read = r => ({
        title: (r.querySelector('.title')?.textContent || '').trim().slice(0, 120),
        by: (r.querySelector('.secondary-flex-columns')?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
        id: ((r.querySelector('a[href*="watch?v="]')?.getAttribute('href') || '').match(/v=([\\w-]{6,20})/) || [])[1] || '',
      });
      for (let i = 0; i < 25 && !chips().length; i++) await wait(300);
      // before filtering: the rows the page itself marks as a "Song" (its best guesses)
      const first = [...document.querySelectorAll('ytmusic-responsive-list-item-renderer')].map(read)
        .filter(s => /^song\\b/i.test(s.by));
      const chip = chips().find(c => /^songs$/i.test(c.textContent.trim()));
      if (chip) (chip.querySelector('a, button, [role=button]') || chip).click();
      let rows = [];
      for (let i = 0; i < 25; i++) {
        await wait(300);
        const on = !chip || chips().some(c => /^songs$/i.test(c.textContent.trim())
          && (c.hasAttribute('is-selected') || c.querySelector('[aria-selected=true]')));
        rows = [...document.querySelectorAll('ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer')];
        if (on && rows.length) break;
      }
      const seen = new Set();
      return [...first, ...rows.slice(0, 8).map(read)].filter(s => s.id && s.title && !seen.has(s.id) && seen.add(s.id))
        .map(s => ({ ...s, by: s.by.replace(/^song\\s*\\u2022\\s*/i, '') })).slice(0, 12) })()"""

    async def _songs(self, query: str) -> list:
        await self.goto("https://music.youtube.com/search?q=" + urllib.parse.quote_plus(query), settle=2.5)
        if await self.js(self._REJECT):
            await asyncio.sleep(3)
        try:
            return await self.js(self._SONGS_JS) or []
        except Exception:                                    # noqa: BLE001
            return []

    async def music(self, query: str) -> str:
        """Find the song on YouTube Music and play it. Returns what is playing, or "" when
        nothing fitting was found (the caller then tries plain YouTube).

        "X by Y": the speech recogniser gets a song's title right far more often than an
        artist's name ("How You Feel by ANOTR" arrived as "... by Amit Shah" and played a
        politician's interview). So a result only counts if its TITLE holds the words that
        were asked for; if none does, the search is done again with the title alone."""
        if not await self.ensure():
            return ""
        title, artist = split_request(query)
        songs = await self._songs(query)
        pick, how = best_fit(title, songs)
        if artist and how < 2:
            # no song with that title by that "artist": the artist was probably misheard
            again, again_how = best_fit(title, await self._songs(title))
            if again_how > how:
                pick, how = again, again_how
                log.info("the artist as heard matched no song with that title: playing the best-known one")
        if pick is None and not artist and songs:
            pick = songs[0]                              # "lofi beats": no title to hold a result to
        if pick is None:
            return ""
        href = "https://music.youtube.com/watch?v=" + pick["id"]
        await self.goto(href, settle=4.0)
        await self._start_playing()
        who = pick["by"].split("\u2022")[0].strip()
        self.title = pick["title"] + (f" by {who}" if who else "")
        self.url, self.has_media, self.pos, self.dur = href, True, 0.0, 0.0
        self.playing = True
        return self.title

    async def _start_playing(self):
        await self.js("""(() => { const p=document.getElementById('movie_player');
            try { p && p.setPlaybackQualityRange && p.setPlaybackQualityRange('tiny','tiny') } catch(e) {}
            try { p && p.setPlaybackQuality && p.setPlaybackQuality('tiny') } catch(e) {}   // 144p: it is a speaker
            try { p && p.setVolume && p.setVolume(35) } catch(e) {}   // quiet enough to be talked over
            const v=document.querySelector('video'); if (v) { v.muted=false; v.play().catch(()=>{}) } })()""")
        await self.js(self._NO_ADS)

    async def youtube(self, query: str) -> str:
        """Play what was asked for: a song from YouTube Music if one fits, otherwise the
        first result of a plain YouTube search. Returns its title."""
        if not await self.ensure():
            return ""
        try:
            found = await self.music(query)
        except Exception:                                    # noqa: BLE001
            log.exception("YouTube Music search failed")
            found = ""
        if found:
            return found
        await self.goto("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query))
        if await self.js(self._REJECT):
            await asyncio.sleep(3)
        href = None
        for _ in range(8):
            href = await self.js(
                "(() => { const a=[...document.querySelectorAll('ytd-video-renderer a#video-title')]"
                ".find(e => e.href && e.href.includes('/watch')); return a ? a.href : null })()")
            if href:
                break
            await asyncio.sleep(1)
        if not href:
            log.warning("no YouTube result found for %r", query)
            return ""
        await self.goto(href, settle=4.0)
        await self._start_playing()
        self.title = _title(await self.js("document.title"))
        self.url, self.has_media, self.pos, self.dur = href, True, 0.0, 0.0
        self.playing = True
        return self.title or query

    # Ads: the extension blocks most ad requests; this catches a video ad that still
    # starts. Every 300 ms, if the player says an ad is showing: mute it, jump to its end
    # and press Skip if there is one; un-mute when the real video is back. It stays in
    # the page across YouTube's own "next video" changes.
    _NO_ADS = """(() => { if (window.__anveNoAds) return true; let n = 0; window.__anveNoAds = setInterval(() => {
        const p = document.querySelector('.html5-video-player'), v = document.querySelector('video');
        if (!p || !v) return;
        // every 3 s: hold the picture at 144p (YouTube raises it by itself, and each
        // "next video" starts at its own choice). Nobody watches; decoding costs CPU.
        if (++n % 10 === 0) { const m = document.getElementById('movie_player');
          try { if (m && m.getPlaybackQuality && m.getPlaybackQuality() !== 'tiny') {
                  m.setPlaybackQualityRange('tiny', 'tiny'); m.setPlaybackQuality('tiny') } } catch (e) {} }
        if (p.classList.contains('ad-showing') || p.classList.contains('ad-interrupting')) {
          if (!v.muted) { v.muted = true; window.__anveMutedForAd = true }
          if (isFinite(v.duration) && v.duration > 0) v.currentTime = v.duration;
          document.querySelectorAll('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern, '
            + '.ytp-ad-overlay-close-button').forEach(b => b.click());
        } else if (window.__anveMutedForAd) { v.muted = false; window.__anveMutedForAd = false }
      }, 300); return true })()"""

    async def pause(self):
        self.playing = False
        try:
            await self.js("document.querySelectorAll('video,audio').forEach(v => v.pause())")
        except Exception:                                    # noqa: BLE001
            pass

    async def resume(self) -> bool:
        try:
            ok = await self.js("(() => { const v=document.querySelector('video,audio'); "
                               "if (v) { v.play().catch(()=>{}); return true } return false })()")
        except Exception:                                    # noqa: BLE001
            ok = False
        self.playing = bool(ok)
        return self.playing

    async def next(self) -> bool:
        try:
            ok = await self.js("(() => { const b=document.querySelector('.ytp-next-button, "
                               "ytmusic-player-bar .next-button'); "
                               "if (b) { b.click(); return true } return false })()")
        except Exception:                                    # noqa: BLE001
            ok = False
        if ok:
            await asyncio.sleep(3)
            self.title = _title(await self.js("document.title"))
            self.playing = True
        return bool(ok)

    async def open(self, url: str) -> bool:
        if not await self.ensure():
            return False
        await self.goto(url)
        if await self.js(self._REJECT):
            await asyncio.sleep(1)
        return True

    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()


class Radio:
    """A free internet radio stream: ffmpeg decodes it, pw-play sends it to the sink."""

    def __init__(self):
        self.proc = None
        self.name = ""

    @property
    def playing(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, station: str = "default") -> str:
        await self.stop()
        self.name, url = RADIO.get(station, RADIO["default"])
        self.proc = await asyncio.create_subprocess_shell(
            f"ffmpeg -nostdin -loglevel error -reconnect 1 -reconnect_streamed 1 -i {url!r} "
            f"-vn -f s16le -ar 48000 -ac 2 - | pw-play --raw --target {SINK} --rate 48000 "
            "--channels 2 --format s16 --volume 0.6 "
            "-P '{\"node.name\":\"anve-radio\",\"node.dont-reconnect\":true}' -",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
        return self.name

    async def stop(self):
        p, self.proc = self.proc, None
        if p and p.returncode is None:
            try:
                os.killpg(p.pid, 15)
            except ProcessLookupError:
                pass


# Music while someone talks to the speaker: quieter, and muffled as if it were playing in
# the next room (a low-pass filter at 500 Hz). The muffling takes the singer's voice and
# the bright part of the music out of the room, which is what the microphone and the
# speech detector trip over; the bass that is left says "the music is still on".
#
# The filter is the page's own Web Audio: the <video>'s sound is routed through one filter
# node, wide open (20 kHz) normally and closed to 500 Hz while listening. It is only set
# up if the page's audio engine really starts (300 ms to do so); otherwise nothing is
# touched and only the volume changes, exactly as before.
_DUCK_JS = """(async () => {
  const k = %s, muffle = %s;
  for (const v of document.querySelectorAll('video,audio')) {
    if (v.dataset.anveVol === undefined) v.dataset.anveVol = v.volume;
    v.volume = Math.max(0, Math.min(1, v.dataset.anveVol * k));
    if (k >= 1) delete v.dataset.anveVol;
    let ctx = null;
    try {
      if (!v._anve && muffle && !v._anveNo) {
        ctx = new AudioContext();
        await Promise.race([ctx.resume(), new Promise(r => setTimeout(r, 300))]);
        if (ctx.state !== 'running') { ctx.close(); continue; }
        const f = ctx.createBiquadFilter();
        f.type = 'lowpass'; f.frequency.value = 20000; f.Q.value = 0.5;
        f.connect(ctx.destination);
        ctx.createMediaElementSource(v).connect(f);     // throws if the page already took the sound
        v._anve = { ctx, f };
        ctx = null;
      }
      if (v._anve) {
        const t = v._anve.ctx.currentTime;
        v._anve.f.frequency.cancelScheduledValues(t);
        v._anve.f.frequency.setTargetAtTime(muffle ? 500 : 20000, t, 0.06);
      }
    } catch (e) {
      // could not be set up for this video: close what was opened and do not try again,
      // the volume change above still applies
      if (ctx) { try { ctx.close() } catch (e2) {} }
      v._anveNo = true;
    }
  }
  return document.querySelectorAll('video,audio').length })()"""


class Media:
    """What is playing, and the one place that pauses, resumes and ducks it."""

    def __init__(self, duck: float = 0.3, muffle: bool = True, music_max: float = 0.0):
        self.browser, self.radio = Browser(), Radio()
        # Music is never played louder than the speaker at `music_max` of its volume
        # (0 = no limit), whatever the speaker volume is set to; ANVE's own voice is not
        # limited. Why: this loudspeaker stands so close to the microphone that above
        # about 30 % the microphone itself overloads (measured 23 Nov 2026 with a song
        # playing: at 50 % one sample in twenty was clipped and the echo canceller took
        # only 15 dB of the music away; at 30 % nothing clipped and it took 28 dB). With
        # the music left over as loud as a voice, "Hey ANVE" cannot be heard by any means.
        self.music_max, self.cap = music_max, 1.0
        self.muffle = muffle                           # low-pass the browser's music while listening
        self.ducked = False
        self.duck_level = max(0.05, min(1.0, duck))   # share of the volume kept while listening
        self.last_station = "default"
        self.last_query = self.restarted_for = ""

    def set_speaker(self, volume: float):
        """The speaker volume changed: work out how far music must be turned down. The
        sink's volume scale is cubic, so its share is a plain ratio on that scale."""
        self.cap = min(1.0, self.music_max / max(volume, 0.05)) if self.music_max else 1.0

    async def recap(self):
        """Put the limit on whatever plays now (a next video is a new player)."""
        if self.cap < 1.0 and self.playing and not self.ducked:
            await self._scale(1.0, "radio" if self.radio.playing else "browser")

    async def _scale(self, k: float, which: str):
        """Set what is playing to `k` times its own volume (1.0 puts it back)."""
        try:
            if which == "browser":
                # the page's volume is a plain gain: the cubic share, cubed
                await self.browser.js(_DUCK_JS % (round(k * self.cap ** 3, 4),
                                                  "true" if (k < 1 and self.muffle) else "false"))
            elif which == "radio":
                from .audio import nodes
                for n in await asyncio.to_thread(nodes, "Stream/Output/Audio"):
                    if n["name"] == "anve-radio":
                        await asyncio.to_thread(subprocess.run, ["wpctl", "set-volume", str(n["id"]),
                                                                 f"{0.6 * k * self.cap:.2f}"], capture_output=True)
        except Exception:                                    # noqa: BLE001
            log.warning("could not change the music volume")

    @property
    def playing(self) -> bool:
        return self.browser.playing or self.radio.playing

    async def refresh(self):
        """Ask the browser whether something is really playing: the person may have
        started or stopped a video there by hand."""
        b = self.browser
        if not await asyncio.to_thread(b._targets):
            was, b.playing = b.playing, False
            b.has_media, b.title, b.url = False, "", ""
            if was:
                log.warning("the browser is gone while a song was playing (see ~/.anve-browser.log)")
                if self.last_query and self.last_query != self.restarted_for:
                    # once per request: open it again and carry on with the same song
                    self.restarted_for = self.last_query
                    log.info("starting the browser again for %r", self.last_query)
                    await b.youtube(self.last_query)
            return
        try:
            st = await b.js(
                "(() => { const m=[...document.querySelectorAll('video,audio')];"
                " const v=m.find(v => !v.paused && !v.ended && v.currentTime > 0) || m.find(v => v.currentTime > 0 && !v.ended);"
                " return {playing: !!v && !v.paused, has: !!v, title: document.title, url: location.href,"
                " t: v ? v.currentTime : 0, d: v && isFinite(v.duration) ? v.duration : 0} })()") or {}
            b.playing = bool(st.get("playing"))
            b.has_media = bool(st.get("has"))
            if b.has_media:                           # YouTube moves on to the next video by itself
                b.title, b.url = _title(st.get("title")), str(st.get("url") or "")
                b.pos, b.dur = float(st.get("t") or 0), float(st.get("d") or 0)
        except Exception:                                    # noqa: BLE001
            pass

    def now(self) -> dict | None:
        """What is playing (or paused), for the phone app. None when nothing is."""
        if self.radio.playing:
            return {"source": "radio", "title": self.radio.name, "paused": False,
                    "ducked": bool(self.ducked)}
        b = self.browser
        if not (b.has_media and b.title):
            return None
        m = re.search(r"[?&]v=([\w-]{6,20})", b.url) if "youtube.com/watch" in b.url else None
        return {"source": "youtube" if m else "web", "title": b.title, "paused": not b.playing,
                "ducked": bool(self.ducked), "position": int(b.pos), "duration": int(b.dur),
                "art": f"https://i.ytimg.com/vi/{m.group(1)}/mqdefault.jpg" if m else ""}

    async def pause(self):
        await self.radio.stop()
        if self.browser.playing:
            await self.browser.pause()

    async def duck(self):
        """Someone is talking to the assistant: turn the music DOWN (not off) while it
        listens and answers, the way a Google Home or an Echo does."""
        if self.playing and not self.ducked:
            self.ducked = "radio" if self.radio.playing else "browser"
            await self._scale(self.duck_level, self.ducked)

    async def unduck(self):
        was, self.ducked = self.ducked, False
        if was:
            await self._scale(1.0, was)

    async def run(self, intent: dict) -> str:
        """Do it. Returns one short sentence to say, or '' to stay silent."""
        do = intent["do"]
        if do == "youtube":
            await self.radio.stop()
            self.last_query, self.restarted_for = intent["query"], ""
            title = await self.browser.youtube(intent["query"])
            self.ducked = False
            return "" if title else f"I could not find {intent['query']} on YouTube."
        if do == "radio":
            if self.browser.playing:
                await self.browser.pause()
            self.last_station = intent.get("station", "default")
            self.ducked = False
            await self.radio.start(self.last_station)
            return ""
        if do == "pause":
            await self.unduck()                    # so a later "play" comes back at full volume
            await self.pause()
            self.last_query = ""                   # paused on purpose: nothing to bring back
            return ""
        if do == "resume":
            await self.unduck()
            if self.playing:
                return ""
            if await self.browser.resume():
                return ""
            return "Nothing is paused."            # never start something nobody asked for
        if do == "next":
            await self.unduck()
            return "" if await self.browser.next() else "There is no next one to skip to."
        if do == "open":
            ok = await self.browser.open(intent["url"])
            return f"Opened {intent['name']}." if ok else "I could not open the browser."
        if do == "search":
            ok = await self.browser.open("https://duckduckgo.com/?q=" + urllib.parse.quote_plus(intent["query"]))
            return "The results are on the screen." if ok else "I could not open the browser."
        return ""

    async def close(self):
        await self.radio.stop()
        await self.browser.close()
