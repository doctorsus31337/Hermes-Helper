import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from hermes_control.updater import UpdateInfo, is_newer, stage_update, version_tuple


class UpdaterTests(unittest.TestCase):
    def test_versions_compare_numerically(self):
        self.assertEqual(version_tuple("v1.2.10"), (1, 2, 10))
        self.assertTrue(is_newer("1.10.0", "1.9.9"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))

    def test_stage_accepts_one_valid_project(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("Hermes-Helper/install.sh", "#!/bin/sh\n")
                archive.writestr("Hermes-Helper/launcher.py", "")
                archive.writestr("Hermes-Helper/hermes_control/__init__.py", "")
            with mock.patch("hermes_control.updater.state_dir", return_value=Path(temp) / "state"):
                staged = stage_update(package)
            self.assertTrue((staged / "install.sh").is_file())

    def test_stage_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "bad.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../escape", "bad")
            with mock.patch("hermes_control.updater.state_dir", return_value=Path(temp) / "state"):
                with self.assertRaises(RuntimeError):
                    stage_update(package)
            self.assertFalse((Path(temp) / "escape").exists())


if __name__ == "__main__":
    unittest.main()

