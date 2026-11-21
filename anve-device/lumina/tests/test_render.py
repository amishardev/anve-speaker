"""What actually reaches the LEDs: every state draws, stays within the limits, and the
states that must be told apart can be."""
import json
import unittest

from lumina.compositor import Compositor
from lumina.config import DEFAULTS, Settings, themes
from lumina.hardware.led_simulator import Simulator
from lumina.hardware.pi5neo_driver import encode, open_strip
from lumina.state_manager import VISUALS
from lumina.states import RENDERERS, VARIANTS
from lumina.tests.helpers import settings


def frames(visual, seconds=3.0, fps=30, s=None, view=None, levels=None):
    s = s or settings()
    comp = Compositor(s.values, s.colours())
    for k, v in (levels or {}).items():
        setattr(comp.levels, k, v)
    out = []
    for i in range(int(seconds * fps)):
        f = comp.render(dict({"visual": visual, "progress": None}, **(view or {})), 50.0 + i / fps)
        out.append((f, comp.output(f)[0]))
    return out, comp


def lit(frame, floor=0.05):
    return sum(1 for c in frame if max(c) > floor)


class EveryState(unittest.TestCase):
    def test_each_state_has_a_renderer(self):
        self.assertEqual(set(RENDERERS), set(VISUALS))
        for visual, names in VARIANTS.items():
            self.assertIn(visual, RENDERERS)
            self.assertTrue(names)

    def test_frames_are_valid_in_every_theme_and_variant(self):
        for theme in themes():
            s = settings(theme=theme)
            for visual in VISUALS:
                for variant in VARIANTS.get(visual, [""]):
                    s.update({"animations": {visual: variant}})
                    got, _ = frames(visual, 1.0, s=s, levels={"mic": 0.7, "mic_real": True, "out": 0.6, "out_real": True})
                    for f, wire in got:
                        self.assertEqual(len(f), 60)
                        self.assertEqual(len(wire), 60)
                        self.assertTrue(all(0.0 <= x <= 1.0 for c in f for x in c), (theme, visual, variant))
                        self.assertTrue(all(0 <= x <= 255 for c in wire for x in c))

    def test_brightness_never_passes_the_limit(self):
        cap = int(255 * DEFAULTS["max_brightness"]) + 1
        for visual in VISUALS:
            got, _ = frames(visual, 2.0, levels={"mic": 1.0, "mic_real": True, "out": 1.0, "out_real": True,
                                                 "bass": 1.0, "mid": 1.0, "treble": 1.0, "beat": 1.0, "music_real": True})
            self.assertLessEqual(max(x for _, wire in got for c in wire for x in c), cap, visual)

    def test_brightness_cannot_be_set_above_the_limit(self):
        s = settings()
        s.update({"brightness": 0.9})
        self.assertEqual(s.values["brightness"], s.values["max_brightness"])

    def test_power_limit(self):
        s = settings(power_limit_ma=150, max_brightness=1.0, brightness=1.0)
        got, comp = frames("ALARM", 1.0, s=s)
        self.assertTrue(comp.limited or comp.estimate_ma <= 150)
        for _, wire in got:
            self.assertLessEqual(60 * 1.0 + 20.0 * sum(x / 255 for c in wire for x in c), 150 * 1.06)

    def test_states_that_should_move_do(self):
        for visual in VISUALS:
            if visual in ("OFF", "SHUTTING_DOWN"):
                continue
            got, _ = frames(visual, 4.0)
            self.assertGreater(len({json.dumps(f) for f, _ in got}), 5, visual)


