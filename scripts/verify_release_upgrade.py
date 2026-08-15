#!/usr/bin/env python3
"""Verify that the current installer upgrades the previous public release."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_control import __version__  # noqa: E402


BASELINE_VERSION = "1.0.5"
TARGET_VERSION = __version__


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")


def version_at(path: Path) -> str:
    source = (path / "hermes_control" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', source, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not read version from {path}")
    return match.group(1)


def main() -> None:
    archive = ROOT / "dist" / f"Hermes-Helper-{TARGET_VERSION}.zip"
    if not archive.is_file():
        raise RuntimeError(f"Build the release first: {archive}")
    with tempfile.TemporaryDirectory(prefix="hermes-helper-upgrade-") as raw_temp:
        temp = Path(raw_temp)
        baseline_zip = temp / "baseline.zip"
        with baseline_zip.open("wb") as handle:
            completed = subprocess.run(
                ["git", "archive", "--format=zip", f"v{BASELINE_VERSION}"],
                cwd=ROOT,
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
        baseline = temp / "baseline"
        current = temp / "current"
        baseline.mkdir()
        current.mkdir()
        with zipfile.ZipFile(baseline_zip) as bundle:
            bundle.extractall(baseline)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(current)
        current_source = current / "Hermes-Helper"
        home = temp / "home"
        home.mkdir()
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "HERMES_HELPER_USER_ROOT": str(home),
            "HERMES_HELPER_SKIP_TK_CHECK": "1",
            "HERMES_HELPER_SKIP_TRAY_CHECK": "1",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
        })
        run(["bash", "install.sh"], cwd=baseline, env=env)
        installed = home / ".local" / "share" / "hermes-helper"
        if version_at(installed) != BASELINE_VERSION:
            raise RuntimeError(f"Baseline installer did not install v{BASELINE_VERSION}")
        run(["bash", "install.sh", "--update"], cwd=current_source, env=env)
        if version_at(installed) != TARGET_VERSION:
            raise RuntimeError(f"Upgrade did not install v{TARGET_VERSION}")
        required = (
            installed / "hermes_control" / "agents.py",
            installed / "hermes_control" / "models.py",
            installed / "hermes_control" / "presets.py",
            installed / "hermes_control" / "tray.py",
            installed / "assets" / "hermes_helper_icon.svg",
            installed / "assets" / "hermes_helper_icon.png",
            installed / "assets" / "hermes_helper_window.png",
            installed / "assets" / "hermes_helper_tray.svg",
            installed / "assets" / "hermes_helper_tray.png",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError(f"Upgrade omitted required v{TARGET_VERSION} files: {missing}")
        backups = sorted((home / ".local" / "state" / "hermes-helper" / "backups").glob("Hermes-Helper-*"))
        if not backups or version_at(backups[-1]) != BASELINE_VERSION:
            raise RuntimeError(f"Upgrade did not preserve a v{BASELINE_VERSION} rollback backup")
        print(f"upgrade_v{BASELINE_VERSION}_to_v{TARGET_VERSION}=verified")


if __name__ == "__main__":
    main()
