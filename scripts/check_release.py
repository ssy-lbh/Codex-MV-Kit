"""Check the actual tracked public surface, without reading ignored user projects."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "venv", "workspace", "workspaces", "private", "build", "dist",
            "__pycache__", ".pytest_cache", "artifacts", "renders"}
FORBIDDEN = {".mp4", ".mov", ".mkv", ".avi", ".wav", ".m4a", ".mp3", ".flac",
             ".zip", ".7z", ".ttf", ".otf", ".pem", ".key", ".log"}
SECRET = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{24,}|"
                    r"github_pat_[A-Za-z0-9_]{24,}|AKIA[0-9A-Z]{16}|"
                    r"-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----")
PERSONAL_PATH = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+|/(?:Users|home)/[^/\s\"']+")


def public_files() -> list[Path]:
    if (ROOT / ".git").exists():
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        names = [n.decode("utf-8") for n in raw.split(b"\0") if n]
        if not names:
            raise ValueError("Git index is empty; stage the intended public files before checking")
        return [ROOT / n for n in names]
    return [p for p in ROOT.rglob("*") if p.is_file() and not EXCLUDED.intersection(p.relative_to(ROOT).parts)
            and not any(part.endswith(".egg-info") for part in p.relative_to(ROOT).parts)]


def main() -> int:
    errors = []
    files = public_files()
    for path in files:
        name = path.relative_to(ROOT).as_posix()
        if path.is_symlink() or EXCLUDED.intersection(path.relative_to(ROOT).parts):
            errors.append(f"Private/generated directory or symlink tracked: {name}")
        if path.name.startswith(".env") or path.suffix.lower() in FORBIDDEN:
            errors.append(f"Private media, font, archive or secret-like file tracked: {name}")
        if path.stat().st_size > 2 * 1024**2:
            errors.append(f"Review unexpectedly large public file: {name}")
        try:
            body = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            errors.append(f"Unexpected binary public file: {name}")
            continue
        if SECRET.search(body):
            errors.append(f"Possible credential: {name}")
        if PERSONAL_PATH.search(body):
            errors.append(f"Personal absolute path: {name}")
    required = ["AGENTS.md", "README.md", "LICENSE", "pyproject.toml", "docs/workflow.md",
                "docs/asset-generation.md", "docs/qa.md", "prompts/README.md"]
    tracked = {p.relative_to(ROOT).as_posix() for p in files}
    errors += [f"Missing public entrypoint: {n}" for n in required if n not in tracked]
    print(json.dumps({"ok": not errors, "files_scanned": len(files), "errors": errors,
                      "scope": "Tracked public files; heuristic scan is not a proof of all media rights."}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