class ToldApart(unittest.TestCase):
    def test_ready_is_almost_nothing(self):
        got, _ = frames("READY", 6.0)
        self.assertLess(max(lit(f, 0.10) for f, _ in got), 20)
        self.assertGreater(max(lit(f, 0.10) for f, _ in got), 0)

    def test_muted_is_red_and_everywhere(self):
        got, _ = frames("MUTED", 5.0)
        for f, _ in got[15:]:
            self.assertEqual(lit(f, 0.2), 60)
            self.assertTrue(all(c[0] > 4 * max(c[1], c[2]) for c in f))

    def test_disconnected_is_a_few_red_pixels(self):
        got, _ = frames("DISCONNECTED", 4.0)
        for f, _ in got[15:]:
            self.assertLessEqual(lit(f, 0.02), 6)

    def test_error_blinks_and_muted_does_not(self):
        err, _ = frames("ERROR", 5.2)
        mut, _ = frames("MUTED", 5.2)
        dark = lambda got: sum(1 for f, _ in got[15:] if max(max(c) for c in f) < 0.2)       # noqa: E731
        self.assertGreater(dark(err), 30)
        self.assertEqual(dark(mut), 0)

    def test_setup_is_amber_not_blue(self):
        got, _ = frames("SETUP_REQUIRED", 3.0)
        f = got[-1][0]
        self.assertTrue(all(c[0] > c[2] * 2 for c in f if max(c) > 0.05))

    def test_progress_fills_what_was_reported(self):
        got, _ = frames("SETUP_IN_PROGRESS", 1.0, view={"progress": 0.25})
        self.assertTrue(13 <= lit(got[-1][0], 0.2) <= 17, lit(got[-1][0], 0.2))
        got, _ = frames("SETUP_IN_PROGRESS", 1.0, view={"progress": 1.0})
        self.assertEqual(lit(got[-1][0], 0.2), 60)

    def test_no_progress_known_means_no_fill(self):
        got, _ = frames("SETUP_IN_PROGRESS", 3.0)
        firsts = {lit(f, 0.3) for f, _ in got[15:]}
        self.assertLess(max(firsts), 30)

    def test_wake_crosses_the_whole_strip(self):
        got, _ = frames("WAKE_WORD_DETECTED", 0.5)
        reached = set()
        for f, _ in got:
            reached |= {i for i, c in enumerate(f) if max(c) > 0.4}
        self.assertGreaterEqual(len(reached), 54)

    def test_listening_follows_the_microphone(self):
        loud, _ = frames("LISTENING", 2.0, levels={"mic": 0.9, "mic_real": True})
        quiet, _ = frames("LISTENING", 2.0, levels={"mic": 0.0, "mic_real": True})
        energy = lambda got: sum(sum(c) for f, _ in got[20:] for c in f)                     # noqa: E731
        self.assertGreater(energy(loud), 2 * energy(quiet))
        self.assertGreater(energy(quiet), 0)

    def test_speaking_uses_the_reply_never_the_microphone(self):
        with_mic, _ = frames("SPEAKING", 2.0, levels={"mic": 1.0, "mic_real": True, "out_real": False})
        without, _ = frames("SPEAKING", 2.0, levels={"mic": 0.0, "mic_real": False, "out_real": False})
        self.assertEqual([f for f, _ in with_mic], [f for f, _ in without])
        loud, _ = frames("SPEAKING", 2.0, levels={"out": 0.95, "out_real": True})
        soft, _ = frames("SPEAKING", 2.0, levels={"out": 0.05, "out_real": True})
        energy = lambda got: sum(sum(c) for f, _ in got[20:] for c in f)                     # noqa: E731
        self.assertGreater(energy(loud), 2 * energy(soft))

    def test_music_reacts_only_to_real_sound(self):
        a, _ = frames("MUSIC_PLAYING", 1.0, levels={"bass": 1.0, "beat": 1.0, "music_real": False})
        b, _ = frames("MUSIC_PLAYING", 1.0, levels={"bass": 0.0, "beat": 0.0, "music_real": False})
        self.assertEqual([f for f, _ in a], [f for f, _ in b])
        c, _ = frames("MUSIC_PLAYING", 1.0, levels={"bass": 1.0, "beat": 1.0, "music_real": True})
        self.assertNotEqual([f for f, _ in a], [f for f, _ in c])

    def test_paused_is_still_and_small(self):
        got, _ = frames("MUSIC_PAUSED", 3.0)
        self.assertEqual({lit(f, 0.05) for f, _ in got[15:]}, {6})


