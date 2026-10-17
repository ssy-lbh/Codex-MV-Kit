"""Self-contained input bundles; never archive an entire working directory blindly."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import __version__
from .project import load_project

MANIFEST = "bundle-manifest.json"
MAX_FILES = 10000
MAX_BYTES = 10 * 1024**3


class ArchiveError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ArchiveError("Bundle paths must be nonempty POSIX relative paths")
    name = PurePosixPath(value)
    if name.is_absolute() or ":" in value or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ArchiveError(f"Unsafe bundle path: {value!r}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)), "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
    if any(p.endswith((".", " ")) or p.split(".")[0].upper() in reserved
           or any(c in p for c in '<>"|?*') for p in name.parts):
        raise ArchiveError(f"Nonportable bundle path: {value!r}")
    return str(name)


def _input_names(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    files = [value["path"] for value in data.get("assets", {}).values()]
    if data.get("audio"):
        files.append(data["audio"]["path"])
    captions = data.get("captions") or {}
    if captions.get("path"):
        files.append(captions["path"])
    files.extend(data.get("support_files", []))
    dirs = [captions["fonts_dir"]] if captions.get("fonts_dir") else []
    return [safe_name(x) for x in files], [safe_name(x) for x in dirs]


def _local_file(root: Path, name: str) -> Path:
    value = root.joinpath(*PurePosixPath(safe_name(name)).parts)
    if not value.resolve().is_relative_to(root.resolve()):
        raise ArchiveError(f"Input escapes project directory: {name}")
    for node in [value, *value.parents]:
        if node == root:
            break
        if node.is_symlink():
            raise ArchiveError(f"Symlink input is not supported: {name}")
    if not value.is_file():
        raise ArchiveError(f"Input is missing: {name}")
    return value


def _portable_tree(files: list[str], directories: list[str]) -> None:
    spelling: dict[str, str] = {}
    file_keys = {n.casefold() for n in files}
    for name in [*files, *directories]:
        parts = PurePosixPath(name).parts
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            key = prefix.casefold()
            if key in spelling and spelling[key] != prefix:
                raise ArchiveError(f"Case-colliding path component: {prefix}")
            spelling[key] = prefix
            if depth < len(parts) and key in file_keys:
                raise ArchiveError("A file is used as a parent directory")
    if any(n.casefold() in file_keys for n in directories):
        raise ArchiveError("A declared directory collides with a file")


def collect_inputs(project_file: Path | str) -> dict[str, Path]:
    project_file = Path(project_file).resolve()
    data = load_project(project_file)
    root = project_file.parent
    names, dirs = _input_names(data)
    names.append(project_file.name)
    for directory in dirs:
        base = root / directory
        if base.is_symlink() or not base.resolve().is_relative_to(root) or not base.is_dir():
            raise ArchiveError(f"Unsafe or missing fonts directory: {directory}")
        names.extend(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())
    result: dict[str, Path] = {}
    casefold_names: dict[str, str] = {}
    for name in names:
        name = safe_name(name)
        if name.casefold() == MANIFEST:
            raise ArchiveError(f"Reserved bundle name: {name}")
        if name.casefold() in casefold_names and casefold_names[name.casefold()] != name:
            raise ArchiveError(f"Case-colliding input paths: {name}")
        if Path(name).name.lower().startswith(".env") or Path(name).suffix.lower() in {".key", ".pem"}:
            raise ArchiveError(f"Secret-like input is excluded: {name}")
        casefold_names[name.casefold()] = name
        result[name] = _local_file(root, name)
    if len(result) > MAX_FILES or sum(p.stat().st_size for p in result.values()) > MAX_BYTES:
        raise ArchiveError("Input bundle exceeds the configured safety limits")
    _portable_tree(list(result), dirs)
    return result


def pack_project(project_file: Path | str, output: Path | str) -> dict[str, Any]:
    project_file, output = Path(project_file).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    files = collect_inputs(project_file)
    if output in files.values():
        raise ArchiveError("Archive output is also a project input")
    records = [{"path": n, "bytes": p.stat().st_size, "sha256": file_sha256(p)}
               for n, p in sorted(files.items())]
    _, directories = _input_names(load_project(project_file))
    manifest = {"schema_version": 1, "toolkit_version": __version__,
                "project_file": project_file.name, "files": records,
                "directories": sorted(set(directories)),
                "scope": "All declared project inputs; rendered outputs are not included unless used as inputs.",
                "visual_acceptance": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for name, path in sorted(files.items()):
            bundle.write(path, name)
        bundle.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    report = verify_archive(output)
    report.update(archive=str(output), archive_sha256=file_sha256(output))
    return report


def _verify_open(bundle: zipfile.ZipFile, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    infos = bundle.infolist()
    if len(infos) > MAX_FILES + 1 or sum(i.file_size for i in infos) > max_bytes:
        raise ArchiveError("Bundle exceeds file-count or uncompressed-size limit")
    actual: dict[str, zipfile.ZipInfo] = {}
    folded: set[str] = set()
    for info in infos:
        name = safe_name(info.filename)
        if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
            raise ArchiveError("Directory and symlink ZIP entries are not accepted")
        if name.casefold() in folded:
            raise ArchiveError(f"Duplicate/case-colliding ZIP entry: {name}")
        folded.add(name.casefold())
        actual[name] = info
    if MANIFEST not in actual or actual[MANIFEST].file_size > 8 * 1024**2:
        raise ArchiveError("Missing or oversized bundle manifest")
    try:
        manifest = json.loads(bundle.read(MANIFEST))
        if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise ArchiveError("Unsupported bundle manifest version")
        records = manifest["files"]
        if not isinstance(records, list) or not all(isinstance(x, dict) for x in records):
            raise ArchiveError("Manifest files must be a list of records")
        expected = {safe_name(x["path"]): x for x in records}
        directories = [safe_name(n) for n in manifest.get("directories", [])]
        if not isinstance(manifest.get("directories", []), list):
            raise ArchiveError("Manifest directories must be a list")
        project_name = safe_name(manifest["project_file"])
        for record in records:
            if type(record["bytes"]) is not int or record["bytes"] < 0 or not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
                raise ArchiveError("Invalid file size or SHA-256 in manifest")
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("Malformed bundle manifest") from exc
    if len(expected) != len(records) or MANIFEST in expected or set(actual) != set(expected) | {MANIFEST}:
        raise ArchiveError("ZIP entries differ from the manifest")
    all_files = {n.casefold() for n in actual}
    if len({n.casefold() for n in directories}) != len(directories):
        raise ArchiveError("Case-colliding manifest directories")
    for name in [*actual, *directories]:
        if any(str(p).casefold() in all_files for p in PurePosixPath(name).parents if str(p) != "."):
            raise ArchiveError("A file is used as a parent directory")
    if any(n.casefold() in all_files for n in directories):
        raise ArchiveError("A declared directory collides with a file")
    _portable_tree(list(actual), directories)
    for name, record in expected.items():
        if actual[name].file_size != record["bytes"]:
            raise ArchiveError(f"Size mismatch: {name}")
        with bundle.open(name) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != record["sha256"]:
            raise ArchiveError(f"Hash mismatch: {name}")
    if project_name not in expected:
        raise ArchiveError("Project JSON is not included")
    try:
        project = json.loads(bundle.read(project_name))
        if not isinstance(project, dict):
            raise ArchiveError("Bundled project must be a JSON object")
        paths, dirs = _input_names(project)
    except (AttributeError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("Malformed bundled project") from exc
    prefix = PurePosixPath(project_name).parent
    for name in paths:
        if str(prefix / name) not in expected:
            raise ArchiveError(f"Referenced input missing from bundle: {name}")
    for directory in dirs:
        if str(prefix / directory) not in directories and not any(n.startswith(str(prefix / directory) + "/") for n in expected):
            raise ArchiveError(f"Referenced fonts directory is empty/missing: {directory}")
    return {"ok": True, "files": len(expected), "total_input_bytes": sum(x["bytes"] for x in records),
            "project_file": project_name, "manifest": manifest, "visual_acceptance": False}


def verify_archive(path: Path | str, *, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as bundle:
            return _verify_open(bundle, max_bytes)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("Invalid ZIP or CRC failure") from exc


def restore_archive(path: Path | str, destination: Path | str) -> dict[str, Any]:
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    with zipfile.ZipFile(path) as bundle:
        report = _verify_open(bundle)
        destination.mkdir(parents=True, exist_ok=False)
        for name in report["manifest"].get("directories", []):
            destination.joinpath(*PurePosixPath(name).parts).mkdir(parents=True, exist_ok=True)
        # On I/O failure a partial NEW directory remains for inspection; never remove user files.
        for info in bundle.infolist():
            output = destination.joinpath(*PurePosixPath(info.filename).parts)
            if not output.resolve().is_relative_to(destination):
                raise ArchiveError("Extraction escaped destination")
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, output.open("xb") as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
        for record in report["manifest"]["files"]:
            if file_sha256(destination / record["path"]) != record["sha256"]:
                raise ArchiveError(f"Restored hash mismatch: {record['path']}")
    report.update(destination=str(destination), restored_hashes_verified=True)
    return report
