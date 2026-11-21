"""The whole thing together: messages in, frames out, over the real sockets."""
import asyncio
import base64
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from lumina.assistant_client import AssistantLEDs
from lumina.audio.audio_analyser import Bands, Smoother, loudness
from lumina.audio.media_player import MediaPlayer
from lumina.controller import Controller
from lumina.hardware.led_simulator import Simulator
from lumina.integrations import assistant_adapter
from lumina.integrations.home_assistant import HomeAssistant
from lumina.integrations.mqtt import MqttBridge, to_message
from lumina.tests.helpers import Clock, controller, online, run, settings


class Inside(unittest.TestCase):
    def test_starts_by_greeting_then_waits_for_the_assistant(self):
        c, clock = controller()
        c.step()
        self.assertEqual(c.comp._visual, "STARTING")
        run(c, clock, 2.0)
        self.assertEqual(c.state.visual(), "CONNECTING")        # running is not the same as ready
        online(c, clock)
        self.assertEqual(c.state.visual(), "READY")

    def test_messages_drive_the_lights(self):
        c, clock = controller()
        online(c, clock)
        c.bus.publish({"type": "event", "name": "wake_word.detected", "source": "assistant"})
        c.step()
        self.assertEqual(c.state.visual(), "WAKE_WORD_DETECTED")
        c.bus.publish({"type": "state", "state": "listening", "source": "assistant"})
        run(c, clock, 1.0)
        self.assertEqual(c.state.visual(), "LISTENING")
        c.bus.publish({"type": "event", "name": "tts.started", "source": "assistant"})
        c.bus.publish({"type": "nonsense"})
        c.bus.publish({"type": "event", "name": 42})
        run(c, clock, 0.5)
        self.assertEqual(c.state.visual(), "SPEAKING")

    def test_levels_reach_the_right_animation(self):
        c, clock = controller()
        online(c, clock, voice="listening")
        for _ in range(30):
            c.bus.publish({"type": "level", "mic": 0.9})
            run(c, clock, 1 / 30)
        self.assertTrue(c.comp.levels.mic_real)
        self.assertGreater(c.comp.levels.mic, 0.6)
        self.assertFalse(c.comp.levels.out_real)                # the reply's level is something else
        run(c, clock, 2.0)                                      # the level stops arriving
        self.assertFalse(c.comp.levels.mic_real)
        self.assertLess(c.comp.levels.mic, 0.1)

    def test_the_voice_and_the_music_wait_until_they_are_heard(self):
        c, clock = controller(voice_delay_s=0.5, music_delay_s=0.2)
        online(c, clock, speech="speaking")
        c.bus.publish({"type": "level", "out": 0.9})
        c.bus.publish({"type": "level", "bass": 0.8, "mid": 0.5, "treble": 0.2, "beat": 1.0})
        c.bus.publish({"type": "level", "mic": 0.7})
        run(c, clock, 0.1)
        lv = c.comp.levels
        self.assertTrue(lv.mic_real)                            # the room: at once
        self.assertFalse(lv.out_real or lv.music_real)
        run(c, clock, 0.2)
        self.assertTrue(lv.music_real)
        self.assertEqual(lv.bass, 0.8)
        self.assertFalse(lv.out_real)
        run(c, clock, 0.3)
        self.assertTrue(lv.out_real)
        self.assertGreater(lv.out, 0.4)

    def test_without_a_delay_nothing_waits(self):
        c, clock = controller()
        online(c, clock, speech="speaking")
        c.bus.publish({"type": "level", "out": 0.9})
        c.step()
        self.assertTrue(c.comp.levels.out_real)

    def test_an_unchanged_picture_is_not_sent_again(self):
        c, clock = controller()
        online(c, clock, fault="error")
        c.state.demo("OFF", 60)
        run(c, clock, 1.0)
        shown = c.shown
        run(c, clock, 3.0)
        self.assertEqual(c.shown, shown)

    def test_a_dead_strip_does_not_stop_anything(self):
        class Broken:
            name, calls, closed = "spi", 0, False

            def show(self, wire):
                self.calls += 1
                raise OSError("no such device")

            def close(self):
                self.closed = True

        clock = Clock()
        s = settings()
        broken = Broken()
        c = Controller(s, strip=broken, clock=clock)
        online(c, clock)
        run(c, clock, 1.0)
        self.assertIsInstance(c.strip, Simulator)
        self.assertTrue(broken.closed)
        self.assertIn("stopped answering", c.status()["strip"]["problem"])
        c.bus.publish({"type": "state", "state": "listening", "source": "assistant"})
        run(c, clock, 0.5)
        self.assertEqual(c.state.visual(), "LISTENING")

    def test_setup_is_read_from_the_marker_file(self):
        marker = os.path.join(tempfile.mkdtemp(), "provisioned")
        c, clock = controller(setup_marker=marker)
        run(c, clock, 3.0)
        self.assertEqual(c.state.visual(), "SETUP_REQUIRED")
        open(marker, "w").close()
        run(c, clock, 3.0)
        self.assertEqual(c.state.visual(), "CONNECTING")
        online(c, clock)
        os.remove(marker)
        run(c, clock, 3.0)
        self.assertEqual(c.state.visual(), "SETUP_REQUIRED")

    def test_settings_apply_at_once_and_status_tells_all(self):
        c, clock = controller()
        online(c, clock)
        self.assertEqual(c.apply_settings({"brightness": 0.05, "theme": "purple-ai", "animations": {"READY": "breathe"}}),
                         ["brightness", "theme", "animations"])
        run(c, clock, 1.0)
        st = c.status()
        self.assertEqual((st["visual"], st["animation"], st["settings"]["theme"]), ("READY", "breathe", "purple-ai"))
        self.assertEqual(st["strip"]["driver"], "simulator")
        self.assertLessEqual(max(x for px in c.strip.last for x in px), 14)
        json.dumps(st)

    def test_a_blink_asked_for_by_the_cloud(self):
        c, clock = controller()
        online(c, clock)
        steps = [[1.0, 150], [0.0, 150]]
        c.bus.publish({"type": "pattern", "name": "fast_blink", "steps": steps, "smooth": False, "seconds": 2, "source": "boxlink"})
        levels = []
        for _ in range(30):
            clock.step(1 / 30)
            c.step()
            levels.append(max(x for px in c.strip.last for x in px))
        self.assertEqual(c.status()["pattern"], "fast_blink")
        self.assertGreater(max(levels), 20)                     # the whole strip lights
        self.assertGreater(sum(1 for a, b in zip(levels, levels[1:]) if abs(a - b) > 15), 3)   # and it blinks
        self.assertEqual(c.state.visual(), "READY")             # the assistant's state is untouched
        run(c, clock, 2.0)
        self.assertIsNone(c.status()["pattern"])                # it ends by itself
        self.assertLess(max(x for px in c.strip.last for x in px), 20)
        c.bus.publish({"type": "pattern", "name": "solid", "steps": [[1.0, 1000]], "seconds": 60})
        c.bus.publish({"type": "event", "name": "error", "source": "assistant"})
        run(c, clock, 3.0)
        self.assertEqual(c.state.visual(), "ERROR")
        dark = sum(1 for _ in range(60) if (clock.step(1 / 30), c.step(), max(x for px in c.strip.last for x in px) < 12)[2])
        self.assertGreater(dark, 10)                            # the error's pauses still show: nothing is hidden
        for bad in ({"type": "pattern", "steps": "x", "seconds": 5}, {"type": "pattern", "steps": [[2, "a"]], "seconds": 5},
                    {"type": "pattern", "name": "off", "steps": [], "seconds": 0}):
            c.bus.publish(bad)
            c.step()
        self.assertIsNone(c.status()["pattern"])

    def test_fades_out_on_shutdown(self):
        c, clock = controller()
        online(c, clock, microphone="muted")
        run(c, clock, 1.0)
        self.assertTrue(any(any(px) for px in c.strip.last))
        c.state.emit("assistant.shutdown", source="lumina")
        run(c, clock, 1.5)
        self.assertFalse(any(any(px) for px in c.strip.last))


