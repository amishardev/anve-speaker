"""The state engine: every transition the brief lists, and everything that can go wrong."""
import threading
import unittest

from lumina.state_manager import EVENTS, STATES, VISUALS
from lumina.tests.helpers import manager


def walk(m, *events):
    seen = []
    for e in events:
        m.emit(e)
        seen.append(m.visual())
    return seen


class Transitions(unittest.TestCase):
    def test_setup_to_ready(self):
        m, _ = manager()
        self.assertEqual(walk(m, "setup.required", "setup.started", "setup.completed", "assistant.ready"),
                         ["SETUP_REQUIRED", "SETUP_IN_PROGRESS", "CONNECTING", "READY"])

    def test_wake_then_listening(self):
        m, clock = manager()
        m.emit("assistant.ready")
        m.emit("wake_word.detected")
        self.assertEqual(m.visual(), "WAKE_WORD_DETECTED")
        m.emit("voice.listening.started")
        self.assertEqual(m.visual(), "WAKE_WORD_DETECTED")      # the pulse finishes first
        clock.step(0.6)
        m.tick()
        self.assertEqual(m.visual(), "LISTENING")

    def test_wake_without_listening_falls_back(self):
        m, clock = manager()
        m.emit("assistant.ready")
        m.emit("wake_word.detected")
        clock.step(0.6)
        m.tick()
        self.assertEqual(m.visual(), "WAKE_WORD_DETECTED")      # held: listening usually begins now
        clock.step(2.5)
        m.tick()
        self.assertEqual(m.visual(), "READY")                   # it never did: back to rest

    def test_wake_holds_until_listening_begins(self):
        """No flash of the resting look between the wake pulse and listening."""
        m, clock = manager()
        m.emit("assistant.ready")
        before = len(m.history)
        m.emit("wake_word.detected")
        seen = []
        for _ in range(10):
            clock.step(0.1)
            m.tick()
            seen.append(m.visual())
        m.emit("voice.listening.started")
        seen.append(m.visual())
        self.assertEqual(seen, ["WAKE_WORD_DETECTED"] * 10 + ["LISTENING"])
        self.assertEqual([r["new"] for r in list(m.history)[before:]], ["WAKE_WORD_DETECTED", "LISTENING"])
        m.emit("tts.started")                                   # the reply, soon after: never hidden
        self.assertEqual(m.visual(), "SPEAKING")
        m.emit("tts.finished")
        self.assertEqual(m.visual(), "READY")

    def test_a_whole_request(self):
        m, _ = manager()
        m.emit("assistant.ready")
        self.assertEqual(walk(m, "voice.listening.started", "request.processing", "ai.thinking", "ai.searching",
                              "tool.executing", "tts.started", "tts.finished"),
                         ["LISTENING", "PROCESSING", "THINKING", "SEARCHING", "TOOL_EXECUTING", "SPEAKING", "READY"])

    def test_music(self):
        m, _ = manager()
        m.emit("assistant.ready")
        self.assertEqual(walk(m, "media.started", "media.paused", "media.resumed", "media.stopped"),
                         ["MUSIC_PLAYING", "MUSIC_PAUSED", "MUSIC_PLAYING", "READY"])
        self.assertEqual(walk(m, "media.started", "media.finished"), ["MUSIC_PLAYING", "READY"])

    def test_disconnect_and_back(self):
        m, _ = manager()
        m.emit("assistant.ready")
        self.assertEqual(walk(m, "assistant.disconnected", "assistant.connecting", "assistant.connected"),
                         ["DISCONNECTED", "CONNECTING", "READY"])

    def test_mute_and_back(self):
        m, _ = manager()
        m.emit("assistant.ready")
        self.assertEqual(walk(m, "microphone.muted", "microphone.unmuted"), ["MUTED", "READY"])

    def test_set_state_names(self):
        m, _ = manager()
        m.set_state("ready")
        for name, visual in (("listening", "LISTENING"), ("processing", "PROCESSING"), ("thinking", "THINKING"),
                             ("searching", "SEARCHING"), ("tool_executing", "TOOL_EXECUTING"), ("speaking", "SPEAKING"),
                             ("ready", "READY"), ("music_playing", "MUSIC_PLAYING"), ("music_paused", "MUSIC_PAUSED"),
                             ("music_stopped", "READY"), ("muted", "MUTED"), ("unmuted", "READY"),
                             ("disconnected", "DISCONNECTED"), ("connecting", "CONNECTING"), ("ready", "READY"),
                             ("error", "ERROR")):
            self.assertTrue(m.set_state(name), name)
            self.assertEqual(m.visual(), visual, name)

    def test_every_event_and_state_name_is_known(self):
        m, _ = manager()
        for e in EVENTS:
            m.emit(e)
            self.assertIn(m.visual(), VISUALS)
            m.emit("microphone.unmuted")
            m.emit("assistant.ready")
        for s in STATES.values():
            self.assertIn(s, EVENTS)

    def test_transition_records(self):
        m, clock = manager()
        m.emit("assistant.ready")
        clock.step(1)
        m.emit("voice.listening.started")
        r = m.history[-1]
        self.assertEqual((r["previous"], r["new"], r["cause"]), ("READY", "LISTENING", "voice.listening.started"))
        self.assertEqual(r["time"], clock.t)
        self.assertIn("meta", r)


