"""Read-only validation for portable, explicit sprite-video project files."""

from __future__ import annotations

import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath

from PIL import ImageColor


class ProjectError(ValueError):
    """A project file does not satisfy the public project schema."""


def _number(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _color(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        ImageColor.getrgb(value)
        return True
    except (ValueError, TypeError):
        return False


def resolve_relative(root, value, *, directory=False) -> Path:
    """Resolve an existing portable path, rejecting traversal and symlink escapes."""
    if not isinstance(value, str) or not value.strip():
        raise ProjectError("path must be a nonempty relative string")
    if "\\" in value or PureWindowsPath(value).drive or PurePosixPath(value).is_absolute():
        raise ProjectError("path must use a relative forward-slash path")
    parts = PurePosixPath(value).parts
    if not parts or any(part in ("..", ".") for part in parts) or "\x00" in value:
        raise ProjectError("path must not contain traversal components")
    root_path = Path(root).resolve()
    try:
        resolved = (root_path / value).resolve(strict=True)
        resolved.relative_to(root_path)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ProjectError("path is missing or escapes the project root") from exc
    if directory and not resolved.is_dir():
        raise ProjectError("path must name an existing directory")
    if not directory and not resolved.is_file():
        raise ProjectError("path must name an existing file")
    if directory:
        try:
            for child in resolved.rglob("*"):
                target = child.resolve(strict=True)
                target.relative_to(root_path)
                if child.is_dir() and target != child.absolute():
                    raise ProjectError("directory paths must not contain directory links")
        except (OSError, ValueError, RuntimeError) as exc:
            raise ProjectError("directory contains a missing or escaped path") from exc
    return resolved


def validate_project(data, root) -> list[str]:
    """Return all useful schema errors without editing data or the filesystem."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["project must be an object"]

    def error(label, message):
        errors.append(f"{label}: {message}")

    def finite(value, label, *, positive=False, nonnegative=False):
        if not _number(value):
            error(label, "must be a finite number")
            return False
        if positive and value <= 0:
            error(label, "must be positive")
            return False
        if nonnegative and value < 0:
            error(label, "must be nonnegative")
            return False
        return True

    def path(value, label, directory=False):
        try:
            resolve_relative(root, value, directory=directory)
        except ProjectError as exc:
            error(label, str(exc))

    if not _integer(data.get("schema_version")) or data["schema_version"] != 1:
        error("schema_version", "must equal 1")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        error("name", "must be a nonempty string")
    duration = data.get("duration_seconds")
    duration_ok = finite(duration, "duration_seconds", positive=True)
    finite(data.get("fps"), "fps", positive=True)
    canvas = data.get("canvas")
    if not isinstance(canvas, dict):
        error("canvas", "must be an object")
    else:
        for key in ("width", "height"):
            if not _integer(canvas.get(key)) or canvas[key] <= 0 or canvas[key] % 2:
                error(f"canvas.{key}", "must be a positive even integer")
        if not _color(canvas.get("background")):
            error("canvas.background", "must be a valid color")
    assets = data.get("assets")
    if not isinstance(assets, dict):
        error("assets", "must be an object")
        assets = {}
    for name, asset in assets.items():
        label = f"assets.{name}"
        if not isinstance(name, str) or not name.strip():
            error(label, "asset identifiers must be nonempty strings")
        if not isinstance(asset, dict):
            error(label, "must be an object")
            continue
        path(asset.get("path"), f"{label}.path")
        if asset.get("kind") not in ("image", "video"):
            error(f"{label}.kind", "must be image or video")
        for key in ("license", "source"):
            if key in asset and (not isinstance(asset[key], str) or not asset[key].strip()):
                error(f"{label}.{key}", "must be a nonempty string")
        for key in ("baked", "loop"):
            if key in asset and not isinstance(asset[key], bool):
                error(f"{label}.{key}", "must be a boolean")
        if "source_offset_seconds" in asset:
            finite(asset["source_offset_seconds"], f"{label}.source_offset_seconds", nonnegative=True)
        if asset.get("end_behavior", "error") not in ("error", "hold"):
            error(f"{label}.end_behavior", "must be error or hold")
    audio = data.get("audio")
    if audio is not None:
        if not isinstance(audio, dict):
            error("audio", "must be an object or null")
        else:
            path(audio.get("path"), "audio.path")
            finite(audio.get("offset_seconds", 0.0), "audio.offset_seconds", nonnegative=True)
    captions = data.get("captions")
    if captions is not None:
        if not isinstance(captions, dict):
            error("captions", "must be an object or null")
        else:
            path(captions.get("path"), "captions.path")
            if not isinstance(captions.get("baked", False), bool):
                error("captions.baked", "must be a boolean")
            if "fonts_dir" in captions:
                path(captions["fonts_dir"], "captions.fonts_dir", directory=True)
    if "support_files" in data:
        if not isinstance(data["support_files"], list):
            error("support_files", "must be an array of relative file paths")
        else:
            for index, value in enumerate(data["support_files"]):
                path(value, f"support_files[{index}]")

    def window(item, label, parent_start=0.0, parent_end=duration):
        start, end = item.get("start"), item.get("end")
        valid = finite(start, f"{label}.start", nonnegative=True)
        valid = finite(end, f"{label}.end", positive=True) and valid
        if valid:
            if start >= end:
                error(label, "start must be before end")
            if duration_ok and (start < parent_start or end > parent_end):
                error(label, "window must lie inside its parent window")
        return valid

    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        error("scenes", "must be an array")
        scenes = []
    windows = []
    scene_ids = set()
    for index, scene in enumerate(scenes):
        label = f"scenes[{index}]"
        if not isinstance(scene, dict):
            error(label, "must be an object")
            continue
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip() or scene_id in scene_ids:
            error(f"{label}.id", "must be a unique nonempty string")
        else:
            scene_ids.add(scene_id)
        valid_window = window(scene, label)
        priority = scene.get("priority", 0)
        if not _integer(priority):
            error(f"{label}.priority", "must be an integer")
        elif valid_window and scene["start"] < scene["end"]:
            windows.append((scene["start"], scene["end"], priority, label))
        if "background" in scene and not _color(scene["background"]):
            error(f"{label}.background", "must be a valid color")
        layers = scene.get("layers")
        if not isinstance(layers, list):
            error(f"{label}.layers", "must be an array")
            continue
        for layer_index, layer in enumerate(layers):
            layer_label = f"{label}.layers[{layer_index}]"
            if not isinstance(layer, dict):
                error(layer_label, "must be an object")
                continue
            asset_name = layer.get("asset")
            if not isinstance(asset_name, str) or asset_name not in assets:
                error(f"{layer_label}.asset", "must refer to an existing asset")
            if valid_window:
                window(layer, layer_label, scene["start"], scene["end"])
            else:
                window(layer, layer_label)
            if not _integer(layer.get("z", 0)):
                error(f"{layer_label}.z", "must be an integer")
            if layer.get("effect", "normal") not in ("normal", "negative", "white_outline"):
                error(f"{layer_label}.effect", "must be normal, negative or white_outline")
            if "outline_width" in layer and (not _integer(layer["outline_width"]) or not 1 <= layer["outline_width"] <= 64):
                error(f"{layer_label}.outline_width", "must be an integer from 1 through 64")
            clock = layer.get("clock")
            period = None
            if clock is not None:
                if not isinstance(clock, dict):
                    error(f"{layer_label}.clock", "must be an object")
                else:
                    if finite(clock.get("period"), f"{layer_label}.clock.period", positive=True):
                        period = clock["period"]
                    finite(clock.get("epoch", 0.0), f"{layer_label}.clock.epoch")
                    finite(clock.get("shift", 0.0), f"{layer_label}.clock.shift")
            keys = layer.get("keyframes")
            if not isinstance(keys, list) or not keys:
                error(f"{layer_label}.keyframes", "must be a nonempty array")
                continue
            previous = None
            for key_index, key in enumerate(keys):
                key_label = f"{layer_label}.keyframes[{key_index}]"
                if not isinstance(key, dict):
                    error(key_label, "must be an object")
                    continue
                time = key.get("time")
                if finite(time, f"{key_label}.time", nonnegative=True):
                    if previous is not None and time <= previous:
                        error(f"{key_label}.time", "must be strictly increasing")
                    previous = time
                    if period is not None and time > period:
                        error(f"{key_label}.time", "must not exceed the loop period")
                    elif clock is None and duration_ok and time > duration:
                        error(f"{key_label}.time", "must not exceed project duration")
                for name in ("x", "y", "rotation", "opacity", "scale"):
                    if name in key and finite(key[name], f"{key_label}.{name}", positive=name == "scale"):
                        if name == "opacity" and not 0 <= key[name] <= 1:
                            error(f"{key_label}.opacity", "must be between 0 and 1")
                if "asset" in key and (not isinstance(key["asset"], str) or key["asset"] not in assets):
                    error(f"{key_label}.asset", "must refer to an existing asset")
    for index, (start, end, priority, label) in enumerate(windows):
        for other_start, other_end, other_priority, other_label in windows[index + 1:]:
            if priority == other_priority and max(start, other_start) < min(end, other_end):
                error(label, f"overlaps {other_label} at the same priority")
    return errors


def load_project(path) -> dict:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            data = json.load(source)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"Cannot read project {path}: {exc}") from exc
    errors = validate_project(data, path.parent)
    if errors:
        raise ProjectError("Invalid project:\n" + "\n".join(errors))
    return data
