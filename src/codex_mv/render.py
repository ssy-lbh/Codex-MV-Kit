"""Deterministic sprite composition and exact-frame FFmpeg rendering."""

from __future__ import annotations

from contextlib import ExitStack
from decimal import Decimal, ROUND_CEILING
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import wave

from PIL import Image, ImageChops, ImageColor, ImageFilter

from .media import BoundedVideoReader, MediaError, Toolchain, probe
from .project import ProjectError, load_project, resolve_relative


_PROPERTIES = ("x", "y", "scale", "rotation", "opacity")
_ENCODER_EOF_TIMEOUT = 5.0
_ENCODER_STOP_TIMEOUT = 3.0


def _frame_count(duration, fps):
    return int((Decimal(str(duration)) * Decimal(str(fps))).to_integral_value(rounding=ROUND_CEILING))


def _run(command, *, cwd):
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MediaError(f"Cannot execute media tool: {exc}") from exc
    if result.returncode:
        raise MediaError(f"FFmpeg/FFprobe failed: {result.stderr.strip()[-6000:]}")
    return result.stdout


def _keys(layer, width, height):
    current = {"x": width / 2, "y": height / 2, "scale": 1.0,
               "rotation": 0.0, "opacity": 1.0, "asset": layer["asset"]}
    result = []
    for key in layer["keyframes"]:
        current = {**current, **key}
        result.append(current)
    return result


def _state(layer, keys, global_time):
    clock = layer.get("clock")
    time = ((global_time - clock.get("epoch", 0.0) + clock.get("shift", 0.0))
            % clock["period"]) if clock else global_time
    left = keys[0]
    right = left
    for key in keys:
        if key["time"] <= time:
            left = right = key
        else:
            right = key
            break
    fraction = 0.0
    if right["time"] > left["time"] and time > left["time"]:
        fraction = min(1.0, (time - left["time"]) / (right["time"] - left["time"]))
    state = {name: left[name] + (right[name] - left[name]) * fraction for name in _PROPERTIES}
    state["asset"] = left["asset"]
    return state, time


def _effect(image, layer):
    effect = layer.get("effect", "normal")
    if effect == "negative":
        red, green, blue, alpha = image.split()
        return Image.merge("RGBA", (ImageChops.invert(red), ImageChops.invert(green),
                                    ImageChops.invert(blue), alpha))
    return image


def _sprite_effect(image, layer):
    if layer.get("effect") != "white_outline":
        return _effect(image, layer)
    # Fill the original alpha surface too, so the outline occludes lower layers.
    radius = layer.get("outline_width", 2)
    alpha = Image.new("L", (image.width + radius * 2, image.height + radius * 2))
    alpha.paste(image.getchannel("A"), (radius, radius))
    dilated = alpha.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    outlined = Image.new("RGBA", alpha.size, (255, 255, 255, 0))
    outlined.putalpha(dilated)
    interior = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    interior.putalpha(alpha)
    return Image.alpha_composite(outlined, interior)


def _place(canvas, image, layer, state):
    sprite = _sprite_effect(image, layer)
    size = (max(1, round(sprite.width * state["scale"])),
            max(1, round(sprite.height * state["scale"])))
    if size != sprite.size:
        sprite = sprite.resize(size, Image.Resampling.LANCZOS)
    if state["rotation"] % 360:
        sprite = sprite.rotate(state["rotation"], Image.Resampling.BICUBIC, expand=True)
    if state["opacity"] < 1:
        sprite.putalpha(sprite.getchannel("A").point(lambda value: round(value * state["opacity"])))
    position = (round(state["x"] - sprite.width / 2), round(state["y"] - sprite.height / 2))
    canvas.alpha_composite(sprite, position)


def _baked_video_in_window(project, start, end):
    for scene in project["scenes"]:
        if max(start, scene["start"]) >= min(end, scene["end"]):
            continue
        for layer in scene["layers"]:
            if max(start, layer["start"]) >= min(end, layer["end"]):
                continue
            names = {layer["asset"], *(key["asset"] for key in layer["keyframes"] if "asset" in key)}
            if any(project["assets"][name].get("baked", False)
                   and project["assets"][name]["kind"] == "video" for name in names):
                return True
    return False


