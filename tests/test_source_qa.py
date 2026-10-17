import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from codex_mv.media import MediaError, Toolchain, probe
from codex_mv.qa import verify_render
from codex_mv.render import render_project
from codex_mv.source import extract_frame, native_frame_table


class SourceDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tools = SimpleNamespace(ffmpeg="ffmpeg", ffprobe="ffprobe")

    def test_native_probe_failure_includes_stderr(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="Invalid data found when processing input")
        with patch("codex_mv.source.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(MediaError, "FFprobe.*Invalid data found"):
                native_frame_table("invalid.mp4", self.tools)

    def test_native_extraction_failure_includes_stderr(self):
        rows = [{"index": 0, "pts": 0, "time": 0.0}]
        failed = subprocess.CompletedProcess([], 2880417800, stdout="",
                                             stderr="Unrecognized option 'vsync'.\nError splitting the argument list: Option not found")
        with tempfile.TemporaryDirectory(prefix="mv_extract_failure_") as temporary:
            output = Path(temporary) / "frame.png"
            with patch("codex_mv.source.native_frame_table", return_value=rows), \
                    patch("codex_mv.source.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(MediaError, "FFmpeg.*exit 2880417800.*Unrecognized option 'vsync'"):
                    extract_frame("source.mp4", output, index=0, toolchain=self.tools)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".evidence.json").exists())


class SourceAndQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.tools = Toolchain()
        except MediaError as exc:
            raise unittest.SkipTest(str(exc))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_evidence_")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_native_evidence_maps_the_same_stream_as_pts(self):
        video = self.root / "two-streams.mkv"
        subprocess.run([self.tools.ffmpeg, "-v", "error", "-n", "-f", "lavfi", "-i",
                        "color=red:size=16x12:rate=2:duration=1", "-f", "lavfi", "-i",
                        "color=blue:size=32x24:rate=2:duration=1", "-map", "0:v:0", "-map", "1:v:0",
                        "-c:v", "ffv1", "-disposition:v:0", "0", "-disposition:v:1", "default", str(video)],
                       check=True, capture_output=True)
        rows = native_frame_table(video, self.tools)
        result = extract_frame(video, self.root / "frame.png", index=1, toolchain=self.tools)
        self.assertEqual(result["actual_native_pts"], rows[1]["pts"])
        self.assertEqual(result["actual_native_index"], 1)
        with Image.open(self.root / "frame.png") as frame:
            self.assertEqual(frame.size, (16, 12))
            pixel = frame.convert("RGB").getpixel((5, 5))
            self.assertGreater(pixel[0], 200)
            self.assertLess(pixel[2], 30)
        by_time = extract_frame(video, self.root / "time.png", time=0.49, toolchain=self.tools)
        self.assertEqual(by_time["actual_native_pts"], rows[1]["pts"])
        self.assertFalse(by_time["visual_interpretation_verified"])
        with self.assertRaises(FileExistsError):
            extract_frame(video, self.root / "frame.png", index=0, toolchain=self.tools)

    def test_vfr_late_native_frame_needs_no_version_specific_sync_option(self):
        colors = ["red", "lime", "blue", "gold"]
        for index, color in enumerate(colors):
            Image.new("RGB", (16, 12), color).save(self.root / f"input-{index}.png")
        concat = self.root / "frames.txt"
        concat.write_text("file input-0.png\nduration 0.08\n"
                          "file input-1.png\nduration 0.28\n"
                          "file input-2.png\nduration 0.12\n"
                          "file input-3.png\nduration 0.4\nfile input-3.png\n", encoding="utf-8")
        video = self.root / "variable.mkv"
        subprocess.run([self.tools.ffmpeg, "-v", "error", "-n", "-f", "concat", "-safe", "0",
                        "-i", str(concat), "-c:v", "ffv1", str(video)], check=True, capture_output=True)
        rows = native_frame_table(video, self.tools)
        intervals = {round(right["time"] - left["time"], 3) for left, right in zip(rows, rows[1:])}
        self.assertGreater(len(intervals), 1, rows)

        # Actually run extraction while rejecting either incompatible sync flag.
        run = subprocess.run

        def run_without_sync_option(command, **kwargs):
            self.assertNotIn("-vsync", command)
            self.assertNotIn("-fps_mode", command)
            return run(command, **kwargs)

        output = self.root / "late.png"
        with patch("codex_mv.source.subprocess.run", side_effect=run_without_sync_option):
            result = extract_frame(video, output, index=3, toolchain=self.tools)
        self.assertEqual(result["actual_native_index"], 3)
        self.assertEqual(result["actual_native_pts"], rows[3]["pts"])
        self.assertEqual(result["actual_native_time"], rows[3]["time"])
        self.assertGreater(result["actual_native_time"], rows[1]["time"])
        with Image.open(output) as frame:
            self.assertEqual(frame.convert("RGB").tobytes(), Image.new("RGB", (16, 12), "gold").tobytes())
        by_time = extract_frame(video, self.root / "late-time.png", time=rows[3]["time"] - 0.01,
                                toolchain=self.tools)
        self.assertEqual(by_time["actual_native_pts"], rows[3]["pts"])
        self.assertEqual(json.loads(output.with_suffix(".evidence.json").read_text(encoding="utf-8")), result)

    def test_qa_uses_exact_decimal_ceil_and_decoded_count(self):
        Image.new("RGBA", (8, 8), (255, 80, 20, 255)).save(self.root / "sprite.png")
        data = {"schema_version": 1, "name": "fractional-window", "duration_seconds": 0.14, "fps": 100,
                "canvas": {"width": 64, "height": 48, "background": "black"},
                "assets": {"sprite": {"path": "sprite.png", "kind": "image"}},
                "scenes": [{"id": "one", "start": 0, "end": 0.14, "priority": 0,
                            "layers": [{"asset": "sprite", "start": 0, "end": 0.14,
                                        "keyframes": [{"time": 0, "x": 32, "y": 24}]}]}]}
        path = self.root / "project.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        output = self.root / "fractional.mp4"
        render_project(path, output, toolchain=self.tools)
        metadata = probe(output, self.tools)
        metadata["streams"][0]["nb_frames"] = "999"
        with patch("codex_mv.qa.probe", return_value=metadata):
            result = verify_render(path, output, toolchain=self.tools)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["expected_frames"], 14)
        self.assertEqual(next(x["actual"] for x in result["checks"] if x["name"] == "frame_count"), 14)
        self.assertFalse(result["visual_acceptance"])


if __name__ == "__main__":
    unittest.main()
