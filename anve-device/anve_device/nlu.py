"""What did the person mean? Everyday speech, English or Hinglish, to one device action.

Until 23 Nov 2026 the speaker understood a handful of exact English sentences ("set volume
to 30 percent", "play X"). Its owner called it "a dumb machine", with reason: the journal
showed "can you just just a second. what's the time" being searched for on YouTube, and
"can you play some of like weightless by martin garrix" searched with all of those words.

`understand(text, ctx)` reads the sentence as the cloud transcribed it and returns the same
kind of dict `skills.parse` does, or None when the request is not for the device (then ANVE
answers it). It works on meaning-carrying words, not sentence shapes, and it uses context:

    ctx = {"playing": bool,   something is playing right now (or turned down to listen)
           "paused":  bool,   something is paused and can go on
           "last":    dict,   the last device action, e.g. {"do": "volume", "step": 0.1}
           "age":     float}  seconds since that action

    "awaaz kam karo", "it's too loud", "thoda aur", "gaana band karo", "ye nahi, agla
    lagao", "kaunsa gaana hai", "Arijit ke gaane bajao", "what's the volume"

Rules of caution, each from a real risk:
  * a stop word stops MUSIC only when music is playing; "stop recording", timers, alarms and
    meetings belong to ANVE and are never touched;
  * "kam karo" / "badhao" without the word volume count only while music plays or right
    after a volume change;
  * with several sentences, the LAST one is what the person ended up wanting.

Pure text in, dict out: tested without a speaker by tools/nlu_test.py.
"""
import re

# Hindi written in Devanagari, as the cloud sometimes transcribes it, to the same words in
# Latin letters. Only the words this module looks for.
_DEV = {
    "आवाज़": "awaaz", "आवाज": "awaaz", "वॉल्यूम": "volume", "वॉल्युम": "volume", "साउंड": "sound",
    "कम": "kam", "ज्यादा": "zyada", "ज़्यादा": "zyada", "बढ़ा": "badha", "बढ़ाओ": "badhao", "बढ़ा": "badha",
    "बढ़ाओ": "badhao", "घटा": "ghata", "घटाओ": "ghatao", "धीरे": "dheere", "धीमा": "dheema", "धीमी": "dheema",
    "तेज़": "tez", "तेज": "tez", "गाना": "gaana", "गाने": "gaane", "गाणा": "gaana", "म्यूजिक": "music",
    "म्यूज़िक": "music", "सॉन्ग": "song", "बंद": "band", "रोको": "roko", "रोक": "rok", "रुको": "ruko",
    "रुक": "ruk", "अगला": "agla", "अगली": "agla", "बजाओ": "bajao", "बजा": "baja", "चलाओ": "chalao",
    "चला": "chala", "लगाओ": "lagao", "लगा": "laga", "सुनाओ": "sunao", "सुना": "suna", "करो": "karo",
    "कर": "kar", "दो": "do", "थोड़ा": "thoda", "थोड़ी": "thoda", "थोडा": "thoda", "बहुत": "bahut",
    "और": "aur", "फिर": "phir", "से": "se", "कौनसा": "kaunsa", "कौन": "kaun", "सा": "sa", "है": "hai",
    "हैं": "hain", "यह": "ye", "ये": "ye", "नहीं": "nahi", "टाइम": "time", "समय": "samay", "क्या": "kya",
    "कितना": "kitna", "कितने": "kitne", "बजा": "baja", "बजे": "baje", "स्टॉप": "stop", "प्ले": "play",
    "चुप": "chup", "बस": "bas", "पूरा": "poora", "आधा": "aadha", "पसंद": "pasand", "दूसरा": "doosra",
    "चालू": "chalu", "वापस": "wapas", "का": "ka", "के": "ke", "की": "ki", "नाम": "naam", "हो": "ho",
    "गया": "gaya", "रहा": "raha", "सुनाई": "sunai", "दे": "de", "मत": "mat", "अब": "ab", "इसे": "ise",
    "किसने": "kisne", "बनाया": "banaya", "तुम्हें": "tumhe", "तुम्हे": "tumhe", "आपको": "aapko", "तुमको": "tumko",
    "इसको": "isko", "पॉज़": "pause", "पॉज": "pause", "नेक्स्ट": "next", "हे": "hey", "ज़रा": "zara", "जरा": "zara",
}
_FILL = {"please", "plz", "kindly", "just", "uh", "um", "hmm", "like", "yaar", "bhai", "na", "toh", "ji",
         "zara", "actually", "okay", "ok", "hey", "hi", "hello", "now", "abhi", "ab", "anve", "anvi", "anvay",
         "envy", "anway", "andy", "annie", "kar", "karo", "kardo", "kariye", "kijiye", "do", "de", "dena",
         "dijiye", "ko", "the", "a", "my", "this", "that", "it", "is", "hai", "hain", "ho", "you", "can",
         "could", "would", "will", "me", "for", "i", "want", "to", "need", "wanna", "lets", "let's", "us"}