def _stop_encoder_tree(encoder, *, force=False):
    """Stop the owned encoder and wrappers without leaving their children running."""
    if os.name == "nt":
        # A package-manager shim can own FFmpeg, which also inherits stderr.
        result = subprocess.run(["taskkill", "/PID", str(encoder.pid), "/T", "/F"],
                                capture_output=True, timeout=_ENCODER_STOP_TIMEOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode and encoder.poll() is None:
            detail = result.stderr.decode("mbcs", errors="replace").strip()
            raise MediaError(f"Cannot stop encoder process tree: {detail}")
    else:
        try:
            os.killpg(encoder.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass


def _finish_failed_encoder(encoder):
    """Give EOF a bounded chance to drain through wrappers before stopping them."""
    try:
        encoder.wait(timeout=_ENCODER_EOF_TIMEOUT)
        return
    except subprocess.TimeoutExpired:
        pass
    _stop_encoder_tree(encoder)
    try:
        encoder.wait(timeout=_ENCODER_STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        _stop_encoder_tree(encoder, force=True)
        encoder.wait(timeout=_ENCODER_STOP_TIMEOUT)


def _encode_frames(project, root, work, chain, start, count, hashes, video_samples):
    width, height = project["canvas"]["width"], project["canvas"]["height"]
    fps = project["fps"]
    command = [chain.ffmpeg, "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s:v", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
               "-frames:v", str(count), "silent.mp4"]
    with ExitStack() as stack:
        images = {}
        readers = {}
        layer_keys = {}
        for scene in project["scenes"]:
            for layer in scene["layers"]:
                layer_keys[id(layer)] = _keys(layer, width, height)
        error_file = work / "encoder.log"
        with error_file.open("wb") as error_log:
            try:
                process_options = ({"creationflags": subprocess.CREATE_NO_WINDOW}
                                   if os.name == "nt" else {"start_new_session": True})
                encoder = subprocess.Popen(command, cwd=work, stdin=subprocess.PIPE,
                                           stdout=subprocess.DEVNULL, stderr=error_log,
                                           **process_options)
            except OSError as exc:
                raise MediaError(f"Cannot start FFmpeg: {exc}") from exc
            try:
                for index in range(count):
                    global_time = float(Fraction(str(start)) + Fraction(index) / Fraction(str(fps)))
                    active = [scene for scene in project["scenes"]
                              if scene["start"] <= global_time < scene["end"]]
                    scene = max(active, key=lambda item: item.get("priority", 0)) if active else None
                    color = scene.get("background", project["canvas"]["background"]) if scene else project["canvas"]["background"]
                    canvas = Image.new("RGBA", (width, height), ImageColor.getrgb(color)[:3] + (255,))
                    if scene:
                        for layer in sorted(scene["layers"], key=lambda item: item.get("z", 0)):
                            if not layer["start"] <= global_time < layer["end"]:
                                continue
                            state, animation_time = _state(layer, layer_keys[id(layer)], global_time)
                            name = state["asset"]
                            asset = project["assets"][name]
                            if asset["kind"] == "image":
                                if name not in images:
                                    try:
                                        with Image.open(resolve_relative(root, asset["path"])) as source:
                                            images[name] = source.convert("RGBA")
                                    except (OSError, ValueError) as exc:
                                        raise MediaError(f"Cannot decode image asset {name}: {exc}") from exc
                                image = images[name]
                            else:
                                if name not in readers:
                                    readers[name] = stack.enter_context(BoundedVideoReader(resolve_relative(root, asset["path"])))
                                reader = readers[name]
                                source_time = animation_time if layer.get("clock") else global_time - layer["start"]
                                source_time += asset.get("source_offset_seconds", 0.0)
                                requested = math.floor(source_time * reader.fps + 1e-9)
                                source_index = requested
                                held = False
                                if asset.get("loop", False):
                                    source_index %= reader.frame_count
                                elif not 0 <= source_index < reader.frame_count:
                                    if asset.get("end_behavior", "error") != "hold":
                                        raise MediaError(f"Video asset {name} source overrun: requested frame {requested}, available {reader.frame_count}")
                                    source_index = min(reader.frame_count - 1, max(0, source_index))
                                    held = True
                                image = reader.get(source_index).convert("RGBA")
                                video_samples.append({"output_frame": index, "asset": name,
                                                      "requested_source_frame": requested,
                                                      "source_frame": source_index, "held": held})
                            _place(canvas, image, layer, state)
                    raw = canvas.convert("RGB").tobytes()
                    hashes.append(hashlib.sha256(raw).hexdigest())
                    encoder.stdin.write(raw)
                encoder.stdin.close()
                status = encoder.wait()
                if status:
                    raise MediaError(f"FFmpeg rawvideo encoding failed: {error_file.read_text(encoding='utf-8', errors='replace')[-6000:]}")
            except BaseException as exc:
                try:
                    encoder.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                try:
                    _finish_failed_encoder(encoder)
                except (OSError, subprocess.SubprocessError, MediaError) as cleanup_error:
                    exc.add_note(f"Encoder process cleanup failed: {cleanup_error}")
                    raise exc from cleanup_error
                if isinstance(exc, BrokenPipeError):
                    raise MediaError(f"FFmpeg stopped reading frames: {error_file.read_text(encoding='utf-8', errors='replace')[-6000:]}") from exc
                raise


def _selected_audio(project, root, work, chain, start, duration):
    audio = project.get("audio")
    if not audio:
        return None
    source = resolve_relative(root, audio["path"])
    metadata = probe(source, toolchain=chain)
    streams = [stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "audio"]
    if not streams:
        raise MediaError("Declared audio file has no audio stream")
    try:
        sample_rate = int(streams[0]["sample_rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaError("Declared audio has no reliable sample rate") from exc
    if sample_rate <= 0:
        raise MediaError("Declared audio has an invalid sample rate")
    expected_samples = round(duration * sample_rate)
    if expected_samples < 1:
        raise MediaError("Declared audio window is shorter than one audio sample")
    source_start = start + audio.get("offset_seconds", 0.0)
    _run([chain.ffmpeg, "-v", "error", "-n", "-ss", str(source_start), "-i", str(source),
          "-map", "0:a:0", "-vn", "-af", f"atrim=end_sample={expected_samples},asetpts=PTS-STARTPTS",
          "-ar", str(sample_rate), "-c:a", "pcm_s16le", "selected.wav"], cwd=work)
    with wave.open(str(work / "selected.wav"), "rb") as selected:
        actual_samples = selected.getnframes()
    if actual_samples != expected_samples:
        raise MediaError(f"Audio source window is short: expected {expected_samples} samples, decoded {actual_samples}")
    return {"source_start_seconds": source_start, "duration_seconds": duration,
            "sample_rate": sample_rate, "declared_samples": expected_samples,
            "decoded_source_window_samples": actual_samples}


def _verify_count(path, expected, chain, work):
    result = _run([chain.ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                   "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)], cwd=work)
    try:
        actual = int(json.loads(result)["streams"][0]["nb_read_frames"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise MediaError("Could not verify the final output frame count") from exc
    if actual != expected:
        raise MediaError(f"Output has {actual} frames; expected exactly {expected}")


def render_project(project_file, output, *, start=0.0, duration=None, toolchain=None, captions=True) -> dict:
    """Render a full project or crop without resetting the global animation clock.

    Output creation is exclusive. Intermediates and partial output created by
    this call are removed on failure; existing user files are never replaced.
    """
    project_file = Path(project_file).resolve()
    project = load_project(project_file)
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0:
        raise ProjectError("Render start must be a finite nonnegative number")
    if duration is None:
        duration = float(Decimal(str(project["duration_seconds"])) - Decimal(str(start)))
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ProjectError("Render duration must be a finite positive number")
    window_end = Decimal(str(start)) + Decimal(str(duration))
    project_end = Decimal(str(project["duration_seconds"]))
    if window_end - project_end > Decimal("1e-9"):
        raise ProjectError("Render window exceeds project duration")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite output: {output}")
    if not output.suffix:
        raise ProjectError("Output needs a media container extension, such as .mp4")
    caption_config = project.get("captions")
    burn_captions = bool(captions and caption_config and not caption_config.get("baked", False))
    if burn_captions and _baked_video_in_window(project, start, start + duration):
        raise ProjectError("Global captions would duplicate captions already baked into a video asset")
    chain = toolchain or Toolchain()
    count = _frame_count(duration, project["fps"])
    root = project_file.parent
    hashes, video_samples = [], []
    created_output = False
    try:
        with tempfile.TemporaryDirectory(prefix="codex_mv_") as temporary:
            work = Path(temporary)
            _encode_frames(project, root, work, chain, start, count, hashes, video_samples)
            visual = "silent.mp4"
            if burn_captions:
                shutil.copyfile(resolve_relative(root, caption_config["path"]), work / "captions.ass")
                ass = "ass=filename=captions.ass"
                if caption_config.get("fonts_dir"):
                    shutil.copytree(resolve_relative(root, caption_config["fonts_dir"], directory=True), work / "fonts")
                    ass += ":fontsdir=fonts"
                # ASS receives project time; the encoded crop still begins at PTS zero.
                filters = f"setpts=PTS+{start}/TB,{ass},setpts=PTS-STARTPTS"
                _run([chain.ffmpeg, "-v", "error", "-n", "-i", visual, "-vf", filters, "-an",
                      "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                      "-frames:v", str(count), "captioned.mp4"], cwd=work)
                visual = "captioned.mp4"
            audio_report = _selected_audio(project, root, work, chain, start, duration)
            final = work / ("result" + output.suffix.lower())
            command = [chain.ffmpeg, "-v", "error", "-n", "-i", visual]
            if audio_report:
                command += ["-i", "selected.wav", "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
            else:
                command += ["-map", "0:v:0", "-c:v", "copy", "-an"]
            # No output -t or -shortest: every one of ceil(duration * fps) frames survives.
            command += [str(final)]
            _run(command, cwd=work)
            _verify_count(final, count, chain, work)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as destination:
                created_output = True
                with final.open("rb") as source:
                    shutil.copyfileobj(source, destination)
    except BaseException:
        if created_output:
            output.unlink(missing_ok=True)
        raise
    return {"output": str(output), "frames": count, "expected_frames": count,
            "fps": project["fps"], "width": project["canvas"]["width"],
            "height": project["canvas"]["height"], "start_seconds": start,
            "duration_seconds": duration, "captions_rendered": burn_captions,
            "audio": audio_report, "input_frame_hashes": hashes,
            "video_frame_samples": video_samples,
            "held_video_frames": sum(sample["held"] for sample in video_samples),
            "video_sampling": "CFR nominal-fps native frame indices; native PTS not asserted",
            "clock": "global_time=start+frame/fps", "visual_acceptance": "not assessed"}
