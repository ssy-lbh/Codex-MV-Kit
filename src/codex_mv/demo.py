"""Generate original geometric markers and a synthetic click track, not a finished MV."""
from __future__ import annotations

import json
import math
import copy
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def create_demo(destination: str | Path, *, duration: float = 4.0) -> Path:
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    if not math.isfinite(duration) or duration < 2:
        raise ValueError("Demo duration must be at least two seconds")
    destination.mkdir(parents=True)
    assets_dir = destination / "assets"
    assets_dir.mkdir()
    assets = {}
    for name, color, shape, blink in [("marker-a", "#5eead4", "circle", False),
                                       ("marker-a-blink", "#5eead4", "circle", True),
                                       ("marker-b", "#fbbf24", "diamond", False)]:
        image = Image.new("RGBA", (128, 128))
        draw = ImageDraw.Draw(image)
        if shape == "circle":
            draw.ellipse((14, 14, 114, 114), fill=color, outline="white", width=4)
        else:
            draw.polygon([(64, 8), (120, 64), (64, 120), (8, 64)], fill=color, outline="white", width=4)
        for x in [46, 80]:
            if blink:
                draw.line((x - 5, 56, x + 5, 56), fill="#0f172a", width=4)
            else:
                draw.ellipse((x - 4, 48, x + 4, 60), fill="#0f172a")
        draw.arc((48, 60, 80, 84), 0, 180, fill="#0f172a", width=3)
        path = assets_dir / f"{name}.png"
        image.save(path)
        assets[name] = {"path": path.relative_to(destination).as_posix(), "kind": "image",
                        "license": "MIT", "source": "Original procedural toolkit validation marker; no AI image generation"}
    rate = 22050
    samples = np.zeros(round(duration * rate), dtype=np.float64)
    # 120 BPM clicks with a fixed global phase; no borrowed melody or lyrics.
    for onset in np.arange(0.125, duration, 0.5):
        start = round(onset * rate)
        n = min(round(0.04 * rate), len(samples) - start)
        t = np.arange(n) / rate
        samples[start:start + n] += 0.45 * np.sin(2 * np.pi * 880 * t) * np.exp(-t * 85)
    with wave.open(str(assets_dir / "clicks.wav"), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((samples * 32767).astype("<i2").tobytes())
    keys_a = [{"time": 0, "x": 160, "y": 190, "scale": 1, "rotation": 0, "opacity": 1},
              {"time": 0.7, "x": 210, "y": 110, "scale": 1, "rotation": -12, "opacity": 1,
               "asset": "marker-a-blink"},
              {"time": 1.2, "x": 160, "y": 190, "scale": 1, "rotation": 0, "opacity": 1,
               "asset": "marker-a"},
              {"time": 2, "x": 160, "y": 190, "scale": 1, "rotation": 0, "opacity": 1}]
    layers = [{"asset": "marker-a", "z": 10, "start": 0, "end": duration,
               "clock": {"epoch": 0, "shift": 0, "period": 2}, "keyframes": keys_a},
              {"asset": "marker-b", "z": 20, "start": 0, "end": duration,
               "clock": {"epoch": 0, "shift": 0.5, "period": 2},
               "keyframes": [{"time": 0, "x": 470, "y": 190, "scale": 1, "rotation": 0, "opacity": 1},
                             {"time": 1, "x": 430, "y": 120, "scale": 1, "rotation": 20, "opacity": 1},
                             {"time": 2, "x": 470, "y": 190, "scale": 1, "rotation": 0, "opacity": 1}]}]
    captions = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,sans-serif,24,&H00FFFFFF,&H00FFFFFF,&H00111111,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,15,15,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.90,Default,,0,0,0,,SYNTHETIC DEMO - SHARED CLOCK
Dialogue: 0,0:00:01.90,0:00:04.00,Default,,0,0,0,,REAL ASSETS GO IN YOUR PRIVATE WORKSPACE
"""
    (destination / "captions.ass").write_text(captions, encoding="utf-8")
    scenes = [{"id": "base", "start": 0, "end": duration, "priority": 0,
               "background": "#243654", "layers": layers}]
    if duration > 2:
        alternate = copy.deepcopy(layers)
        for layer in alternate:
            layer.update(start=2, end=min(3, duration))
        scenes.append({"id": "alternate-background", "start": 2, "end": min(3, duration),
                       "priority": 10, "background": "#56334b", "layers": alternate})
    data = {"schema_version": 1, "name": "synthetic-demo", "duration_seconds": duration,
            "fps": 30, "canvas": {"width": 640, "height": 360, "background": "#111827"},
            "audio": {"path": "assets/clicks.wav", "offset_seconds": 0}, "assets": assets,
            "captions": {"path": "captions.ass", "baked": False},
            "scenes": scenes,
            "support_files": ["asset-provenance.json"]}
    (destination / "asset-provenance.json").write_text(json.dumps({"license": "MIT", "generated_with": "demo.py / Pillow and NumPy",
        "music": "synthetic clicks, no third-party music", "characters": "abstract technical markers, not a production character design",
        "native_motion_accuracy": "not applicable; original synthetic motion"}, indent=2), encoding="utf-8")
    project = destination / "project.json"
    project.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return project
