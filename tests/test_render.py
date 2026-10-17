import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import numpy as np
from PIL import Image

from codex_mv.media import BoundedVideoReader, MediaError, Toolchain, probe
from codex_mv.project import ProjectError
from codex_mv.render import _frame_count, _keys, _place, _state, render_project


def project_fixture():
    return {"schema_version": 1, "name": "synthetic-render", "duration_seconds": 1.1, "fps": 10,
            "canvas": {"width": 64, "height": 48, "background": "black"},
            "assets": {"red": {"path": "red.png", "kind": "image"},
                       "blue": {"path": "blue.png", "kind": "image"}},
            "scenes": [{"id": "base", "start": 0, "end": 1.1, "priority": 0, "background": "black",
                        "layers": [{"asset": "red", "start": 0, "end": 1.1, "z": 1,
                                    "clock": {"epoch": -0.1, "shift": 0.2, "period": 1},
                                    "keyframes": [{"time": 0, "x": 32, "y": 24},
                                                  {"time": 0.5, "x": 40, "y": 24, "asset": "blue"},
                                                  {"time": 1, "x": 32, "y": 24}]}]}]}


class CompositionTests(unittest.TestCase):
    def test_decimal_exact_ceil_and_real_cel_switch(self):
        self.assertEqual(_frame_count(0.1, 30), 3)
        self.assertEqual(_frame_count(0.21, 10), 3)
        layer = project_fixture()["scenes"][0]["layers"][0]
        keys = _keys(layer, 64, 48)
        first, phase = _state(layer, keys, 0.1)
        self.assertAlmostEqual(phase, 0.4)
        self.assertEqual(first["asset"], "red")
        second, phase = _state(layer, keys, 0.3)
        self.assertAlmostEqual(phase, 0.6)
        self.assertEqual(second["asset"], "blue")
        self.assertAlmostEqual(first["scale"], 1)

    def test_outline_retains_filled_occlusion_and_negative_preserves_alpha(self):
        canvas = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        sprite = Image.new("RGBA", (8, 8), (30, 60, 90, 255))
        state = {"x": 16, "y": 16, "scale": 1, "rotation": 0, "opacity": 1}
        _place(canvas, sprite, {"effect": "white_outline", "outline_width": 2}, state)
        self.assertEqual(canvas.getpixel((16, 16)), (0, 0, 0, 255))
        self.assertEqual(canvas.getpixel((10, 16)), (255, 255, 255, 255))
        canvas = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        sprite.putalpha(128)
        _place(canvas, sprite, {"effect": "negative"}, state)
        self.assertEqual(canvas.getpixel((16, 16)), (225, 195, 165, 128))


class EncoderShutdownTests(unittest.TestCase):
    def test_failed_render_drains_wrapper_child_before_temp_cleanup(self):
        self._check_wrapper_failure(hang=False)

    @unittest.skipUnless(os.name == "nt", "Windows process-tree fallback")
    def test_unresponsive_windows_wrapper_child_is_stopped_with_parent(self):
        self._check_wrapper_failure(hang=True)

    def _check_wrapper_failure(self, *, hang):
        # Reproduce an executable shim: its child inherits encoder.log, then
        # needs time after stdin EOF to release that handle and exit.
        real_popen = subprocess.Popen
        with tempfile.TemporaryDirectory(prefix="mv_encoder_wrapper_test_") as temporary:
            root = Path(temporary)
            ready, done = root / "child-ready", root / "child-done"
            child = root / "child.py"
            child.write_text(
                "import os, sys, time\nfrom pathlib import Path\n"
                "ready = Path(sys.argv[1])\n"
                "ready.with_suffix('.tmp').write_text(str(os.getpid()), encoding='ascii')\n"
                "ready.with_suffix('.tmp').replace(ready)\n"
                "sys.stdin.buffer.read()\n"
                f"time.sleep({60 if hang else 0.3})\n"
                "Path(sys.argv[2]).write_text('EOF drained', encoding='ascii')\n",
                encoding="utf-8",
            )
            wrapper = root / "wrapper.py"
            wrapper.write_text(
                "import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-u', *sys.argv[1:]])\n"
                "raise SystemExit(child.wait())\n",
                encoding="utf-8",
            )
            project = root / "project.json"
            project.write_text(json.dumps(project_fixture()), encoding="utf-8")
            (root / "red.png").write_bytes(b"intentionally corrupt PNG")
            Image.new("RGBA", (8, 8), "blue").save(root / "blue.png")
            processes, work_dirs, child_handles = [], [], []

            def launch_wrapper(command, **kwargs):
                if command[0] != "fixture":
                    return real_popen(command, **kwargs)
                work_dirs.append(Path(kwargs["cwd"]))
                process = real_popen([sys.executable, "-u", str(wrapper), str(child), str(ready), str(done)], **kwargs)
                processes.append(process)
                deadline = time.monotonic() + 5
                while not ready.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Encoder wrapper child did not start")
                    time.sleep(0.01)
                if hang:
                    import ctypes
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    handle = kernel.OpenProcess(0x00100000, False, int(ready.read_text(encoding="ascii")))
                    if not handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                    child_handles.append((kernel, handle))
                return process

            try:
                with patch("codex_mv.render.subprocess.Popen", side_effect=launch_wrapper), \
                        patch("codex_mv.render._ENCODER_EOF_TIMEOUT", 0.1 if hang else 5.0):
                    with self.assertRaisesRegex(MediaError, "Cannot decode image asset red"):
                        render_project(project, root / "failed.mp4",
                                       toolchain=SimpleNamespace(ffmpeg="fixture", ffprobe="fixture"))
                self.assertEqual(len(processes), 1)
                if hang:
                    import ctypes
                    kernel, handle = child_handles[0]
                    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                    kernel.WaitForSingleObject.restype = ctypes.c_uint32
                    self.assertEqual(kernel.WaitForSingleObject(handle, 3000), 0)
                    self.assertIsNotNone(processes[0].returncode)
                    self.assertFalse(done.exists())
                else:
                    self.assertEqual(processes[0].returncode, 0)
                    self.assertEqual(done.read_text(encoding="ascii"), "EOF drained")
                self.assertFalse(work_dirs[0].exists())
                self.assertFalse((root / "failed.mp4").exists())
            finally:
                # Keep the fixture leak-free even when run against the old
                # implementation, which kills the wrapper before its child.
                for process in processes:
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                if not hang:
                    deadline = time.monotonic() + 5
                    while ready.exists() and not done.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                for kernel, handle in child_handles:
                    import ctypes
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    kernel.CloseHandle.restype = ctypes.c_int
                    kernel.CloseHandle(handle)


class SyntheticRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.chain = Toolchain()
        except MediaError as exc:
            raise unittest.SkipTest(str(exc))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mv_render_test_")
        self.root = Path(self.temporary.name) / "项目 测试"
        self.root.mkdir()
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(self.root / "red.png")
        Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(self.root / "blue.png")
        self.data = project_fixture()
        self.path = self.root / "project.json"

    def tearDown(self):
        self.temporary.cleanup()

    def write_project(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def render(self, name, **kwargs):
        self.write_project()
        return render_project(self.path, self.root / name, toolchain=self.chain, **kwargs)

    def test_exact_frame_count_global_crop_clock_and_real_cels(self):
        full = self.render("full.mp4")
        crop = self.render("crop.mp4", start=0.2, duration=0.21)
        self.assertEqual(full["frames"], 11)
        self.assertEqual(crop["frames"], 3)
        self.assertEqual(crop["input_frame_hashes"], full["input_frame_hashes"][2:5])
        self.assertGreater(len(set(full["input_frame_hashes"])), 2)
        self.assertEqual(int(probe(self.root / "crop.mp4", self.chain)["streams"][0]["nb_frames"]), 3)
        with BoundedVideoReader(self.root / "full.mp4") as reader:
            frame = np.asarray(reader.get(3))
            self.assertGreater(int(frame[24, 38, 2]), 200)
            self.assertLess(int(frame[24, 38, 0]), 40)

    def test_scene_priority_and_canvas_gaps(self):
        self.data["scenes"][0].update(start=0.2, end=0.8)
        self.data["scenes"][0]["layers"] = []
        self.data["scenes"][0]["background"] = "red"
        self.data["scenes"].append({"id": "foreground", "start": 0.4, "end": 0.6,
                                     "priority": 1, "background": "blue", "layers": []})
        report = self.render("scenes.mp4")
        def solid_hash(color):
            return hashlib.sha256(Image.new("RGB", (64, 48), color).tobytes()).hexdigest()
        self.assertEqual(report["input_frame_hashes"][0], solid_hash("black"))
        self.assertEqual(report["input_frame_hashes"][2], solid_hash("red"))
        self.assertEqual(report["input_frame_hashes"][4], solid_hash("blue"))
        self.assertEqual(report["input_frame_hashes"][6], solid_hash("red"))
        self.assertEqual(report["input_frame_hashes"][8], solid_hash("black"))

    def test_default_duration_uses_exact_remaining_project_window(self):
        full = self.render("remaining-full.mp4")
        crop = self.render("remaining-crop.mp4", start=0.8)
        self.assertEqual(crop["duration_seconds"], 0.3)
        self.assertEqual(crop["frames"], 3)
        self.assertEqual(crop["input_frame_hashes"], full["input_frame_hashes"][8:11])

    def test_existing_output_and_failed_render_cleanup(self):
        destination = self.root / "existing.mp4"
        destination.write_bytes(b"user-owned")
        self.write_project()
        with self.assertRaises(FileExistsError):
            render_project(self.path, destination, toolchain=self.chain)
        self.assertEqual(destination.read_bytes(), b"user-owned")
        (self.root / "red.png").write_bytes(b"broken image")
        with self.assertRaises(MediaError):
            self.render("failed.mp4")
        self.assertFalse((self.root / "failed.mp4").exists())
        with self.assertRaises(ProjectError):
            self.render("outside.mp4", start=1.0, duration=0.2)

    def test_audio_uses_project_offset_plus_crop_and_checks_all_samples(self):
        rate = 8000
        times = np.arange(rate * 2) / rate
        frequencies = np.where(times < 0.4, 300, 1000)
        samples = (np.sin(2 * np.pi * frequencies * times) * 18000).astype("<i2")
        with wave.open(str(self.root / "source.wav"), "wb") as audio:
            audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            audio.writeframes(samples.tobytes())
        self.data["audio"] = {"path": "source.wav", "offset_seconds": 0.2}
        report = self.render("audio.mp4", start=0.3, duration=0.4)
        self.assertAlmostEqual(report["audio"]["source_start_seconds"], 0.5)
        self.assertEqual(report["audio"]["declared_samples"], 3200)
        result = subprocess.run([self.chain.ffmpeg, "-v", "error", "-i", str(self.root / "audio.mp4"),
                                 "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-f", "f32le", "pipe:1"],
                                capture_output=True, check=True)
        decoded = np.frombuffer(result.stdout, dtype="<f4")
        self.assertGreaterEqual(len(decoded), 3200)
        spectrum = np.abs(np.fft.rfft(decoded[:3200]))
        peak = np.fft.rfftfreq(3200, 1 / rate)[np.argmax(spectrum)]
        self.assertAlmostEqual(peak, 1000, delta=10)
        self.data["audio"]["offset_seconds"] = 1.9
        with self.assertRaisesRegex(MediaError, "window is short"):
            self.render("short.mp4", start=0, duration=0.4)
        self.assertFalse((self.root / "short.mp4").exists())

    def test_ass_uses_global_time_with_unicode_input_paths(self):
        ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 64
PlayResY: 48
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,14,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,0,0,2,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.40,0:00:00.90,Default,,0,0,0,,GLOBAL
"""
        (self.root / "字幕.ass").write_text(ass, encoding="utf-8")
        (self.root / "fonts").mkdir()
        self.data["captions"] = {"path": "字幕.ass", "baked": False, "fonts_dir": "fonts"}
        enabled = self.render("captions.mp4", start=0.5, duration=0.2)
        disabled = self.render("plain.mp4", start=0.5, duration=0.2, captions=False)
        self.assertTrue(enabled["captions_rendered"])
        self.assertFalse(disabled["captions_rendered"])
        with BoundedVideoReader(self.root / "captions.mp4") as burned, BoundedVideoReader(self.root / "plain.mp4") as plain:
            self.assertGreater(np.abs(np.asarray(burned[0]).astype(int) - np.asarray(plain[0]).astype(int)).sum(), 1000)

    def test_video_source_bounds_explicit_loop_hold_and_baked_caption_guard(self):
        frames = [np.full((12, 16, 3), (index * 50, 100, 20), np.uint8) for index in range(4)]
        movie = self.root / "source.mkv"
        subprocess.run([self.chain.ffmpeg, "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "-s:v", "16x12", "-r", "10", "-i", "pipe:0", "-c:v", "ffv1", str(movie)],
                       input=b"".join(frame.tobytes() for frame in frames), capture_output=True, check=True)
        asset = {"path": "source.mkv", "kind": "video"}
        self.data["assets"]["movie"] = asset
        layer = self.data["scenes"][0]["layers"][0]
        layer.update(asset="movie", keyframes=[{"time": 0, "x": 32, "y": 24}])
        layer.pop("clock")
        with self.assertRaisesRegex(MediaError, "source overrun"):
            self.render("overrun.mp4")
        self.assertFalse((self.root / "overrun.mp4").exists())
        asset["loop"] = True
        looped = self.render("loop.mp4", start=0.2, duration=0.3)
        self.assertEqual([sample["source_frame"] for sample in looped["video_frame_samples"]], [2, 3, 0])
        self.assertEqual(looped["held_video_frames"], 0)
        asset.update(loop=False, end_behavior="hold")
        held = self.render("hold.mp4")
        self.assertEqual(held["held_video_frames"], 7)
        self.assertEqual(held["video_frame_samples"][-1]["requested_source_frame"], 10)
        self.assertEqual(held["video_frame_samples"][-1]["source_frame"], 3)
        (self.root / "captions.ass").write_text("[Script Info]\n", encoding="utf-8")
        self.data["captions"] = {"path": "captions.ass", "baked": False}
        asset["baked"] = True
        with self.assertRaisesRegex(ProjectError, "duplicate captions"):
            self.render("duplicate.mp4")


if __name__ == "__main__":
    unittest.main()
