from __future__ import annotations

import struct
from pathlib import Path

from hermes_control.gui import APP_ICON_FILENAME, TRAY_ICON_FILENAME, WINDOW_ICON_FILENAME


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_icon_family_contains_editable_source_and_runtime_rasters() -> None:
    assert (ASSETS / "hermes_helper_icon.svg").is_file()
    assert png_size(ASSETS / APP_ICON_FILENAME) == (512, 512)
    assert png_size(ASSETS / WINDOW_ICON_FILENAME) == (64, 64)
    assert png_size(ASSETS / TRAY_ICON_FILENAME) == (64, 64)


def test_application_and_tray_use_distinct_purpose_built_icons() -> None:
    assert APP_ICON_FILENAME == "hermes_helper_icon.png"
    assert WINDOW_ICON_FILENAME == "hermes_helper_window.png"
    assert TRAY_ICON_FILENAME == "hermes_helper_tray.png"
    assert (ASSETS / APP_ICON_FILENAME).read_bytes() != (ASSETS / TRAY_ICON_FILENAME).read_bytes()
    assert (ASSETS / WINDOW_ICON_FILENAME).read_bytes() != (ASSETS / TRAY_ICON_FILENAME).read_bytes()


def test_icon_rasters_preserve_transparency() -> None:
    for filename in (APP_ICON_FILENAME, WINDOW_ICON_FILENAME, TRAY_ICON_FILENAME):
        data = (ASSETS / filename).read_bytes()
        color_type = data[25]
        assert color_type in {4, 6}, f"{filename} must include an alpha channel"


def test_every_application_icon_path_uses_the_shared_filename() -> None:
    import inspect

    from hermes_control.gui import HermesHelperApp

    assert "WINDOW_ICON_FILENAME" in inspect.getsource(HermesHelperApp._set_icon)
    assert "APP_ICON_FILENAME" in inspect.getsource(HermesHelperApp.save_settings)


def test_installer_and_release_bundle_include_the_icon_family() -> None:
    from scripts.build_release import source_files

    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "assets/hermes_helper_icon.png" in installer
    packaged = {path.relative_to(ROOT).as_posix() for path in source_files()}
    assert {
        "assets/hermes_helper_icon.svg",
        "assets/hermes_helper_icon.png",
        "assets/hermes_helper_window.png",
        "assets/hermes_helper_tray.svg",
        "assets/hermes_helper_tray.png",
    } <= packaged
