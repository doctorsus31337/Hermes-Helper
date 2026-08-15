import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from hermes_control.config import AppConfig
from hermes_control.monitor import Snapshot, find_log, read_log_tail, sanitize_log_text, stack_readiness
from hermes_control.processes import ProcessInfo


class MonitorTests(unittest.TestCase):
    def test_log_tail_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gateway.log"
            path.write_text("old\n" * 500 + "LATEST\n")
            tail = read_log_tail(path, limit_bytes=120)
            self.assertIn("LATEST", tail)
            self.assertLessEqual(len(tail.encode()), 120)

    def test_unreadable_legacy_log_is_skipped_without_aborting_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hermes_home = root / ".hermes"
            readable = hermes_home / "gateway.log"
            unreadable = hermes_home / "logs" / "gateway.log"
            readable.parent.mkdir(parents=True)
            readable.write_text("gateway ready\n")
            unreadable.parent.mkdir()
            unreadable.symlink_to("/root/.hermes/logs/gateway.log")
            issues: list[str] = []

            def candidate_mtime(path: Path) -> float | None:
                if path == unreadable:
                    raise PermissionError(13, "Permission denied", str(path))
                try:
                    return path.stat().st_mtime if path.is_file() else None
                except OSError:
                    return None

            with mock.patch("hermes_control.monitor.log_path", return_value=root / "state" / "gateway.log"), mock.patch(
                "hermes_control.monitor._candidate_mtime", side_effect=candidate_mtime
            ):
                selected = find_log(AppConfig(hermes_home=str(hermes_home)), issues)

            self.assertEqual(selected, readable)
            self.assertEqual(len(issues), 1)
            self.assertIn("Skipped unreadable gateway log", issues[0])
            self.assertIn("Permission denied", issues[0])

    def test_log_tail_reports_an_isolated_read_error(self):
        issues: list[str] = []
        path = mock.Mock()
        path.open.side_effect = PermissionError(13, "Permission denied", "/blocked/gateway.log")
        self.assertEqual(read_log_tail(path, issues=issues), "")
        self.assertEqual(len(issues), 1)
        self.assertIn("Could not read selected gateway log", issues[0])

    def test_terminal_escape_codes_are_removed_from_live_log(self):
        raw = "\x1b[33mWARNING\x1b[0m Telegram connecting\r\n"
        self.assertEqual(sanitize_log_text(raw), "WARNING Telegram connecting\n")

    def test_local_chat_ready_is_separate_from_gateway_ownership(self):
        config = AppConfig(hermes_executable="/opt/hermes", model="model:latest")
        snap = Snapshot(
            timestamp=0,
            ollama_reachable=True,
            running_models=[{"name": "model:latest"}],
            gateway=[ProcessInfo(pid=99, command="hermes gateway", owner_uid=os.getuid() + 1)],
        )
        readiness = stack_readiness(snap, config)
        self.assertTrue(readiness.local_chat_ready)
        self.assertFalse(readiness.full_stack_ready)
        self.assertFalse(readiness.controllable_gateway)

    def test_cloud_readiness_does_not_require_ollama(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "config.yaml").write_text(
                "model:\n  default: gpt-5.6-sol\n  provider: openai-codex\n",
                encoding="utf-8",
            )
            config = AppConfig(hermes_home=str(home), hermes_executable="/opt/hermes")
            snap = Snapshot(
                timestamp=0,
                ollama_reachable=False,
                running_models=[],
                gateway=[ProcessInfo(pid=99, command="hermes gateway", owner_uid=os.getuid())],
            )

            readiness = stack_readiness(snap, config)

            self.assertTrue(readiness.cloud_mode)
            self.assertTrue(readiness.local_chat_ready)
            self.assertTrue(readiness.full_stack_ready)
            self.assertEqual(readiness.active_model, "gpt-5.6-sol")
            self.assertNotIn("Ollama API is offline", readiness.blockers)


if __name__ == "__main__":
    unittest.main()