class Priority(unittest.TestCase):
    def test_error_beats_everything(self):
        m, _ = manager()
        m.emit("assistant.ready")
        for e in ("media.started", "voice.listening.started", "microphone.muted", "error"):
            m.emit(e, code="E42", message="went wrong")
        self.assertEqual(m.visual(), "ERROR")
        self.assertEqual(m.view()["error"], {"code": "E42", "message": "went wrong"})
        m.emit("error.cleared")
        self.assertEqual(m.visual(), "MUTED")

    def test_listening_never_shows_while_muted(self):
        m, _ = manager()
        m.emit("assistant.ready")
        m.emit("microphone.muted")
        self.assertFalse(m.emit("voice.listening.started"))
        self.assertFalse(m.emit("wake_word.detected"))
        self.assertEqual(m.visual(), "MUTED")
        m.snapshot({"connection": "connected", "microphone": "muted", "voice": "listening"})
        self.assertEqual(m.visual(), "MUTED")
        self.assertEqual(m.dims["voice"], "idle")
        self.assertGreaterEqual(m.counts["invalid"], 3)

    def test_muted_reply_shows_between_red_ends(self):
        m, _ = manager()
        m.emit("assistant.ready")
        m.emit("microphone.muted")
        m.emit("tts.started")
        self.assertEqual(m.visual(), "SPEAKING")
        self.assertTrue(m.view()["privacy"])
        m.emit("tts.finished")
        self.assertEqual(m.visual(), "MUTED")
        self.assertFalse(m.view()["privacy"])

    def test_muted_strict(self):
        m, _ = manager(muted_overlay=False)
        m.emit("assistant.ready")
        m.emit("microphone.muted")
        m.emit("tts.started")
        self.assertEqual(m.visual(), "MUTED")

    def test_music_under_thinking(self):
        m, _ = manager()
        m.emit("assistant.ready")
        m.emit("media.started")
        m.emit("ai.thinking")
        self.assertEqual(m.visual(), "THINKING")
        self.assertTrue(m.view()["media_under"])
        m.emit("response.generated")
        self.assertEqual(m.visual(), "MUSIC_PLAYING")

    def test_listening_beats_music_and_speaking_beats_music(self):
        m, _ = manager()
        m.emit("assistant.ready")
        m.emit("media.started")
        m.emit("voice.listening.started")
        self.assertEqual(m.visual(), "LISTENING")
        m.emit("tts.started")
        self.assertEqual(m.visual(), "SPEAKING")

    def test_setup_required_beats_connection(self):
        m, _ = manager()
        m.emit("assistant.connected")
        m.emit("setup.required")
        self.assertEqual(m.visual(), "SETUP_REQUIRED")

    def test_progress_is_only_what_was_reported(self):
        m, _ = manager()
        m.emit("setup.started")
        self.assertIsNone(m.view()["progress"])
        m.emit("setup.progress", percent=25)
        self.assertEqual(m.view()["progress"], 0.25)
        m.emit("setup.progress", percent="soon")
        self.assertIsNone(m.view()["progress"])


