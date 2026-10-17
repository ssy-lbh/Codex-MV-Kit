import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from codex_mv.media import BoundedVideoReader, MediaError, Toolchain, probe


class ToolchainTests(unittest.TestCase):
    def test_environment_and_explicit_precedence(self):
        with patch.dict(os.environ, {"MV_FFMPEG": "environment-encoder", "MV_FFPROBE": "environment-probe"}):
            with patch("codex_mv.media.shutil.which", side_effect=lambda value: "/bin/" + value):
                chain = Toolchain(ffmpeg="explicit-encoder")
        self.assertEqual(chain.ffmpeg, "/bin/explicit-encoder")
        self.assertEqual(chain.ffprobe, "/bin/environment-probe")

    def test_missing_tool_has_actionable_error(self):
        with patch("codex_mv.media.shutil.which", return_value=None):
            with self.assertRaisesRegex(MediaError, "MV_FFMPEG"):
                Toolchain()


class NativeVideoReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.chain = Toolchain()
        except MediaError as exc:
            raise unittest.SkipTest(str(exc))
        cls.temporary = tempfile.TemporaryDirectory(prefix="mv_reader_test_")
        cls.path = Path(cls.temporary.name) / "native.mkv"
        cls.frames = [np.full((18, 26, 3), (index * 30, 120, 200), dtype=np.uint8) for index in range(6)]
        result = subprocess.run([cls.chain.ffmpeg, "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s:v", "26x18", "-r", "10", "-i", "pipe:0", "-an", "-c:v", "ffv1",
                                 "-pix_fmt", "bgr0", str(cls.path)], input=b"".join(frame.tobytes() for frame in cls.frames),
                                capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_native_rgb_nonsequential_reads_and_bounded_cache(self):
        with BoundedVideoReader(self.path, cache_size=2) as reader:
            self.assertEqual(len(reader), 6)
            self.assertAlmostEqual(reader.fps, 10)
            for index in (0, 5, 2, 3, 1, 5):
                image = reader.get(index)
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.size, (26, 18))
                np.testing.assert_array_equal(np.asarray(image), self.frames[index])
                self.assertLessEqual(len(reader._cache), 2)
            np.testing.assert_array_equal(np.asarray(reader[-1]), self.frames[-1])
        with self.assertRaisesRegex(MediaError, "closed"):
            reader.get(0)
        reader.close()

    def test_view_indexing_nested_views_and_parent_lifetime(self):
        with BoundedVideoReader(self.path) as reader:
            view = reader.view(1, 4)
            self.assertEqual(len(view), 4)
            np.testing.assert_array_equal(np.asarray(view[-1]), self.frames[4])
            np.testing.assert_array_equal(np.asarray(view[1:3][0]), self.frames[2])
            np.testing.assert_array_equal(np.asarray(reader[2:4][1]), self.frames[3])
            with self.assertRaises(IndexError):
                view.get(4)
            with self.assertRaises(ValueError):
                view.view(3, 2)
            with self.assertRaises(ValueError):
                reader[::2]
            with self.assertRaises(TypeError):
                reader.get(1.0)
            with self.assertRaises(TypeError):
                reader.get(True)
        with self.assertRaises(MediaError):
            view.get(0)

    def test_invalid_bounds_and_probe_metadata(self):
        with self.assertRaises(ValueError):
            BoundedVideoReader(self.path, cache_size=0)
        with BoundedVideoReader(self.path) as reader:
            for value in (6, -7):
                with self.assertRaises(IndexError):
                    reader.get(value)
            with self.assertRaises(ValueError):
                reader.view(-1, 2)
        metadata = probe(self.path, self.chain)
        self.assertEqual(metadata["streams"][0]["width"], 26)
        self.assertEqual(metadata["streams"][0]["height"], 18)
        with self.assertRaises(MediaError):
            probe(self.path.with_name("missing.mp4"), self.chain)


if __name__ == "__main__":
    unittest.main()
