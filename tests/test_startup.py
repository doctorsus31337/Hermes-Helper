import tempfile
import unittest
from pathlib import Path
from hermes_control import startup


class StartupTests(unittest.TestCase):
    def test_audit_matches_uppercase_legacy_name_and_generic_service_content(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            user_autostart = base / "home" / ".config" / "autostart"
            user_systemd = base / "home" / ".config" / "systemd" / "user"
            system = base / "system"
            xdg = base / "xdg"
            for folder in (user_autostart, user_systemd, system, xdg):
                folder.mkdir(parents=True)
            (user_autostart / "Hermes-Gateway-Monitor.desktop").write_text("[Desktop Entry]\n")
            (user_systemd / "agent.service").write_text("ExecStart=/opt/hermes-agent/cli.py --gateway\n")

            locations = [
                (user_autostart, "User", "Desktop autostart", False),
                (user_systemd, "User", "User service", True),
                (system, "System", "System service", True),
                (xdg, "System", "Desktop autostart", False),
            ]
            items = startup.audit_startup(locations)
            names = {item.path.name for item in items}
            self.assertIn("Hermes-Gateway-Monitor.desktop", names)
            self.assertIn("agent.service", names)
            service = next(item for item in items if item.path.name == "agent.service")
            self.assertTrue(service.pre_login_risk)


if __name__ == "__main__":
    unittest.main()