class Brightness(unittest.TestCase):
    """At rest, in a conversation and with music, each at its own brightness."""

    LOUD = {"mic": 1.0, "mic_real": True, "out": 1.0, "out_real": True,
            "bass": 1.0, "mid": 1.0, "treble": 1.0, "beat": 1.0, "music_real": True}

    def own(self, **kw):
        return settings(max_brightness=1.0, brightness=0.2, active_brightness=1.0, music_brightness=0.6,
                        power_limit_ma=20000, gamma=1.0, animations={"READY": "glow"}, **kw)

    def top(self, visual, s, seconds=2.0, start=15):
        got, _ = frames(visual, seconds, s=s, levels=self.LOUD)
        return max(x for _, wire in got[start:] for c in wire for x in c)

    def test_each_look_at_its_own_brightness(self):
        s = self.own()
        self.assertLessEqual(self.top("READY", s), round(255 * 0.2) + 1)
        self.assertGreater(self.top("READY", s), round(255 * 0.15))           # at rest the strip is at 20 %, not dark
        for visual in ("LISTENING", "SPEAKING", "THINKING"):
            self.assertGreater(self.top(visual, s), 200, visual)
        self.assertGreater(self.top("WAKE_WORD_DETECTED", s, 0.5, 0), 200)     # the pulse itself
        music = self.top("MUSIC_PLAYING", s)
        self.assertLessEqual(music, round(255 * 0.6) + 1)
        self.assertGreater(music, round(255 * 0.3))

    def test_full_brightness_only_while_listening_when_asked(self):
        s = self.own(bright_when="listening")
        for visual in ("SPEAKING", "THINKING", "TOOL_EXECUTING"):
            self.assertLessEqual(self.top(visual, s), round(255 * 0.2) + 1, visual)
        self.assertGreater(self.top("LISTENING", s), 200)
        self.assertGreater(self.top("WAKE_WORD_DETECTED", s, 0.5, 0), 200)
        self.assertEqual(settings(bright_when="sometimes").values["bright_when"], "conversation")

    def test_at_rest_the_whole_strip_glows(self):
        got, _ = frames("READY", 4.0, s=self.own())
        for f, _ in got[15:]:
            self.assertEqual(lit(f, 0.5), 60)

    def test_up_at_once_for_the_wake_word_then_slowly_back_down(self):
        s = self.own()
        comp = Compositor(s.values, s.colours())
        t = 10.0
        for _ in range(30):
            t += 1 / 30
            comp.render({"visual": "READY"}, t)
        self.assertAlmostEqual(comp._bright, 0.2)
        comp.render({"visual": "WAKE_WORD_DETECTED"}, t + 1 / 30)
        self.assertAlmostEqual(comp._bright, 1.0)                 # the first frame after the word
        t += 1 / 30
        steps = []
        for _ in range(60):                                       # the conversation is over
            t += 1 / 30
            comp.render({"visual": "READY"}, t)
            steps.append(comp._bright)
        self.assertGreater(steps[14], 0.6)                        # half a second on, still bright
        self.assertAlmostEqual(steps[-1], 0.2)                    # two seconds on, back at rest
        self.assertTrue(all(a >= b for a, b in zip(steps, steps[1:])))
        self.assertLess(max(a - b for a, b in zip(steps, steps[1:])), 0.03)   # no visible step

    def test_the_new_brightnesses_obey_the_limit(self):
        s = settings()
        self.assertEqual(s.update({"active_brightness": 0.9, "music_brightness": 0.7}),
                         ["active_brightness", "music_brightness"])
        self.assertEqual((s.values["active_brightness"], s.values["music_brightness"]), (0.2, 0.2))
        self.assertEqual(s.update({"max_brightness": 1.0}), [])  # the dashboard cannot raise the limit


