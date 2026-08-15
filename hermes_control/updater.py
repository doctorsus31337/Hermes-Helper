from __future__ import annotations

import hashlib
import json
import re
import shutil
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import state_dir


MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    sha256: str
    notes: str = ""
    published_at: str = ""


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})(?:[-+].*)?", value.strip())
    if not match:
        raise ValueError(f"Invalid version: {value}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    left = version_tuple(candidate)
    right = version_tuple(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _https_url(value: str, label: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{label} must be a complete HTTPS URL.")
    return value


def check_for_update(manifest_url: str, current_version: str, timeout: float = 12.0) -> UpdateInfo | None:
    if not manifest_url.strip():
        raise RuntimeError("No update channel is configured yet. Add the release manifest URL in Settings.")
    url = _https_url(manifest_url.strip(), "Update manifest URL")
    request = urllib.request.Request(url, headers={"User-Agent": f"Hermes-Helper/{current_version}"})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        if int(response.headers.get("Content-Length", "0") or 0) > 1_000_000:
            raise RuntimeError("Update manifest is unexpectedly large.")
        data = json.loads(response.read(1_000_001).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Update manifest root must be a JSON object.")
    version = str(data.get("version", "")).strip()
    download_url = _https_url(str(data.get("download_url", "")).strip(), "Update download URL")
    sha256 = str(data.get("sha256", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Update manifest has an invalid SHA-256 digest.")
    info = UpdateInfo(version, download_url, sha256, str(data.get("notes", "")), str(data.get("published_at", "")))
    return info if is_newer(info.version, current_version) else None


def download_update(info: UpdateInfo, progress: Callable[[int, int], None] | None = None) -> Path:
    destination_dir = state_dir() / "updates"
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"Hermes-Helper-{info.version}.zip"
    temp = target.with_suffix(".part")
    request = urllib.request.Request(info.download_url, headers={"User-Agent": "Hermes-Helper-Updater"})
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response, temp.open("wb") as handle:
        total = int(response.headers.get("Content-Length", "0") or 0)
        if total > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("Update package exceeds the 100 MiB safety limit.")
        while True:
            block = response.read(128 * 1024)
            if not block:
                break
            received += len(block)
            if received > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("Update package exceeds the 100 MiB safety limit.")
            digest.update(block)
            handle.write(block)
            if progress:
                progress(received, total)
    actual = digest.hexdigest()
    if actual != info.sha256:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"Update verification failed. Expected {info.sha256[:12]}…, received {actual[:12]}…")
    temp.replace(target)
    return target


def _safe_members(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for item in archive.infolist():
        member = (destination / item.filename).resolve()
        if root != member and root not in member.parents:
            raise RuntimeError(f"Unsafe path in update archive: {item.filename}")
        mode = item.external_attr >> 16
        if mode & 0o170000 == 0o120000:
            raise RuntimeError(f"Symbolic links are not allowed in update archives: {item.filename}")
    archive.extractall(destination)


def stage_update(package: Path) -> Path:
    staging_root = state_dir() / "updates" / "staged"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)
    with zipfile.ZipFile(package) as archive:
        _safe_members(archive, staging_root)
    installers = list(staging_root.rglob("install.sh"))
    valid = [item for item in installers if (item.parent / "launcher.py").is_file() and (item.parent / "hermes_control").is_dir()]
    if len(valid) != 1:
        raise RuntimeError("Verified update does not contain exactly one valid Hermes-Helper project.")
    return valid[0].parent


def launch_installer(staged_project: Path) -> subprocess.Popen:
    installer = staged_project / "install.sh"
    return subprocess.Popen(["bash", str(installer), "--update", "--launch"], cwd=str(staged_project), start_new_session=True)
