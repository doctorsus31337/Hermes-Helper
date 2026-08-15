import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from hermes_control.updater import UpdateInfo, check_for_update, download_update, is_newer, launch_installer, stage_update, version_tuple


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class UpdaterTests(unittest.TestCase):
    def test_manifest_download_hash_stage_and_installer_launch_pipeline(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package_buffer = io.BytesIO()
            with zipfile.ZipFile(package_buffer, "w") as archive:
                archive.writestr("Hermes-Helper/install.sh", "#!/bin/sh\n")
                archive.writestr("Hermes-Helper/launcher.py", "")
                archive.writestr("Hermes-Helper/hermes_control/__init__.py", '__version__ = "1.0.5"\n')
            package = package_buffer.getvalue()
            digest = hashlib.sha256(package).hexdigest()
            manifest = json.dumps({
                "version": "1.0.5",
                "download_url": "https://example.test/Hermes-Helper-1.0.5.zip",
                "sha256": digest,
            }).encode()
            with mock.patch("hermes_control.updater.urllib.request.urlopen", side_effect=[FakeResponse(manifest), FakeResponse(package)]), \
                 mock.patch("hermes_control.updater.state_dir", return_value=root / "state"), \
                 mock.patch("hermes_control.updater.subprocess.Popen") as popen:
                info = check_for_update("https://example.test/update.json", "1.0.4")
                if info is None:
                    self.fail("Expected a newer update")
                downloaded = download_update(info)
                staged = stage_update(downloaded)
                launch_installer(staged)
            self.assertEqual(downloaded.read_bytes(), package)
            self.assertEqual(staged.name, "Hermes-Helper")
            popen.assert_called_once_with(
                ["bash", str(staged / "install.sh"), "--update", "--launch"],
                cwd=str(staged),
                start_new_session=True,
            )

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

