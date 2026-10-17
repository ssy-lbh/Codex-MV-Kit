"""Native presentation timestamps are evidence, separate from production frame clocks."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .archive import file_sha256
from .media import MediaError, Toolchain, probe


def _run(command: list[str], operation: str) -> subprocess.CompletedProcess:
    """Preserve media-tool diagnostics instead of an opaque process exit code."""
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MediaError(f"{operation}: {exc}") from exc
    if result.returncode:
        details = result.stderr.strip()[-6000:] or "No diagnostic output"
        raise MediaError(f"{operation} (exit {result.returncode}): {details}")
    return result


def native_frame_table(path: str | Path, toolchain: Toolchain | None = None) -> list[dict[str, Any]]:
    tools = toolchain or Toolchain()
    result = _run([tools.ffprobe, "-v", "error", "-select_streams", "v:0",
                   "-show_frames", "-show_entries",
                   "frame=pts,best_effort_timestamp,best_effort_timestamp_time",
                   "-of", "json", str(path)], f"FFprobe native-frame inspection failed for {path}")
    rows = []
    for index, frame in enumerate(json.loads(result.stdout).get("frames", [])):
        if "best_effort_timestamp_time" not in frame:
            raise MediaError(f"No native timestamp for frame {index}")
        rows.append({"index": index, "pts": frame.get("pts", frame.get("best_effort_timestamp")),
                     "time": float(frame["best_effort_timestamp_time"])})
    if not rows:
        raise MediaError("No video frames")
    return rows


def extract_frame(path: str | Path, output: str | Path, *, time: float | None = None,
                  index: int | None = None, toolchain: Toolchain | None = None) -> dict[str, Any]:
    tools = toolchain or Toolchain()
    path, output = Path(path), Path(output)
    if output.exists() or output.with_suffix(".evidence.json").exists():
        raise FileExistsError(output)
    if output.suffix.lower() != ".png":
        raise ValueError("Evidence frame output must be a PNG")
    if (time is None) == (index is None):
        raise ValueError("Choose exactly one of time or index")
    rows = native_frame_table(path, tools)
    if time is not None:
        import math
        if not math.isfinite(time) or time < 0 or time > rows[-1]["time"]:
            raise ValueError("Requested time is outside the native frame timeline")
        row = min(rows, key=lambda r: abs(r["time"] - time))
    else:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(rows):
            raise ValueError("Requested frame index is outside the native timeline")
        row = rows[index]
    output.parent.mkdir(parents=True, exist_ok=True)
    # One selected native frame needs no frame-rate conversion. Avoid both the
    # removed -vsync option and -fps_mode, which older FFmpeg builds lack.
    command = [tools.ffmpeg, "-v", "error", "-n", "-noautorotate", "-i", str(path),
               "-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
               f"select=eq(n\\,{row['index']})", "-frames:v", "1",
               "-f", "image2", "-update", "1", str(output)]
    _run(command, f"FFmpeg native-frame extraction failed for {path}")
    stream = next(s for s in probe(path, tools)["streams"] if s["codec_type"] == "video")
    report = {"source_file": str(path), "source_sha256": file_sha256(path),
              "requested_time": time, "requested_index": index,
              "actual_native_index": row["index"], "actual_native_pts": row["pts"],
              "actual_native_time": row["time"], "time_base": stream["time_base"],
              "selection": "nearest native PTS" if time is not None else "exact native frame index",
              "width": stream["width"], "height": stream["height"],
              "png_file": str(output), "png_sha256": file_sha256(output),
              "visual_interpretation_verified": False}
    output.with_suffix(".evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    return report
