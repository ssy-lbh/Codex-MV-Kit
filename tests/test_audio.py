import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave

import numpy as np

from codex_mv.audio import AudioError, analyze_audio, compare_audio
from codex_mv.media import Toolchain
from codex_mv.timing import BeatClock


RATE = 8000


def write_wave(path, values, rate=RATE):
    pcm = np.clip(np.asarray(values), -1, 1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(np.round(pcm * 32767).astype("<i2").tobytes())


def clicks(duration, bpm=120, phase=0.125, start=0):
    values = np.zeros(round(duration * RATE))
    pulse = 0.8 * np.exp(-np.arange(round(0.012 * RATE)) / (0.002 * RATE))
    for time in np.arange(phase, duration, 60 / bpm):
        if time < start:
            continue
        index = round(time * RATE)
        length = min(len(pulse), len(values) - index)
        if length > 0:
            values[index:index + length] += pulse[:length]
    return values


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe not on PATH")
class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tools = Toolchain()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, name, values):
        path = self.root / name
        write_wave(path, values)
        return path

    def test_click_tempo_phase_and_explicit_half_beat_branch(self):
        path = self.save("click.wav", clicks(8))
        report = analyze_audio(path, sample_rate=RATE, toolchain=self.tools)
        self.assertEqual(report["sample_count"], 8 * RATE)
        self.assertAlmostEqual(report["estimated_bpm"], 120, delta=0.7)
        clock = BeatClock(report["estimated_bpm"], report["phase_seconds"])
        self.assertAlmostEqual(clock.residual(0.125), 0, delta=0.025)
        self.assertEqual([p["branch"] for p in report["phase_candidates"]],
                         ["onset_aligned", "half_beat_shift"])
        first, half = report["phase_candidates"]
        self.assertGreater(first["weighted_onset_support"], half["weighted_onset_support"])
        self.assertTrue(any(p["relation"] == "half_tempo" and abs(p["bpm"] - 60) < 0.01
                            for p in report["tempo_alternatives"]))
        self.assertFalse(report["downbeat_inferred"])
        self.assertFalse(report["lyrics_inferred"])
        self.assertGreater(report["uncertainty"]["bpm_half_width"],
                           report["uncertainty"]["scan_step_bpm"])

    def test_entire_stream_including_later_onsets_is_analyzed(self):
        path = self.save("late-clicks.wav", clicks(12, bpm=100, phase=0.17, start=4))
        report = analyze_audio(path, sample_rate=RATE, bpm_min=90, bpm_max=110, toolchain=self.tools)
        self.assertEqual(report["sample_count"], 12 * RATE)
        self.assertAlmostEqual(report["estimated_bpm"], 100, delta=0.7)
        self.assertGreater(report["onset_count"], 10)
        self.assertEqual(report["bpm_search_range"], [90, 110])

    def test_stationary_tone_has_no_invented_tempo_and_honest_stft_units(self):
        time = np.arange(4 * RATE) / RATE
        path = self.save("tone.wav", 0.3 * np.sin(2 * math.pi * 440 * time))
        report = analyze_audio(path, sample_rate=RATE, toolchain=self.tools)
        self.assertIsNone(report["estimated_bpm"])
        stft = report["stft"]
        self.assertEqual(stft["n_fft"], 2048)
        self.assertAlmostEqual(stft["frequency_bin_spacing_hz"], RATE / 2048)
        self.assertGreater(stft["hann_equivalent_noise_bandwidth_hz"], stft["frequency_bin_spacing_hz"])
        self.assertAlmostEqual(stft["window_duration_seconds"], 2048 / RATE)
        self.assertAlmostEqual(stft["dominant_frequency_bin_hz"], 440, delta=RATE / 2048)

    def test_cap_rejects_instead_of_silently_returning_a_prefix(self):
        path = self.save("capped.wav", clicks(2))
        with self.assertRaisesRegex(AudioError, "no partial analysis"):
            analyze_audio(path, sample_rate=RATE, max_duration_seconds=0.25, toolchain=self.tools)

    def test_exact_match_and_independent_preview_offsets(self):
        values = np.random.default_rng(7).normal(0, 0.1, 3 * RATE)
        reference = self.save("source.wav", values)
        candidate = self.save("preview.wav", values[RATE:2 * RATE])
        report = compare_audio(reference, candidate, reference_start=1, candidate_start=0,
                               duration=1, sample_rate=RATE, toolchain=self.tools)
        self.assertTrue(report["sample_count_match"])
        self.assertEqual(report["reference_samples"], RATE)
        self.assertEqual(report["candidate_samples"], RATE)
        self.assertAlmostEqual(report["correlation"], 1, places=12)
        self.assertEqual(report["lag_samples"], 0)
        self.assertEqual(report["reference"]["raw_sample_count"], 3 * RATE)
        self.assertEqual(report["reference"]["raw_samples_after_window"], RATE)

    def test_count_mismatch_never_correlates_minimum_length(self):
        reference = self.save("full.wav", clicks(2))
        candidate = self.save("short.wav", clicks(2)[:-17])
        report = compare_audio(reference, candidate, duration=2, sample_rate=RATE, toolchain=self.tools)
        self.assertFalse(report["sample_count_match"])
        self.assertEqual(report["status"], "count_mismatch")
        self.assertEqual(report["expected_samples"], 2 * RATE)
        self.assertEqual(report["candidate_samples"], 2 * RATE - 17)
        self.assertIsNone(report["correlation"])
        self.assertIsNone(report["lag_samples"])

    def test_missing_duration_compares_entire_remainders(self):
        reference = self.save("one.wav", clicks(1))
        candidate = self.save("two.wav", clicks(2))
        report = compare_audio(reference, candidate, sample_rate=RATE, toolchain=self.tools)
        self.assertEqual(report["reference_samples"], RATE)
        self.assertEqual(report["candidate_samples"], 2 * RATE)
        self.assertFalse(report["sample_count_match"])
        self.assertIsNone(report["correlation"])

    def test_declared_window_rounding_and_trailing_samples_are_separate(self):
        values = np.random.default_rng(11).uniform(-0.2, 0.2, RATE)
        path = self.save("window.wav", values)
        report = compare_audio(path, path, duration=0.1001, sample_rate=RATE, toolchain=self.tools)
        self.assertEqual(report["expected_samples"], round(0.1001 * RATE))
        self.assertEqual(report["reference_samples"], report["expected_samples"])
        self.assertEqual(report["reference"]["raw_samples_after_window"], RATE - report["expected_samples"])
        self.assertTrue(report["sample_count_match"])

    def test_lag_sign_is_candidate_delay(self):
        values = np.random.default_rng(18).uniform(-0.2, 0.2, 2 * RATE)
        delayed = np.r_[np.zeros(37), values[:-37]]
        reference = self.save("noise.wav", values)
        candidate = self.save("delay.wav", delayed)
        report = compare_audio(reference, candidate, duration=2, sample_rate=RATE, toolchain=self.tools)
        self.assertEqual(report["lag_samples"], 37)
        self.assertLess(abs(report["correlation"]), 0.1)

    def test_aac_decoder_excess_is_separate_from_complete_declared_window(self):
        values = 0.3 * np.sin(2 * math.pi * 440 * np.arange(RATE) / RATE)
        reference = self.save("aac-source.wav", values)
        candidate = self.root / "aac-candidate.m4a"
        subprocess.run([self.tools.ffmpeg, "-v", "error", "-nostdin", "-i", str(reference),
                        "-c:a", "aac", str(candidate)], check=True, capture_output=True)
        report = compare_audio(reference, candidate, duration=1, sample_rate=RATE, toolchain=self.tools)
        self.assertTrue(report["sample_count_match"])
        self.assertEqual(report["candidate_samples"], RATE)
        self.assertGreaterEqual(report["candidate"]["raw_sample_count"], RATE)
        self.assertEqual(report["candidate"]["raw_samples_after_window"],
                         report["candidate"]["raw_sample_count"] - RATE)
        self.assertGreater(report["correlation"], 0.98)

    def test_silent_and_one_sided_silent_windows(self):
        silent = self.save("silent.wav", np.zeros(RATE))
        tone = self.save("non-silent.wav", 0.1 * np.sin(2 * math.pi * 440 * np.arange(RATE) / RATE))
        both = compare_audio(silent, silent, sample_rate=RATE, toolchain=self.tools)
        self.assertIsNone(both["correlation"])
        self.assertEqual(both["status"], "silent_match")
        self.assertTrue(both["reference_is_silent"])
        self.assertTrue(both["candidate_is_silent"])
        one = compare_audio(silent, tone, sample_rate=RATE, toolchain=self.tools)
        self.assertEqual(one["status"], "silent_mismatch")
        self.assertIsNone(one["correlation"])
        self.assertTrue(one["reference_is_silent"])
        self.assertFalse(one["candidate_is_silent"])

    def test_invalid_numeric_arguments(self):
        path = self.save("valid.wav", clicks(1))
        for kwargs in ({"start": -1}, {"reference_start": -1}, {"candidate_start": -1},
                       {"duration": 0}, {"duration": math.inf}, {"sample_rate": True},
                       {"silence_threshold": -1}, {"max_lag_seconds": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                compare_audio(path, path, toolchain=self.tools, **kwargs)
        for kwargs in ({"bpm_min": 0}, {"bpm_min": 200, "bpm_max": 60}, {"bpm_max": math.nan}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyze_audio(path, toolchain=self.tools, **kwargs)


if __name__ == "__main__":
    unittest.main()
