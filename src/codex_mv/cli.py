from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__


def emit(data, output: Path | None = None):
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    print(text)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codex-mv", description="Portable sprite MV production tools")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--ffmpeg", help="FFmpeg executable (or MV_FFMPEG / PATH)")
    p.add_argument("--ffprobe", help="FFprobe executable (or MV_FFPROBE / PATH)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local runtime and external tools")
    d = commands.add_parser("demo", help="Create original synthetic inputs in a NEW directory")
    d.add_argument("directory", type=Path)
    d.add_argument("--duration", type=float, default=4.0)
    v = commands.add_parser("validate", help="Validate project JSON and local inputs")
    v.add_argument("project", type=Path)
    r = commands.add_parser("render", help="Render a full project or globally phased preview")
    r.add_argument("project", type=Path)
    r.add_argument("--output", "-o", type=Path, required=True)
    r.add_argument("--start", type=float, default=0)
    r.add_argument("--duration", type=float)
    r.add_argument("--no-captions", action="store_true")
    q = commands.add_parser("qa", help="Verify video frames and the entire declared audio window")
    q.add_argument("project", type=Path)
    q.add_argument("video", type=Path)
    q.add_argument("--start", type=float, default=0)
    q.add_argument("--duration", type=float)
    q.add_argument("--min-correlation", type=float, default=0.99)
    q.add_argument("--output", "-o", type=Path)
    b = commands.add_parser("probe", help="Read video/audio metadata")
    b.add_argument("input", type=Path)
    a = commands.add_parser("analyze-audio", help="Exploratory PCM/onset rhythm analysis")
    a.add_argument("input", type=Path)
    a.add_argument("--output", "-o", type=Path)
    a.add_argument("--bpm-min", type=float, default=60)
    a.add_argument("--bpm-max", type=float, default=200)
    c = commands.add_parser("compare-audio", help="Compare two complete declared audio windows")
    c.add_argument("reference", type=Path)
    c.add_argument("candidate", type=Path)
    c.add_argument("--reference-start", type=float, default=0)
    c.add_argument("--candidate-start", type=float, default=0)
    c.add_argument("--duration", type=float)
    c.add_argument("--output", "-o", type=Path)
    e = commands.add_parser("extract-frame", help="Extract one native frame and an integer-PTS sidecar")
    e.add_argument("input", type=Path)
    e.add_argument("--output", "-o", type=Path, required=True)
    select = e.add_mutually_exclusive_group(required=True)
    select.add_argument("--time", type=float)
    select.add_argument("--index", type=int)
    z = commands.add_parser("pack", help="Bundle all declared inputs with hashes, excluding undeclared work files")
    z.add_argument("project", type=Path)
    z.add_argument("--output", "-o", type=Path, required=True)
    z = commands.add_parser("verify-archive", help="Verify ZIP paths, manifest, CRC and all input hashes")
    z.add_argument("archive", type=Path)
    z = commands.add_parser("restore", help="Restore a verified bundle into a NEW directory")
    z.add_argument("archive", type=Path)
    z.add_argument("destination", type=Path)
    return p


def main(argv=None) -> int:
    # JSON and diagnostics use UTF-8 even when Windows redirects a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args(argv)
    try:
        from .archive import pack_project, restore_archive, verify_archive
        from .audio import analyze_audio, compare_audio
        from .demo import create_demo
        from .media import Toolchain, probe
        from .project import load_project
        from .qa import verify_render
        from .render import render_project
        from .source import extract_frame

        if args.command == "demo":
            emit({"project": str(create_demo(args.directory, duration=args.duration))})
        elif args.command == "validate":
            data = load_project(args.project)
            emit({"ok": True, "name": data["name"], "assets": len(data["assets"]), "scenes": len(data["scenes"])})
        elif args.command == "pack":
            emit(pack_project(args.project, args.output))
        elif args.command == "verify-archive":
            emit(verify_archive(args.archive))
        elif args.command == "restore":
            emit(restore_archive(args.archive, args.destination))
        else:
            tools = Toolchain(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
            if args.command == "doctor":
                emit({"version": __version__, "python": sys.version.split()[0],
                      "ffmpeg": tools.ffmpeg, "ffprobe": tools.ffprobe,
                      "ffmpeg_version": subprocess.check_output([tools.ffmpeg, "-version"], text=True, encoding="utf-8", errors="replace").splitlines()[0],
                      "ffprobe_version": subprocess.check_output([tools.ffprobe, "-version"], text=True, encoding="utf-8", errors="replace").splitlines()[0]})
            elif args.command == "probe":
                emit(probe(args.input, tools))
            elif args.command == "analyze-audio":
                emit(analyze_audio(args.input, toolchain=tools, bpm_min=args.bpm_min, bpm_max=args.bpm_max), args.output)
            elif args.command == "compare-audio":
                result = compare_audio(args.reference, args.candidate, reference_start=args.reference_start,
                                       candidate_start=args.candidate_start, duration=args.duration, toolchain=tools)
                emit(result, args.output)
                if not result["sample_count_match"] or result.get("status") == "silent_mismatch":
                    return 2
            elif args.command == "extract-frame":
                emit(extract_frame(args.input, args.output, time=args.time, index=args.index, toolchain=tools))
            elif args.command == "qa":
                result = verify_render(args.project, args.video, start=args.start, duration=args.duration,
                                       min_correlation=args.min_correlation, toolchain=tools)
                emit(result, args.output)
                return 0 if result["ok"] else 2
            elif args.command == "render":
                report_path = args.output.with_suffix(".render.json")
                if report_path.exists():
                    raise FileExistsError(report_path)
                result = render_project(args.project, args.output, start=args.start, duration=args.duration,
                                        toolchain=tools, captions=not args.no_captions)
                result["qa"] = verify_render(args.project, args.output, start=args.start, duration=args.duration, toolchain=tools)
                emit(result, report_path)
                return 0 if result["qa"]["ok"] else 2
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"codex-mv: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