_VOL = {"volume", "awaaz", "awaz", "aawaz", "avaaz", "awaj", "sound", "vol"}
_UP = {"up", "increase", "raise", "louder", "loud", "higher", "high", "badha", "badhao", "badhana", "badhaao",
       "zyada", "jyada", "jyaada", "zyaada", "tez", "upar", "uper", "more", "unchi", "ooncha"}
_DOWN = {"down", "decrease", "lower", "reduce", "quieter", "softer", "soft", "low", "kam", "ghata", "ghatao",
         "ghatana", "dheere", "dhire", "dheema", "dhima", "halka", "halki", "neeche", "niche", "less", "slow"}
_SMALL = {"thoda", "thodi", "thora", "little", "bit", "slightly", "tad", "zara"}
_BIG = {"bahut", "much", "lot", "kaafi", "kafi", "way", "really", "very"}
_HNUM = {"das": 10, "bees": 20, "bis": 20, "tees": 30, "tis": 30, "chalis": 40, "chaalis": 40, "pachas": 50,
         "pachaas": 50, "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90, "sau": 100, "poora": 100,
         "pura": 100, "full": 100, "max": 100, "maximum": 100, "aadha": 50, "adha": 50, "half": 50,
         "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90, "hundred": 100, "minimum": 10, "min": 10}
_STOP = {"stop", "pause", "band", "bandh", "bnd", "rok", "roko", "ruk", "ruko", "chup", "enough", "quiet",
         "silence", "shut", "hatao", "bas"}
_MUSIC = {"music", "song", "songs", "gaana", "gana", "gaane", "gane", "track", "video", "radio", "ise", "isko",
          "ye", "yeh", "playing", "playback", "sab"}
_NOT_OURS = {"record", "recording", "timer", "alarm", "meeting", "reminder", "listening", "talking", "email",
             "call", "note", "notes", "task"}
_NEXT = {"next", "skip", "agla", "agli", "doosra", "dusra", "another", "different"}
_RESUME = {"resume", "continue", "unpause", "chalu", "wapas", "chalao", "chala", "bajao", "baja", "play",
           "start", "shuru"}
_PLAY_EN = r"(?:start playing|play|put on|listen to|i want to hear|i wanna hear)"
_PLAY_HI = r"(?:bajao|baja do|baja|chalao|chala do|chala|lagao|laga do|laga|sunao|suna do|suna|play karo|play kar do|play kardo|play kar)"
_GENERIC = {"", "music", "song", "songs", "something", "anything", "gaana", "gaane", "gana", "gane", "kuch",
            "koi", "some", "any", "a song", "some music", "kuch gaane", "koi gaana", "kuch bhi", "tunes"}


def _clean(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[ऀ-ॿ]+", lambda m: " " + _DEV.get(m.group(0), m.group(0)) + " ", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"[^\w\s'.?!ऀ-ॿ-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _words(clause: str) -> list:
    return [w for w in re.sub(r"[.?!]", " ", clause).split() if w]


def _level(ws: list) -> int | None:
    for i, w in enumerate(ws):
        if w.isdigit() and 0 <= int(w) <= 100:
            if i + 1 < len(ws) and ws[i + 1] in ("percent", "per", "pratishat") or _VOL & set(ws):
                return int(w)
        if w in _HNUM and (_VOL & set(ws) or w in ("full", "max", "maximum", "minimum", "poora", "pura")):
            # "thirty five"
            if i + 1 < len(ws) and ws[i + 1] in ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine") \
                    and _HNUM[w] % 10 == 0 and _HNUM[w] < 100:
                return _HNUM[w] + ("one two three four five six seven eight nine".split().index(ws[i + 1]) + 1)
            return _HNUM[w]
    return None


def _volume(ws: list, t: str, ctx: dict) -> dict | None:
    s = set(ws)
    if len(ws) > 14 or re.search(r"\bvolume of\b", t):
        return None
    if _VOL & s and re.search(r"\b(kitna|kitni|what'?s|what is|how (?:loud|much|high)|current|batao|bata)\b", t) \
            and not (_UP | _DOWN) & s and _level(ws) is None:
        return {"do": "volume_query"}
    # complaints say which way without saying "volume" (an order, "... karo", is not one)
    order = bool(s & {"karo", "kar", "kardo", "do", "dijiye", "kijiye", "badhao", "ghatao", "set", "make", "turn"})
    if not order and re.search(r"\b(too|very|so|bahut|kaafi|kafi|itna|itni) (loud|tez|zyada tez|high)\b|kaan (phat|dukh)|"
                 r"\b(loud|tez) (hai|ho gaya|ho raha)\b", t) and not re.search(r"\bnot (too |very |so )?loud\b", t):
        return {"do": "volume", "step": -0.15}
    if not order and re.search(r"\b(can'?t|cannot|can not|couldn'?t) hear\b|sunai nahi|\b(too|very|so|bahut|kaafi) "
                 r"(quiet|low|soft|dheere|dheema|kam|halka)\b|awaaz nahi aa|aawaz nahi aa", t):
        return {"do": "volume", "step": 0.15}
    has_vol = bool(_VOL & s)
    near = ctx.get("playing") or ((ctx.get("last") or {}).get("do") == "volume" and ctx.get("age", 1e9) < 120)
    if not has_vol and not near:
        return None
    if has_vol:
        lv = _level(ws)
        if lv is not None and not (lv <= 9 and (_UP | _DOWN) & s and "by" not in s and "percent" not in s):
            if "by" in s and (_UP | _DOWN) & s:                  # "increase volume by 20"
                return {"do": "volume", "step": (lv / 100) * (1 if _UP & s else -1)}
            return {"do": "volume", "level": max(5, min(100, lv)) / 100}
        if "mute" in s:
            return {"do": "volume", "level": 0.05}
    up, down = bool(_UP & s), bool(_DOWN & s)
    if "nahi" in s or "not" in s or "don't" in s or "mat" in s:   # "kam mat karo": leave it to ANVE
        return None
    if up == down:
        return None
    # without the word volume, the sentence must be about nothing else
    rest = [w for w in ws if w not in _FILL and w not in _UP and w not in _DOWN and w not in _SMALL
            and w not in _BIG and w not in _VOL and w not in ("turn", "make", "set", "aur", "bhi", "se", "ka", "ki")]
    if not has_vol and rest:
        return None
    step = 0.05 if _SMALL & s else 0.2 if _BIG & s else 0.1
    return {"do": "volume", "step": step if up else -step}


def _follow_up(ws: list, ctx: dict) -> dict | None:
    """ "thoda aur", "more", "again", "a bit less", "zyada ho gaya": about the last action. """
    last = ctx.get("last") or {}
    if not last or ctx.get("age", 1e9) > 90 or len(ws) > 5:
        return None
    core = [w for w in ws if w not in _FILL and w not in _SMALL and w not in _BIG]
    s = set(core)
    more = s <= {"aur", "more", "again", "even", "phir", "se", "once", "ek", "baar", "still", "bhi", "aur bhi"} and bool(core)
    if last.get("do") == "volume" and "step" in last:
        step = last["step"]
        small = 0.05 if _SMALL & set(ws) else abs(step)
        if more:
            return {"do": "volume", "step": small if step > 0 else -small}
        if re.search(r"zyada ho gaya|too much|not that much|itna nahi|bahut ho gaya|thoda wapas|go back", " ".join(ws)):
            return {"do": "volume", "step": -step / 2}
    if last.get("do") == "next" and (more or s <= {"another", "ek", "aur", "agla"}) and ctx.get("playing"):
        return {"do": "next"}
    return None


def _play(t: str) -> dict | None:
    m = re.search(rf"\b{_PLAY_EN}\b(.*)$", t)
    q = None
    if m:
        q = m.group(1)
        q = re.sub(rf"^(?:\s*(?:{_PLAY_EN}|me|us|some of|some|of|like|the song|a song called|song called|the|a|"
                   rf"something like|something by|songs by|songs of|song|music by|karo|kar do|kardo)\b)+", " ", q)
    else:
        m = re.search(rf"^(.*?)\b{_PLAY_HI}\b[\s.?!]*(?:na|please|yaar|zara)?[\s.?!]*$", t)
        if m:
            q = m.group(1)
            q = re.sub(r"\b(ke|ka|ki) (gaane|gaana|gane|gana|songs?)\b", " ", q)       # "arijit ke gaane"
            q = re.sub(r"\b(gaane|gaana|gane|gana|song|songs|music|wala|wale|koi|kuch|zara|ek|mujhe|hume|"
                       r"please|thoda|sa)\b", " ", q)
    if q is None:
        return None
    q = re.sub(r"\b(on|in|from) (youtube|yt|you tube|the internet|web)\b", " ", q)
    q = re.sub(r"[.?!]", " ", q)
    q = re.sub(r"\b(please|for me|right now|now|yaar|na|zara|if you can|thanks|thank you)\s*$", " ", q)
    q = re.sub(r"\s+", " ", q).strip(" -'")
    if len(q) > 80 or re.search(r"\b(recording|meeting|voicemail|message|note|email|game)\b", q):
        return None
    if re.search(r"\bradio\b", q):
        kind = next((k for k in ("lofi", "jazz", "classical", "indie") if k in q), "default")
        return {"do": "radio", "station": kind}
    if q in _GENERIC:
        return {"do": "radio", "station": "default"}
    kind = re.fullmatch(r"(lofi|jazz|classical|indie)(?: music| songs?)?", q)
    if kind:
        return {"do": "radio", "station": kind.group(1)}
    return {"do": "youtube", "query": q}


def _clause(t: str, ctx: dict) -> dict | None:
    ws = _words(t)
    if not ws or len(ws) > 22:
        return None
    s = set(ws)
    core = [w for w in ws if w not in _FILL]
    playing, paused = bool(ctx.get("playing")), bool(ctx.get("paused"))
    # "Who made you?": the owner asked (26 Nov 2026) that the speaker say who built it,
    # and only when someone asks. About the speaker itself ("you", "this", "ANVE"), not
    # "who made the Taj Mahal".
    if len(ws) <= 12 and re.search(
            r"\bwho(?:'s| is| has)? (?:made|built|build|created|create|designed|developed|invented|make|makes) "
            r"(?:you|u|this(?! \w)|it(?! \w)|anve|the speaker|this speaker|this box|this thing|this device)\b|"
            r"\bwho(?:'s| is| are| was) (?:your|ur) (?:maker|creator|developer|builder|inventor|designer)\b|"
            r"\bwho (?:are|were) you (?:made|built|created|developed) by\b|"
            r"\b(?:tumhe|tumko|tujhe|aapko|isko|ise|isse|tumhein|is speaker ko) kis ?ne (?:banaya|bnaya|create kiya|develop kiya)\b|"
            r"\bkis ?ne (?:banaya|bnaya) (?:hai )?(?:tumhe|tumko|tujhe|aapko|isko|ise|ye speaker|is speaker ko)\b|"
            r"^(?:tumhe |aapko )?kis ?ne (?:banaya|bnaya)(?: hai| tha)?$", t.strip(" .?!")):
        return {"do": "maker"}
    # the time, from the device's own clock
    if len(ws) <= 10 and re.search(r"\b(what(?:'s| is) (?:the )?(?:current )?time|what time is it|tell me (?:the )?time|"
                                   r"current time|time is it|time kya|kitne baje|kitna baja|kya time|samay kya|"
                                   r"time batao|time bata)\b", t) and not re.search(r"\b(in|at) [a-z ]+$", t.strip(" .?!")):
        return {"do": "time"}
    if re.search(r"\b(what(?:'s| is) (?:this |that )?(?:song|playing|track)|which song|what song|song name|name of (?:this|the) song|"
                 r"kaunsa (?:gaana|gana|song)|kaun sa (?:gaana|gana|song)|(?:gaane|gane|song) ka naam|ye kya (?:baj|chal))", t):
        return {"do": "nowplaying"}
    f = _follow_up(ws, ctx)
    if f:
        return f
    v = _volume(ws, t, ctx)
    if v:
        return v
    if _NOT_OURS & s:
        return None
    # stop / pause the music: only when there is music, and only a short order
    if (playing or paused) and _STOP & s and len(core) <= 6:
        other = [w for w in core if w not in _STOP and w not in _MUSIC and w not in ("up", "off", "kijiye", "bhi", "sab")]
        if "bas" in s and not ({"karo", "kar", "band", "ho", "gaya"} & s or len(core) == 1):
            other.append("bas")                               # "bas" is also just "only"
        if not other and playing:
            return {"do": "pause"}
        if not other and paused and not playing:
            return {"do": "pause"}
    if re.search(r"\bturn (it |the music |this )?off\b|\bswitch (it |the music )?off\b", t) and playing:
        return {"do": "pause"}
    if playing and (_NEXT & s or re.search(r"change (the |this )?(song|music|track)|pasand nahi|ye nahi|yeh nahi|not this", t)) \
            and len(core) <= 7 and not re.search(rf"\b{_PLAY_EN}\b .*\b(by|of)\b", t):
        return {"do": "next"}
    if paused and not playing and len(core) <= 4 and _RESUME & s and \
            not [w for w in core if w not in _RESUME and w not in _MUSIC and w not in ("again", "phir", "se", "back", "karo")]:
        return {"do": "resume"}
    return _play(t)


def understand(text: str, ctx: dict | None = None) -> dict | None:
    ctx = ctx or {}
    t = _clean(text)
    if not t or len(t) > 200:
        return None
    # "can you just, just a second. what's the time": the last sentence is the request
    clauses = [c.strip() for c in re.split(r"(?<=[.?!])\s+", t) if c.strip(" .?!")]
    if len(clauses) > 1:
        got = _clause(clauses[-1], ctx)
        if got:
            return got
    return _clause(t, ctx)