class Sound(unittest.TestCase):
    def test_smoother_rises_fast_falls_slowly_and_ignores_noise(self):
        s = Smoother(0.6, 0.1, 0.05)
        for _ in range(4):
            s.feed(1.0)
        self.assertGreater(s.value, 0.9)
        s.feed(0.0)
        self.assertGreater(s.value, 0.8)
        quiet = Smoother(0.6, 0.1, 0.05)
        for _ in range(50):
            quiet.feed(0.03)
        self.assertEqual(quiet.value, 0.0)
        quiet.feed(float("nan"))
        self.assertEqual(quiet.value, 0.0)

    def test_loudness_of_real_sound(self):
        import math
        import struct
        tone = b"".join(struct.pack("<h", int(12000 * math.sin(i * 0.2))) for i in range(1600))
        self.assertGreater(loudness(tone), 0.7)
        self.assertEqual(loudness(bytes(3200)), 0.0)
        self.assertEqual(loudness(b""), 0.0)

    def test_bands_tell_bass_from_treble(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is not here")
        t = np.arange(1024) / 16000
        bass = (0.6 * np.sin(2 * np.pi * 80 * t) * 32767).astype("<i2").tobytes()
        high = (0.6 * np.sin(2 * np.pi * 5000 * t) * 32767).astype("<i2").tobytes()
        b = Bands(16000)
        got = [b.feed(bass) for _ in range(6)][-1]
        self.assertGreater(got["bass"], got["treble"] + 0.3)
        b = Bands(16000)
        got = [b.feed(high) for _ in range(6)][-1]
        self.assertGreater(got["treble"], got["bass"] + 0.3)
        b = Bands(16000)
        [b.feed(bytes(2048)) for _ in range(20)]
        self.assertEqual(b.feed(bass)["beat"], 1.0)                 # silence, then a hit

    def test_quiet_music_is_turned_up_and_silence_is_not(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is not here")
        t = np.arange(1024) / 16000
        quiet = (0.006 * np.sin(2 * np.pi * 80 * t) * 32767).astype("<i2").tobytes()     # about -47 dBFS
        b = Bands(16000)
        got = [b.feed(quiet) for _ in range(10)][-1]
        self.assertGreater(got["bass"], 0.5)
        self.assertGreater(b.gain_db, 20)
        b = Bands(16000)
        got = [b.feed(bytes(2048)) for _ in range(10)][-1]
        self.assertEqual((got["bass"], got["mid"], got["treble"], b.gain_db), (0.0, 0.0, 0.0, 0.0))
        loud = (0.6 * np.sin(2 * np.pi * 80 * t) * 32767).astype("<i2").tobytes()
        b = Bands(16000)
        b.feed(loud)
        self.assertEqual(b.gain_db, 0.0)                            # loud music is left as it is

    def test_a_recorder_that_cannot_run_is_not_restarted_every_frame(self):
        import sys
        from lumina.audio.speaker import Loopback
        lb = Loopback(lambda m: None, command=[sys.executable, "-c", "pass"])
        for _ in range(60):
            lb.want(True)
            time.sleep(0.02)
        self.assertEqual(lb.starts, 1)
        self.assertEqual(lb.problem, "the recorder stopped")
        lb.close()

    def test_media_player_words(self):
        p = MediaPlayer()
        self.assertEqual([p.update(w) for w in ("idle", "playing", "playing", "paused", "playing", "off", "what?")],
                         [None, "media.started", None, "media.paused", "media.resumed", "media.stopped", None])


class Bridges(unittest.TestCase):
    def test_datagrams(self):
        self.assertEqual(assistant_adapter.parse(b'{"type":"event","name":"ai.thinking"}')["source"], "assistant")
        for bad in (b"", b"hello", b"[1,2]", b'{"type":"shutdown"}', b"\xff\xfe", b"x" * 5000):
            self.assertIsNone(assistant_adapter.parse(bad))
        self.assertEqual(assistant_adapter.parse(b'{"type":"event","name":"x","meta":"oops"}')["meta"], {})

    def test_mqtt_messages(self):
        self.assertEqual(to_message("lumina", "lumina/state", b"listening")["state"], "listening")
        self.assertEqual(to_message("lumina", "lumina/event", b"ai.thinking")["name"], "ai.thinking")
        self.assertEqual(to_message("lumina", "lumina/event", b'{"name":"error","meta":{"code":"7"}}')["meta"], {"code": "7"})
        for topic, payload in (("other/state", b"x"), ("lumina/event", b"{broken"), ("lumina/event", b'{"nom":1}'), ("lumina/state", b"")):
            self.assertIsNone(to_message("lumina", topic, payload))

    def test_mqtt_bridge_with_a_stand_in_broker(self):
        class Client:
            def __init__(self):
                self.published, self.subscribed, self.started = [], None, False

            def connect_async(self, host, port): self.where = (host, port)
            def loop_start(self): self.started = True
            def loop_stop(self): self.started = False
            def disconnect(self): pass
            def subscribe(self, topics): self.subscribed = topics
            def publish(self, topic, payload, retain=False): self.published.append((topic, json.loads(payload), retain))

        c, clock = controller(mqtt={"enabled": True, "prefix": "home/lumina"})
        client = Client()
        bridge = MqttBridge(c.settings.values, c.bus.publish, client=client)
        self.assertTrue(bridge.start())
        client.on_connect(client, None, None, 0)
        self.assertEqual(client.subscribed[0][0], "home/lumina/event")
        c.extras.append(bridge)
        online(c, clock)

        class Msg:
            topic, payload = "home/lumina/state", b"listening"

        client.on_message(client, None, Msg())
        run(c, clock, 0.2)
        self.assertEqual(c.state.visual(), "LISTENING")
        topic, body, retain = client.published[-1]
        self.assertEqual((topic, body["visual"], retain), ("home/lumina/status", "LISTENING", True))
        bridge.stop()

    def test_home_assistant_gives_music_only(self):
        answers = [{"state": "idle"}, {"state": "playing"}, {"state": "paused"}, RuntimeError("down"), {"state": "playing"}, {"state": "off"}]

        def fetch():
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

        c, clock = controller()
        online(c, clock)
        ha = HomeAssistant(c.settings.values, c.bus.publish, fetch=fetch)
        seen = []
        for _ in range(6):
            ok = ha.poll_once()
            run(c, clock, 0.1)
            seen.append((ok, c.state.visual()))
        self.assertEqual(seen, [(True, "READY"), (True, "MUSIC_PLAYING"), (True, "MUSIC_PAUSED"), (False, "MUSIC_PAUSED"),
                                (True, "MUSIC_PLAYING"), (True, "READY")])
        self.assertFalse(HomeAssistant(c.settings.values, c.bus.publish).start())       # not configured: says so

    def test_the_client_never_raises(self):
        leds = AssistantLEDs(port=9)                        # nobody listens there
        for call in (leds.ready, leds.listening, leds.thinking, leds.speaking, leds.music_playing, leds.muted,
                     lambda: leds.error("x" * 500), lambda: leds.level(mic=0.5), lambda: leds.status(voice="idle"),
                     lambda: leds.set_state("ready"), lambda: leds.emit("ai.searching"), lambda: leds.feed_pcm(bytes(640))):
            call()
        leds.speaking(audio_stream=lambda: 1 / 0)           # a source that fails
        time.sleep(0.15)
        leds.close()
        self.assertFalse(leds.ready())
        self.assertFalse(leds.level(mic=0.1))


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OverTheWire(unittest.TestCase):
    """LUMINA really running, spoken to the way the assistant and a browser do."""

    def setUp(self):
        self.udp, self.http = free_port(socket.SOCK_DGRAM), free_port()
        s = settings(events={"host": "127.0.0.1", "port": self.udp}, dashboard={"enabled": True, "host": "127.0.0.1", "port": self.http})
        self.c = Controller(s, strip=Simulator(60))
        self.loop = asyncio.new_event_loop()
        self.task = None

        def serve():
            asyncio.set_event_loop(self.loop)
            self.task = self.loop.create_task(self.c.run())
            try:
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.wait(lambda: self.c.dashboard is not None)
        self.leds = AssistantLEDs(port=self.udp)

    def tearDown(self):
        self.leds.close()
        self.loop.call_soon_threadsafe(self.task.cancel)
        self.thread.join(5)
        self.assertTrue(self.c.strip.closed)                    # the strip is let go of, whatever happened

    def wait(self, what, seconds=4.0):
        end = time.time() + seconds
        while time.time() < end:
            if what():
                return True
            time.sleep(0.02)
        self.fail("did not happen in time")

    def get(self, path, key=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.http}{path}", headers={"X-Lumina-Key": key} if key else {})
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")

    def post(self, path, body, ctype="application/json"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.http}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_the_assistant_talks_and_the_lights_follow(self):
        self.leds.status(connection="connected", setup="complete")
        self.wait(lambda: self.c.state.real_visual() == "READY")
        self.leds.wake_word_detected()
        self.leds.listening()
        self.wait(lambda: self.c.state.visual() == "LISTENING")
        self.leds.thinking()
        self.wait(lambda: self.c.state.visual() == "THINKING")
        self.leds.speaking(audio_stream=lambda: 0.8)
        self.wait(lambda: self.c.state.visual() == "SPEAKING" and self.c.comp.levels.out_real)
        self.leds.ready()
        self.wait(lambda: self.c.state.visual() == "READY")
        self.leds.music_playing()
        self.wait(lambda: self.c.state.visual() == "MUSIC_PLAYING")
        self.leds.muted()
        self.wait(lambda: self.c.state.visual() == "MUTED")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as junk:
            junk.sendto(b"\x00garbage", ("127.0.0.1", self.udp))
        self.leds.error("Connection failed", code="net")
        self.wait(lambda: self.c.state.visual() == "ERROR")
        self.assertEqual(self.c.state.view()["error"]["message"], "Connection failed")

    def test_dashboard_files_status_and_commands(self):
        code, body, ctype = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"LUMINA", body)
        self.assertIn("text/html", ctype)
        for path in ("/app.js", "/styles.css"):
            self.assertEqual(self.get(path)[0], 200)
        for path in ("/../config.py", "/nothing.html", "/dashboard.key"):
            self.assertRaises(urllib.error.HTTPError, self.get, path)
        code, body, _ = self.get("/api/status")
        st = json.loads(body)
        self.assertEqual(code, 200)
        self.assertIn(st["visual"], ("STARTING", "CONNECTING"))
        self.assertEqual(self.post("/api/event", {"name": "assistant.ready"})[0], 200)
        self.wait(lambda: self.c.state.real_visual() == "READY")
        self.assertEqual(self.post("/api/state", {"state": "thinking"})[0], 200)
        self.wait(lambda: self.c.state.visual() == "THINKING")
        self.assertEqual(self.post("/api/demo", {"state": "MUSIC_PLAYING", "seconds": 5})[0], 200)
        self.wait(lambda: self.c.state.view()["demo"] and self.c.state.visual() == "MUSIC_PLAYING")
        self.assertEqual(self.c.state.real_visual(), "THINKING")
        self.assertEqual(self.post("/api/demo", {"off": True})[0], 200)
        self.assertEqual(self.post("/api/demo", {"state": "DISCO"})[0], 422)
        code, out = self.post("/api/settings", {"brightness": 0.1, "strip": {"driver": "spi"}})
        self.assertEqual((code, out["changed"]), (200, ["brightness"]))
        self.assertEqual(self.c.settings.values["strip"]["driver"], "simulator")
        self.assertEqual(self.post("/api/event", {"name": "x"}, ctype="text/plain")[0], 415)
        self.assertEqual(self.post("/api/launch", {})[0], 422)

    def test_other_machines_need_the_key(self):
        d = self.c.dashboard
        self.assertFalse(d._allowed("192.168.1.50", None))
        self.assertTrue(d._allowed("192.168.1.50", d.key))
        for _ in range(5):
            self.assertFalse(d._allowed("192.168.1.66", "guess"))
        self.assertFalse(d._allowed("192.168.1.66", d.key))        # locked out for a while after five wrong keys
        self.assertTrue(d._allowed("192.168.1.50", d.key))

    def test_websocket_frames_and_a_browser_that_vanishes(self):
        def connect(origin="", expect=b"101"):
            s = socket.create_connection(("127.0.0.1", self.http), timeout=4)
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall((f"GET /ws HTTP/1.1\r\nHost: pi.local:8742\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       + (f"Origin: {origin}\r\n" if origin else "")
                       + f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += s.recv(4096)
            self.assertIn(expect, buf.split(b"\r\n")[0])
            return s, buf.split(b"\r\n\r\n", 1)[1]

        # a page from another site, in a browser on this very machine, is turned away
        connect(origin="http://evil.example", expect=b"403")[0].close()
        connect(origin="http://pi.local:8742")[0].close()            # the dashboard's own page is not

        def read(s, buf):
            while True:
                if len(buf) >= 2:
                    n, off = buf[1] & 0x7F, 2
                    if n == 126 and len(buf) >= 4:
                        n, off = int.from_bytes(buf[2:4], "big"), 4
                    elif n == 126:
                        n = None
                    if n is not None and len(buf) >= off + n:
                        return json.loads(buf[off:off + n]), buf[off + n:]
                buf += s.recv(65536)

        def send(s, obj):
            data = json.dumps(obj).encode()
            mask = os.urandom(4)
            s.sendall(bytes([0x81, 0x80 | len(data)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

        s, buf = connect()
        first, buf = read(s, buf)
        self.assertEqual(first["t"], "s")
        send(s, {"type": "event", "name": "assistant.ready"})
        send(s, {"type": "state", "state": "speaking"})
        kinds = set()
        for _ in range(40):
            m, buf = read(s, buf)
            kinds.add(m["t"])
            if m["t"] == "f":
                self.assertEqual(len(m["px"]), 360)
            if {"s", "f", "reply"} <= kinds and self.c.state.visual() == "SPEAKING":
                break
        self.assertTrue({"s", "f", "reply"} <= kinds)
        s.close()                                               # the browser is gone, mid-stream
        other, buf2 = connect()
        self.assertEqual(read(other, buf2)[0]["t"], "s")
        other.close()
        self.leds.listening()
        self.wait(lambda: self.c.state.visual() == "LISTENING")
        self.wait(lambda: not self.c.dashboard.clients)


if __name__ == "__main__":
    unittest.main()