class Strip(unittest.TestCase):
    def test_transitions_do_not_jump(self):
        s = settings()
        comp = Compositor(s.values, s.colours())
        t = 10.0
        for _ in range(60):
            t += 1 / 30
            before = comp.render({"visual": "MUTED"}, t)
        after = comp.render({"visual": "READY"}, t + 1 / 30)
        self.assertLess(max(abs(a - b) for p, q in zip(before, after) for a, b in zip(p, q)), 0.12)
        for _ in range(30):
            t += 1 / 30
            end = comp.render({"visual": "READY"}, t)
        self.assertLess(lit(end, 0.3), 12)

    def test_red_ends_while_muted(self):
        got, _ = frames("SPEAKING", 1.0, view={"privacy": True}, levels={"out": 0.5, "out_real": True})
        f = got[-1][0]
        for i in (0, 1, 2, 3, 56, 57, 58, 59):
            self.assertGreater(f[i][0], 0.4)
            self.assertLess(f[i][2], 0.1)

    def test_colour_order_reverse_and_part_of_the_strip(self):
        s = settings(strip={"order": "GRB", "active": 10, "offset": 5}, max_brightness=1.0, brightness=1.0, gamma=1.0)
        comp = Compositor(s.values, s.colours())
        frame = [(1.0, 0.5, 0.0)] + [(0.0, 0.0, 0.0)] * 9
        wire, preview = comp.output(frame)
        self.assertEqual(len(wire), 60)
        self.assertEqual(wire[5][1], 255)                       # red is the second byte on a GRB strip
        self.assertIn(wire[5][0], (127, 128))
        self.assertEqual(sum(1 for c in wire if any(c)), 1)
        s.update({"direction": "reverse"})
        comp.configure(s.values, s.colours())
        wire, _ = comp.output(frame)
        self.assertEqual(wire[14][1], 255)
        self.assertEqual(len(preview), 360)

    def test_slow_fades_are_smooth(self):
        """Neighbouring LEDs round differently, so a level between two steps shows as a mix."""
        s = settings()
        comp = Compositor(s.values, s.colours())
        wire, _ = comp.output([(0.5, 0.5, 0.5)] * 60)
        self.assertGreaterEqual(len({c[0] for c in wire}), 2)
        again, _ = comp.output([(0.5, 0.5, 0.5)] * 60)
        self.assertEqual(wire, again)                           # and it does not flicker
        black, _ = comp.output([(0.0, 0.0, 0.0)] * 60)
        self.assertFalse(any(any(c) for c in black))

    def test_the_signal_for_the_leds(self):
        data = encode([(0x00, 0xFF, 0x81)])
        self.assertEqual(len(data), 8 + 24 + 260)
        body = data[8:32]
        self.assertEqual(body[0:8], bytes([0xC0] * 8))
        self.assertEqual(body[8:16], bytes([0xF8] * 8))
        self.assertEqual(body[16:24], bytes([0xF8, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xC0, 0xF8]))
        self.assertEqual(len(encode([(1, 2, 3)] * 60)), 8 + 60 * 24 + 260)

    def test_simulator_knows_the_colour_order(self):
        sim = Simulator(2, "GRB")
        sim.show([(10, 200, 3), (0, 0, 0)])
        self.assertEqual(sim.rgb()[0], (200, 10, 3))
        sim.close()
        self.assertRaises(RuntimeError, sim.show, [(0, 0, 0)] * 2)


class SettingsFile(unittest.TestCase):
    def test_defaults_never_light_the_real_strip(self):
        self.assertEqual(DEFAULTS["strip"]["driver"], "simulator")
        s = settings()
        s.path.write_text("{ this is not json", encoding="utf-8")
        broken = Settings(s.path)
        self.assertEqual(broken.values["strip"]["driver"], "simulator")
        self.assertTrue(broken.problem)
        s.path.write_text('{"strip": {"driver": "laser"}, "brightness": 7}', encoding="utf-8")
        odd = Settings(s.path)
        self.assertEqual(odd.values["strip"]["driver"], "simulator")
        self.assertEqual(odd.values["brightness"], odd.values["max_brightness"])

    def test_the_dashboard_cannot_switch_the_strip_on(self):
        s = settings()
        self.assertEqual(s.update({"strip": {"driver": "spi"}, "max_brightness": 1.0, "power_limit_ma": 9000}), [])
        self.assertEqual(s.values["strip"]["driver"], "simulator")
        self.assertEqual(s.values["max_brightness"], 0.2)
        self.assertEqual(s.update({"strip": {"driver": "spi"}}, trusted=True), ["strip"])
        self.assertEqual(Settings(s.path).values["strip"]["driver"], "spi")

    def test_changes_are_kept(self):
        s = settings()
        s.update({"theme": "cyberpunk", "colours": {"ready": "#00ff00", "wake": "nonsense"}, "animations": {"ready": "breathe"},
                  "speed": 99, "music_mode": "polka"})
        again = Settings(s.path)
        self.assertEqual(again.values["theme"], "cyberpunk")
        self.assertEqual(again.values["colours"], {"ready": "#00ff00"})
        self.assertEqual(again.values["animations"], {"READY": "breathe"})
        self.assertEqual((again.values["speed"], again.values["music_mode"]), (3.0, "smooth"))
        self.assertEqual(again.colours()["ready"], (0.0, 1.0, 0.0))
        self.assertEqual(s.update({"theme": "no-such-theme"}), [])

    def test_no_strip_no_problem(self):
        s = settings(strip={"driver": "spi", "spi_bus": 9, "spi_device": 9})
        strip, problem = open_strip(s.values)
        self.assertIsInstance(strip, Simulator)
        self.assertIn("not available", problem)


if __name__ == "__main__":
    unittest.main()
