from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TemperatureReading:
    celsius: float
    source: str
    label: str
    path: str
    category: str


CPU_DRIVERS = {"coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"}


def _text(path: Path, fallback: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return fallback


def _category(driver: str, label: str) -> str:
    combined = f"{driver} {label}".lower()
    if driver.lower() in CPU_DRIVERS or any(word in combined for word in ("package id", "tctl", "tdie", "cpu")):
        return "CPU"
    if "nvme" in combined:
        return "NVMe"
    if any(word in combined for word in ("gpu", "amdgpu", "nouveau")):
        return "GPU"
    if any(word in combined for word in ("bat", "battery")):
        return "Battery"
    return "System"


def read_temperatures(root: Path = Path("/sys/class/hwmon")) -> list[TemperatureReading]:
    readings: list[TemperatureReading] = []
    try:
        devices = sorted(root.glob("hwmon*"))
    except OSError:
        return []
    for device in devices:
        driver = _text(device / "name", device.name)
        for input_path in sorted(device.glob("temp*_input")):
            stem = input_path.name.removesuffix("_input")
            label = _text(device / f"{stem}_label", stem)
            try:
                celsius = float(_text(input_path)) / 1000.0
            except (TypeError, ValueError):
                continue
            if -20.0 <= celsius <= 130.0:
                readings.append(
                    TemperatureReading(
                        celsius=round(celsius, 1),
                        source=driver,
                        label=label,
                        path=str(input_path),
                        category=_category(driver, label),
                    )
                )
    return readings


def primary_cpu_temperature(readings: list[TemperatureReading]) -> TemperatureReading | None:
    cpu = [item for item in readings if item.category == "CPU"]
    if not cpu:
        return None
    preferred = [
        item for item in cpu
        if any(name in item.label.lower() for name in ("package id", "tctl", "tdie"))
    ]
    return max(preferred or cpu, key=lambda item: item.celsius)


def category_maxima(readings: list[TemperatureReading]) -> dict[str, TemperatureReading]:
    output: dict[str, TemperatureReading] = {}
    for item in readings:
        if item.category not in output or item.celsius > output[item.category].celsius:
            output[item.category] = item
    return output

