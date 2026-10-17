import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import codex_mv


class CliEncodingTests(unittest.TestCase):
    def test_redirected_json_and_errors_are_utf8_with_legacy_locale(self):
        env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0",
               "PYTHONPATH": str(Path(codex_mv.__file__).resolve().parent.parent)}
        with tempfile.TemporaryDirectory(prefix="mv_cli_encoding_") as temporary:
            target = Path(temporary) / "中文工程"
            result = subprocess.run([sys.executable, "-m", "codex_mv", "demo", str(target),
                                     "--duration", "2"], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.decode("utf-8"))["project"], str(target / "project.json"))
            missing = target / "不存在.json"
            error = subprocess.run([sys.executable, "-m", "codex_mv", "validate", str(missing)],
                                   env=env, capture_output=True)
            self.assertEqual(error.returncode, 2)
            self.assertIn("不存在.json", error.stderr.decode("utf-8"))
            self.assertNotIn("UnicodeEncodeError", error.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
