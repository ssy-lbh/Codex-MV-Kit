import copy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from codex_mv.project import ProjectError, load_project, validate_project


def project_fixture():
    return {"schema_version": 1, "name": "test", "duration_seconds": 2.0, "fps": 10,
            "canvas": {"width": 64, "height": 48, "background": "#111827"},
            "assets": {"red": {"path": "sprite.png", "kind": "image", "license": "CC0-1.0"}},
            "scenes": [{"id": "base", "start": 0.0, "end": 2.0, "priority": 0,
                        "layers": [{"asset": "red", "start": 0.0, "end": 2.0, "z": 0,
                                    "keyframes": [{"time": 0, "x": 32, "y": 24, "scale": 1.0},
                                                  {"time": 2, "x": 40, "y": 24, "scale": 1.0}]}]}]}


class ProjectValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mv_project_test_")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(self.root / "sprite.png")
        self.data = project_fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_data_is_not_mutated_and_validation_writes_nothing(self):
        original = copy.deepcopy(self.data)
        before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in self.root.rglob("*")}
        self.assertEqual(validate_project(self.data, self.root), [])
        self.assertEqual(self.data, original)
        self.assertEqual(before, {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in self.root.rglob("*")})

    def test_nonfinite_boolean_and_nonpositive_values_are_rejected(self):
        for field, values in (("fps", [0, -1, float("nan"), float("inf"), True, 10 ** 1000]),
                              ("duration_seconds", [0, -1, float("nan"), False])):
            for value in values:
                with self.subTest(field=field, value=str(value)[:30]):
                    data = copy.deepcopy(self.data)
                    data[field] = value
                    self.assertTrue(validate_project(data, self.root))
        for value in (63, 0, True, 64.0):
            self.data["canvas"]["width"] = value
            self.assertTrue(validate_project(self.data, self.root))

    def test_traversal_absolute_and_missing_paths_are_rejected(self):
        for path in ("../sprite.png", "/absolute.png", "C:/absolute.png", "C:relative.png", "assets\\sprite.png", "missing.png"):
            self.data["assets"]["red"]["path"] = path
            with self.subTest(path=path):
                self.assertTrue(any("assets.red.path" in error for error in validate_project(self.data, self.root)))

    def test_symlink_escape_is_rejected(self):
        outside = Path(self.temporary.name) / "outside.png"
        outside.write_bytes((self.root / "sprite.png").read_bytes())
        link = self.root / "link.png"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        self.data["assets"]["red"]["path"] = "link.png"
        self.assertTrue(validate_project(self.data, self.root))

    def test_schema_version_requires_integer(self):
        for version in (1.0, True, "1", 2):
            self.data["schema_version"] = version
            self.assertTrue(any("schema_version" in error for error in validate_project(self.data, self.root)))

    def test_scene_priority_overlap_and_half_open_boundaries(self):
        second = copy.deepcopy(self.data["scenes"][0])
        second.update(id="second", start=1.0)
        second["layers"][0]["start"] = 1.0
        self.data["scenes"].append(second)
        self.assertTrue(any("same priority" in error for error in validate_project(self.data, self.root)))
        second["priority"] = 1
        self.assertEqual(validate_project(self.data, self.root), [])
        second["priority"] = 0
        self.data["scenes"][0]["end"] = 1.0
        self.data["scenes"][0]["layers"][0]["end"] = 1.0
        self.assertEqual(validate_project(self.data, self.root), [])

    def test_layer_ranges_key_order_cels_and_effect_parameters(self):
        layer = self.data["scenes"][0]["layers"][0]
        layer["end"] = 3.0
        layer["keyframes"] = [{"time": 1, "scale": 0, "opacity": 2}, {"time": 0, "x": float("inf"), "asset": "missing"}]
        layer["clock"] = {"epoch": 0, "shift": 0, "period": 0}
        layer["effect"] = "nonuniform_warp"
        errors = validate_project(self.data, self.root)
        for expected in ("parent window", "strictly increasing", "scale", "opacity", "existing asset", "clock.period", "effect"):
            self.assertTrue(any(expected in error for error in errors), (expected, errors))

    def test_loading_and_fonts_directory_validation(self):
        (self.root / "captions.ass").write_text("[Script Info]\n", encoding="utf-8")
        (self.root / "fonts").mkdir()
        self.data["captions"] = {"path": "captions.ass", "baked": False, "fonts_dir": "fonts"}
        path = self.root / "project.json"
        path.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertEqual(load_project(path), self.data)
        self.data["captions"]["fonts_dir"] = "sprite.png"
        path.write_text(json.dumps(self.data), encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "existing directory"):
            load_project(path)
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "Cannot read project"):
            load_project(path)


if __name__ == "__main__":
    unittest.main()
