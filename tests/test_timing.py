import math
import unittest

from codex_mv.timing import BeatClock


class BeatClockTests(unittest.TestCase):
    def test_period_and_round_trip(self):
        clock = BeatClock(120, phase=0.125)
        self.assertEqual(clock.period, 0.5)
        for beat in (-8, -0.5, 0, 1.25, 100):
            self.assertAlmostEqual(clock.time_to_beat(clock.beat_to_time(beat)), beat)

    def test_crop_keeps_global_phase(self):
        clock = BeatClock(100, phase=-0.17)
        crop_start = 12.375
        for local_time in (0, 0.1, 0.825, 2.0):
            global_time = crop_start + local_time
            expected = (global_time + 0.17 + 0.2) % (4 * 0.6)
            self.assertAlmostEqual(clock.loop_phase(global_time, beats=4, shift=0.2), expected)
            self.assertAlmostEqual(clock.time_to_beat(global_time), (global_time + 0.17) / 0.6)

    def test_negative_time_residual_and_half_beat_tie(self):
        clock = BeatClock(120)
        self.assertAlmostEqual(clock.residual(-0.45), 0.05)
        self.assertAlmostEqual(clock.residual(-0.30), 0.20)
        self.assertAlmostEqual(clock.residual(-0.25), -0.25)
        self.assertAlmostEqual(clock.residual(0.25), -0.25)
        self.assertAlmostEqual(clock.loop_phase(-0.1), 0.4)

    def test_invalid_clock_and_conversion_inputs(self):
        for bpm in (0, -120, math.inf, math.nan, True):
            with self.subTest(bpm=bpm), self.assertRaises(ValueError):
                BeatClock(bpm)
        for phase in (math.inf, math.nan, False):
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                BeatClock(120, phase)
        clock = BeatClock(120)
        for value in (math.inf, math.nan, True):
            with self.assertRaises(ValueError):
                clock.beat_to_time(value)
            with self.assertRaises(ValueError):
                clock.time_to_beat(value)
        with self.assertRaises(ValueError):
            clock.loop_phase(0, beats=0)


if __name__ == "__main__":
    unittest.main()
