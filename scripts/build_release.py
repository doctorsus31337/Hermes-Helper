#!/usr/bin/env python3
"""Build the versioned Hermes-Helper release ZIP and verified manifest."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_control import __version__  # noqa: E402


DIST = ROOT / "dist"
ARCHIVE_ROOT = "Hermes-Helper"
INCLUDE = (
    "launcher.py",
    "install.sh",
    "uninstall.sh",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "update.example.json",
    "assets",
    "hermes_control",
)


def source_files() -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE:
        item = ROOT / name
        if item.is_file():
            files.append(item)
        elif item.is_dir():
            files.extend(
                path for path in item.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
            )
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def build() -> tuple[Path, Path]:
    DIST.mkdir(parents=True, exist_ok=True)
    archive = DIST / f"Hermes-Helper-{__version__}.zip"
    manifest_path = DIST / "update.json"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for source in source_files():
            relative = source.relative_to(ROOT)
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative.as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
            mode = 0o755 if relative.name in {"install.sh", "uninstall.sh"} else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, source.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {
        "version": __version__,
        "download_url": f"https://github.com/doctorsus31337/Hermes-Helper/releases/download/v{__version__}/{archive.name}",
        "sha256": digest,
        "published_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "notes": "A new purpose-built Gothic blackglass icon family with separate high-detail application and small-panel tray artwork.",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return archive, manifest_path


if __name__ == "__main__":
    archive_path, manifest = build()
    print(archive_path)
    print(manifest)
