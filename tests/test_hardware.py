import tempfile
import unittest
from pathlib import Path

from hermes_control.hardware import category_maxima, primary_cpu_temperature, read_temperatures


class HardwareTests(unittest.TestCase):
    def _sensor(self, root: Path, device: str, name: str, index: int, label: str, millidegrees: int) -> None:
        folder = root / device
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "name").write_text(name)
        (folder / f"temp{index}_label").write_text(label)
        (folder / f"temp{index}_input").write_text(str(millidegrees))

    def test_package_sensor_is_selected_and_sources_remain_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sensor(root, "hwmon0", "coretemp", 1, "Package id 0", 58000)
            self._sensor(root, "hwmon0", "coretemp", 2, "Core 0", 55000)
            self._sensor(root, "hwmon1", "nvme", 1, "Composite", 41000)
            readings = read_temperatures(root)
            primary = primary_cpu_temperature(readings)
            self.assertIsNotNone(primary)
            self.assertEqual(primary.celsius, 58.0)
            self.assertEqual(primary.label, "Package id 0")
            self.assertEqual(category_maxima(readings)["NVMe"].celsius, 41.0)

    def test_implausible_values_are_discarded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sensor(root, "hwmon0", "coretemp", 1, "Package id 0", 999000)
            self.assertEqual(read_temperatures(root), [])


if __name__ == "__main__":
    unittest.main()

