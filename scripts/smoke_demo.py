"""Real CLI: render, crop, inspect, archive, restore and reproduce all input frame hashes."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", type=Path, default=Path("workspace/smoke"))
    args = p.parse_args()
    work = args.workdir.resolve()
    if work.exists():
        p.error("Use a new work directory; smoke tests do not overwrite prior results")
    work.mkdir(parents=True)

    def run(*command):
        result = subprocess.run([sys.executable, "-m", "codex_mv", *map(str, command)],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Command {command[0]} failed: {result.stderr}\n{result.stdout}")
        return json.loads(result.stdout)

    run("doctor")
    project = Path(run("demo", work / "inputs")["project"])
    run("validate", project)
    full = run("render", project, "--output", work / "full.mp4")
    preview = run("render", project, "--output", work / "preview.mp4", "--start", "0.75", "--duration", "2.2")
    evidence = run("extract-frame", work / "full.mp4", "--output", work / "native.png", "--index", "37")
    analysis = run("analyze-audio", project.parent / "assets/clicks.wav")
    bundle = run("pack", project, "--output", work / "project.zip")
    run("verify-archive", work / "project.zip")
    restored = run("restore", work / "project.zip", work / "restored")
    again = run("render", work / "restored/project.json", "--output", work / "restored.mp4")
    hashes_same = full["input_frame_hashes"] == again["input_frame_hashes"]
    if not hashes_same:
        raise RuntimeError("Restored project does not reproduce input frame hashes")
    result = {"ok": True, "full_frames": full["frames"], "preview_frames": preview["frames"],
              "full_qa": full["qa"], "preview_qa": preview["qa"],
              "native_evidence": evidence, "analysis": analysis,
              "bundle_files": bundle["files"], "restored_hashes_verified": restored["restored_hashes_verified"],
              "all_input_frame_hashes_equal_after_restore": hashes_same,
              "visual_acceptance": False}
    (work / "smoke-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "full_frames": full["frames"], "preview_frames": preview["frames"],
                      "restored_frame_hashes_equal": hashes_same, "report": str(work / "smoke-report.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