class Trouble(unittest.TestCase):
    def test_unknown_things_are_refused_and_logged(self):
        m, _ = manager()
        m.emit("assistant.ready")
        self.assertFalse(m.emit("coffee.ready"))
        self.assertFalse(m.set_state("banana"))
        self.assertFalse(m.snapshot({"voice": "yodelling"}))
        self.assertEqual(m.visual(), "READY")
        self.assertEqual(m.counts["invalid"], 3)
        self.assertTrue(all("refused" in r["remark"] for r in list(m.log)[-3:]))

    def test_duplicates_change_nothing(self):
        m, _ = manager()
        m.emit("assistant.ready")
        m.emit("voice.listening.started")
        n = len(m.history)
        for _ in range(5):
            m.emit("voice.listening.started")
        self.assertEqual(len(m.history), n)
        self.assertEqual(m.counts["duplicates"], 5)

    def test_out_of_order_finish_is_harmless(self):
        m, _ = manager()
        m.emit("assistant.ready")
        self.assertTrue(m.emit("tts.finished"))
        self.assertTrue(m.emit("media.paused"))
        self.assertEqual(m.visual(), "MUSIC_PAUSED")

    def test_a_missed_event_does_not_stick(self):
        for event, limit in (("voice.listening.started", 45), ("ai.thinking", 120), ("tts.started", 180)):
            m, clock = manager()
            m.emit("assistant.ready")
            m.emit(event)
            clock.step(limit - 1)
            m.tick()
            self.assertNotEqual(m.visual(), "READY", event)
            clock.step(2)
            m.tick()
            self.assertEqual(m.visual(), "READY", event)
            self.assertEqual(m.counts["timeouts"], 1)

    def test_status_keeps_a_long_state_alive(self):
        m, clock = manager()
        for _ in range(200):                       # 400 s of thinking, reported every 2 s
            m.snapshot({"connection": "connected", "setup": "complete", "processing": "thinking"})
            clock.step(2)
            m.tick()
        self.assertEqual(m.visual(), "THINKING")

    def test_status_is_the_authority(self):
        m, _ = manager()
        m.emit("assistant.ready")
        m.emit("voice.listening.started")
        m.snapshot({"connection": "connected", "voice": "idle", "media": "playing"})
        self.assertEqual(m.visual(), "MUSIC_PLAYING")

    def test_before_anything_reports(self):
        m, clock = manager(startup_grace=90)
        self.assertEqual(m.real_visual(), "CONNECTING")
        clock.step(91)
        m.tick()
        self.assertEqual(m.visual(), "DISCONNECTED")

    def test_assistant_stops_and_restarts(self):
        m, clock = manager(assistant_timeout=8)
        m.snapshot({"connection": "connected", "setup": "complete"})
        self.assertEqual(m.visual(), "READY")
        clock.step(9)
        m.tick()
        self.assertEqual(m.visual(), "DISCONNECTED")
        m.snapshot({"connection": "connecting", "setup": "complete"})
        self.assertEqual(m.visual(), "CONNECTING")
        m.snapshot({"connection": "connected", "setup": "complete"})
        self.assertEqual(m.visual(), "READY")

    def test_shutdown_and_restart(self):
        m, _ = manager()
        m.emit("assistant.ready")
        m.emit("assistant.shutdown")
        self.assertEqual(m.visual(), "SHUTTING_DOWN")
        self.assertFalse(m.emit("voice.listening.started"))
        m.emit("assistant.ready")
        self.assertEqual(m.visual(), "READY")

    def test_many_at_once(self):
        m, _ = manager()
        m.emit("assistant.ready")
        events = ["voice.listening.started", "ai.thinking", "tts.started", "tts.finished", "media.started",
                  "media.paused", "microphone.muted", "microphone.unmuted", "error", "error.cleared", "wake_word.detected"]
        errors = []

        def hammer(k):
            try:
                for i in range(400):
                    m.emit(events[(i * 7 + k) % len(events)])
                    m.tick()
                    m.view()
            except Exception as e:                    # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertIn(m.visual(), VISUALS)
        self.assertEqual(m.visual(), m.demo_state or m.real_visual())


class Demo(unittest.TestCase):
    def test_demo_shows_but_does_not_change_the_truth(self):
        m, clock = manager()
        m.emit("assistant.ready")
        m.emit("media.started")
        self.assertTrue(m.demo("SPEAKING", 10))
        v = m.view()
        self.assertEqual((v["visual"], v["real"], v["demo"], v["dims"]["speech"]), ("SPEAKING", "MUSIC_PLAYING", True, "idle"))
        self.assertTrue(m.history[-1]["demo"])
        clock.step(11)
        m.tick()
        self.assertEqual((m.visual(), m.view()["demo"]), ("MUSIC_PLAYING", False))

    def test_real_things_end_a_demo(self):
        for event, visual in (("wake_word.detected", "WAKE_WORD_DETECTED"), ("voice.listening.started", "LISTENING"),
                              ("microphone.muted", "MUTED"), ("error", "ERROR"), ("assistant.disconnected", "DISCONNECTED")):
            m, _ = manager()
            m.emit("assistant.ready")
            m.demo("MUSIC_PLAYING", 60)
            m.emit(event)
            self.assertEqual(m.visual(), visual, event)
            self.assertFalse(m.view()["demo"])

    def test_a_standing_error_does_not_block_demos(self):
        """The microphone has been unplugged for days and the assistant says so every
        second: looks can still be tried. A NEW problem still ends the demo."""
        m, clock = manager()
        status = {"connection": "connected", "setup": "complete", "fault": "hardware"}
        m.snapshot(status)
        m.demo("LISTENING", 20)
        for _ in range(5):
            clock.step(1)
            m.snapshot(status)
            m.emit("hardware.error")
            m.tick()
        v = m.view()
        self.assertEqual((v["visual"], v["real"], v["demo"]), ("LISTENING", "ERROR", True))
        m.snapshot(dict(status, microphone="muted"))
        self.assertEqual((m.visual(), m.view()["demo"]), ("ERROR", False))

    def test_demo_off_and_unknown(self):
        m, _ = manager()
        m.emit("assistant.ready")
        self.assertFalse(m.demo("DISCO"))
        m.demo("ERROR")
        m.demo(None)
        self.assertEqual(m.visual(), "READY")


if __name__ == "__main__":
    unittest.main()
