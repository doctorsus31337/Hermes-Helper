from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StartupItem:
    path: Path
    scope: str
    kind: str
    writable: bool
    pre_login_risk: bool


def autostart_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "hermes-helper.desktop"


def set_graphical_login_start(enabled: bool, launcher: Path, icon: Path) -> Path:
    target = autostart_path()
    if not enabled:
        target.unlink(missing_ok=True)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Hermes-Helper\n"
        "Comment=Start the Hermes dashboard after graphical login\n"
        f"Exec={launcher} --startup\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "OnlyShowIn=XFCE;GNOME;KDE;MATE;LXQt;LXDE;\n"
    )
    temp = target.with_suffix(".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(target)
    return target


def graphical_login_start_enabled() -> bool:
    return autostart_path().is_file()


def audit_startup(locations: list[tuple[Path, str, str, bool]] | None = None) -> list[StartupItem]:
    home = Path.home()
    patterns = locations or [
        (home / ".config" / "autostart", "User", "Desktop autostart", False),
        (home / ".config" / "systemd" / "user", "User", "User service", True),
        (Path("/etc/systemd/system"), "System", "System service", True),
        (Path("/usr/lib/systemd/system"), "System", "System service", True),
        (Path("/etc/xdg/autostart"), "System", "Desktop autostart", False),
    ]
    output: list[StartupItem] = []
    for folder, scope, kind, pre_login in patterns:
        try:
            candidates = list(folder.rglob("*"))
        except OSError:
            continue
        for path in candidates:
            if path.name == "hermes-helper.desktop":
                continue
            if not (path.is_file() or path.is_symlink()):
                continue
            name_match = "hermes" in path.name.lower()
            content_match = False
            if path.suffix.lower() in {".desktop", ".service", ".timer", ".sh"}:
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")[:64_000].lower()
                    content_match = any(
                        marker in content
                        for marker in (
                            "hermes-gateway-monitor",
                            "hermes-tray-monitor",
                            "cli.py --gateway",
                            "hermes gateway",
                            "hermes-agent",
                        )
                    )
                except OSError:
                    pass
            if name_match or content_match:
                output.append(StartupItem(path, scope, kind, os.access(path, os.W_OK), pre_login))
    return sorted(output, key=lambda item: (item.scope, str(item.path)))


def backup_user_startup_items(items: list[StartupItem]) -> list[Path]:
    safe = [item for item in items if item.scope == "User" and item.writable]
    if not safe:
        return []
    destination = Path.home() / ".local" / "share" / "hermes-helper" / "startup-backups" / time.strftime("%Y%m%d-%H%M%S")
    destination.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for item in safe:
        target = destination / item.path.name
        counter = 1
        while target.exists():
            target = destination / f"{item.path.stem}-{counter}{item.path.suffix}"
            counter += 1
        shutil.move(str(item.path), str(target))
        moved.append(target)
    return moved
