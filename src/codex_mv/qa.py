"""Technical checks; a passing report is deliberately not a visual sign-off."""
from __future__ import annotations

import math
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .audio import compare_audio
from .media import Toolchain, probe
from .project import load_project


def verify_render(project_file: str | Path, video: str | Path, *, start: float = 0.0,
                  duration: float | None = None, min_correlation: float = 0.99,
                  toolchain: Toolchain | None = None) -> dict[str, Any]:
    if not math.isfinite(min_correlation) or not -1 <= min_correlation <= 1:
        raise ValueError("min_correlation must be finite and between -1 and 1")
    tools = toolchain or Toolchain()
    project_file = Path(project_file)
    data = load_project(project_file)
    exact_duration = (Fraction(str(data["duration_seconds"])) - Fraction(str(start))
                      if duration is None else Fraction(str(duration)))
    duration = float(exact_duration)
    if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
        raise ValueError("Invalid verification window")
    if start + duration > data["duration_seconds"] + 1e-9:
        raise ValueError("Verification window exceeds project")
    info = probe(video, tools)
    streams = info.get("streams", [])
    videos = [s for s in streams if s["codec_type"] == "video"]
    audios = [s for s in streams if s["codec_type"] == "audio"]
    expected_frames = math.ceil(exact_duration * Fraction(str(data["fps"])))
    checks = []
    if len(videos) != 1:
        return {"ok": False, "errors": ["Expected exactly one video stream"], "visual_acceptance": False}
    v = videos[0]
    import json
    count = subprocess.check_output([tools.ffprobe, "-v", "error", "-select_streams", "v:0",
                                     "-count_frames", "-show_entries", "stream=nb_read_frames",
                                     "-of", "json", str(video)])
    frame_count = int(json.loads(count)["streams"][0]["nb_read_frames"])
    fps = float(Fraction(v.get("avg_frame_rate", "0/1")))
    actual_duration = float(v.get("duration", info.get("format", {}).get("duration", 0)))
    checks.extend([{"name": "frame_count", "ok": frame_count == expected_frames,
                    "expected": expected_frames, "actual": frame_count},
                   {"name": "resolution", "ok": [v["width"], v["height"]] == [data["canvas"]["width"], data["canvas"]["height"]]},
                   {"name": "fps", "ok": abs(fps - data["fps"]) < 1e-6, "actual": fps},
                   {"name": "pixel_format", "ok": v.get("pix_fmt") == "yuv420p", "actual": v.get("pix_fmt")},
                   {"name": "video_duration", "ok": abs(actual_duration - expected_frames / data["fps"]) < 0.002,
                    "actual": actual_duration}])
    audio = None
    if data.get("audio"):
        checks.append({"name": "audio_stream", "ok": len(audios) == 1})
        if len(audios) == 1:
            source = project_file.parent / data["audio"]["path"]
            audio = compare_audio(source, video,
                                  reference_start=data["audio"].get("offset_seconds", 0.0) + start,
                                  candidate_start=0.0, duration=duration, sample_rate=22050, toolchain=tools)
            checks.append({"name": "declared_audio_window_samples", "ok": bool(audio["sample_count_match"])})
            corr = audio.get("correlation")
            matches = ((corr is not None and corr >= min_correlation)
                       or audio.get("status") == "silent_match")
            checks.append({"name": "unshifted_audio_correlation", "ok": matches,
                           "threshold": min_correlation, "actual": corr})
    else:
        checks.append({"name": "no_unrequested_audio", "ok": not audios})
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "video": str(video), "global_start_seconds": start, "declared_duration_seconds": duration,
            "expected_frames": expected_frames, "audio": audio,
            "sample_scope": "The complete explicitly declared window, never pairwise min-length truncation.",
            "visual_acceptance": False, "lyrics_complete": False}
