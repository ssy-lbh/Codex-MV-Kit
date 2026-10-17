"""Portable external media tools and a small, lazy native-resolution reader."""

from __future__ import annotations

from collections import OrderedDict
import json
import math
import operator
import os
from pathlib import Path
import shutil
import subprocess

import cv2
from PIL import Image


class MediaError(RuntimeError):
    """A media file or an external media operation could not be read."""


def _executable(value: str | os.PathLike | None, variable: str, fallback: str) -> str:
    candidate = os.fspath(value) if value is not None else os.environ.get(variable, fallback)
    resolved = shutil.which(candidate)
    if resolved is None:
        raise MediaError(f"Cannot find {fallback}: configure {variable} or add it to PATH")
    return resolved


class Toolchain:
    """Resolve explicit executable arguments, then MV_* variables, then PATH."""

    def __init__(self, ffmpeg=None, ffprobe=None):
        self.ffmpeg = _executable(ffmpeg, "MV_FFMPEG", "ffmpeg")
        self.ffprobe = _executable(ffprobe, "MV_FFPROBE", "ffprobe")


def probe(path, toolchain=None) -> dict:
    """Return unmodified FFprobe ``streams`` and ``format`` JSON information."""
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"Media file does not exist: {path}")
    chain = toolchain or Toolchain()
    try:
        result = subprocess.run(
            [chain.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"FFprobe could not inspect {path}: {exc}") from exc
    if result.returncode:
        raise MediaError(f"FFprobe failed for {path}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(f"FFprobe returned invalid JSON for {path}") from exc


def _integer(value, name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be an integer") from exc


class BoundedVideoReader:
    """Decode RGB PIL images on demand, retaining at most ``cache_size`` frames.

    Indices address native decoded frames. No resize, frame-rate conversion or
    eager RGB loading occurs. Views share this reader and its cache/lifetime.
    """

    def __init__(self, path, cache_size=12):
        self.path = Path(path)
        self.cache_size = _integer(cache_size, "cache_size")
        if self.cache_size <= 0:
            raise ValueError("cache_size must be positive")
        self._cache: OrderedDict[int, Image.Image] = OrderedDict()
        self._cap = cv2.VideoCapture(str(self.path))
        self._closed = False
        self._next_index = 0
        if not self._cap.isOpened():
            self.close()
            raise MediaError(f"Cannot open video: {self.path}")
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        count = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or fps <= 0 or not math.isfinite(count) or count < 1:
            self.close()
            raise MediaError(f"Video has no reliable FPS/frame-count metadata: {self.path}")
        self.fps = float(fps)
        self.frame_count = int(round(count))

    def __len__(self):
        return self.frame_count

    def get(self, index) -> Image.Image:
        if self._closed:
            raise MediaError("Video reader is closed")
        index = _integer(index, "index")
        if index < 0:
            index += self.frame_count
        if not 0 <= index < self.frame_count:
            raise IndexError(index)
        if index in self._cache:
            image = self._cache.pop(index)
            self._cache[index] = image
            return image
        if index != self._next_index:
            if not self._cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise MediaError(f"Cannot seek video {self.path} to frame {index}")
        success, frame = self._cap.read()
        if not success or frame is None:
            raise MediaError(f"Cannot decode video {self.path} frame {index}")
        self._next_index = index + 1
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self._cache[index] = image
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return image

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self.frame_count)
            if step != 1:
                raise ValueError("Video views require a slice step of 1")
            return self.view(start, max(0, stop - start))
        return self.get(index)

    def view(self, start, count):
        return VideoView(self, start, count)

    def close(self):
        if not self._closed:
            self._closed = True
            self._cap.release()
            self._cache.clear()

    def __enter__(self):
        if self._closed:
            raise MediaError("Video reader is closed")
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        cap = getattr(self, "_cap", None)
        if cap is not None:
            cap.release()


class VideoView:
    """A non-owning, bounded window of a reader; indices remain view-relative."""

    def __init__(self, parent: BoundedVideoReader, start, count):
        self.parent = parent
        self.start = _integer(start, "start")
        self.frame_count = _integer(count, "count")
        if self.start < 0 or self.frame_count < 0 or self.start + self.frame_count > len(parent):
            raise ValueError("Video view lies outside its parent")
        self.fps = parent.fps

    def __len__(self):
        return self.frame_count

    def get(self, index):
        index = _integer(index, "index")
        if index < 0:
            index += self.frame_count
        if not 0 <= index < self.frame_count:
            raise IndexError(index)
        return self.parent.get(self.start + index)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self.frame_count)
            if step != 1:
                raise ValueError("Video views require a slice step of 1")
            return self.view(start, max(0, stop - start))
        return self.get(index)

    def view(self, start, count):
        start = _integer(start, "start")
        count = _integer(count, "count")
        if start < 0 or count < 0 or start + count > self.frame_count:
            raise ValueError("Video view lies outside its parent")
        return VideoView(self.parent, self.start + start, count)
