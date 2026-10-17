import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from codex_mv.archive import ArchiveError, file_sha256, pack_project, restore_archive, safe_name, verify_archive
from codex_mv.demo import create_demo
from codex_mv.project import load_project


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_bundle_")
        self.root = Path(self.temp.name)
        self.project = create_demo(self.root / "inputs", duration=2.0)
        self.bundle = self.root / "project.zip"

    def tearDown(self):
        self.temp.cleanup()

    def test_declared_inputs_only_real_restore_and_no_overwrite(self):
        (self.project.parent / "unlisted-private.txt").write_text("not an input", encoding="utf-8")
        result = pack_project(self.project, self.bundle)
        self.assertTrue(result["ok"])
        self.assertNotIn("unlisted-private.txt", {x["path"] for x in result["manifest"]["files"]})
        target = self.root / "restored"
        restored = restore_archive(self.bundle, target)
        self.assertTrue(restored["restored_hashes_verified"])
        self.assertEqual(load_project(target / "project.json"), load_project(self.project))
        for record in restored["manifest"]["files"]:
            self.assertEqual(file_sha256(target / record["path"]), record["sha256"])
        original = file_sha256(self.bundle)
        with self.assertRaises(FileExistsError):
            pack_project(self.project, self.bundle)
        with self.assertRaises(FileExistsError):
            restore_archive(self.bundle, target)
        self.assertEqual(file_sha256(self.bundle), original)

    def test_empty_fonts_directory_survives_bundle(self):
        data = load_project(self.project)
        (self.project.parent / "fonts").mkdir()
        data["captions"]["fonts_dir"] = "fonts"
        self.project.write_text(json.dumps(data), encoding="utf-8")
        pack_project(self.project, self.bundle)
        restored = self.root / "with-empty-fonts"
        restore_archive(self.bundle, restored)
        self.assertTrue((restored / "fonts").is_dir())
        load_project(restored / "project.json")

    def test_hash_corruption_rejected_before_any_extraction(self):
        pack_project(self.project, self.bundle)
        corrupt = self.root / "corrupt.zip"
        with zipfile.ZipFile(self.bundle) as original, zipfile.ZipFile(corrupt, "x") as changed:
            for info in original.infolist():
                body = original.read(info.filename)
                if info.filename == "assets/marker-a.png":
                    body = bytes([body[0] ^ 1]) + body[1:]
                changed.writestr(info.filename, body)
        with self.assertRaisesRegex(ArchiveError, "Hash mismatch"):
            restore_archive(corrupt, self.root / "rejected")
        self.assertFalse((self.root / "rejected").exists())

    def test_nonportable_and_traversal_paths_rejected(self):
        for name in ["../escape", "/escape", "a/../b", "a//b", "a\\b", "D:/x", "NUL", "con.txt",
                     "assets./sprite.png", "assets/sprite.png ", "COM1.png", "a\n/b"]:
            with self.subTest(name=name), self.assertRaises(ArchiveError):
                safe_name(name)
        with zipfile.ZipFile(self.bundle, "x") as z:
            z.writestr("../outside", "never extract")
        with self.assertRaises(ArchiveError):
            restore_archive(self.bundle, self.root / "unsafe")
        self.assertFalse((self.root / "unsafe").exists())
        self.assertFalse((self.root / "outside").exists())

    def test_malformed_manifest_is_a_controlled_error(self):
        for number, data in enumerate([[], {"schema_version": 1},
                                       {"schema_version": 1, "files": [{"path": "a"}]},
                                       {"schema_version": 1, "files": [], "project_file": "project.json"}]):
            path = self.root / f"bad-{number}.zip"
            with zipfile.ZipFile(path, "x") as z:
                z.writestr("bundle-manifest.json", json.dumps(data))
            with self.subTest(number=number), self.assertRaises(ArchiveError):
                verify_archive(path)

    def test_directory_spelling_cannot_alias_across_platforms(self):
        pack_project(self.project, self.bundle)
        ambiguous = self.root / "ambiguous.zip"
        with zipfile.ZipFile(self.bundle) as original, zipfile.ZipFile(ambiguous, "x") as changed:
            for info in original.infolist():
                body = original.read(info.filename)
                name = info.filename
                if name == "assets/marker-a.png":
                    name = "Assets/marker-a.png"
                elif name == "bundle-manifest.json":
                    manifest = json.loads(body)
                    for record in manifest["files"]:
                        if record["path"] == "assets/marker-a.png":
                            record["path"] = "Assets/marker-a.png"
                    body = json.dumps(manifest).encode("utf-8")
                changed.writestr(name, body)
        with self.assertRaisesRegex(ArchiveError, "Case-colliding path component"):
            verify_archive(ambiguous)


if __name__ == "__main__":
    unittest.main()
