from __future__ import annotations

import tomllib
from pathlib import Path

from hermes_control import __version__
from scripts import verify_release_upgrade


ROOT = Path(__file__).resolve().parents[1]


def test_patch_release_metadata_is_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert __version__ == "1.0.6"
    assert project["project"]["version"] == __version__
    assert f"Hermes-Helper-{__version__}.zip" in readme


def test_upgrade_verifier_targets_previous_public_release() -> None:
    assert verify_release_upgrade.BASELINE_VERSION == "1.0.5"
    assert verify_release_upgrade.TARGET_VERSION == __version__
