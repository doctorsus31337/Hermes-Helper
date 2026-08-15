import os
import subprocess
import tempfile
import unittest
from unittest import mock

from hermes_control.config import AppConfig
from hermes_control.processes import ProcessController, ProcessInfo


class ProcessControllerTests(unittest.TestCase):
    def test_managed_process_is_detached_without_a_terminal(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": temp}):
            controller = ProcessController(AppConfig(hermes_home=temp))
            process = mock.Mock(pid=4321)
            with mock.patch("hermes_control.processes.subprocess.Popen", return_value=process) as popen:
                pid = controller._start(
                    "gateway",
                    ["/opt/hermes", "gateway", "start"],
                    None,
                    controller.runtime / "gateway-output.log",
                )

            self.assertEqual(pid, 4321)
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(kwargs["start_new_session"])
            self.assertTrue(kwargs["close_fds"])

    def test_gateway_owned_by_another_account_blocks_duplicate_launch(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": temp}):
            controller = ProcessController(AppConfig())
            foreign = ProcessInfo(
                pid=2678,
                command="python cli.py --gateway",
                owner_uid=os.getuid() + 1,
            )
            with mock.patch("hermes_control.processes.find_processes", return_value=[foreign]):
                with self.assertRaisesRegex(RuntimeError, "different Linux account"):
                    controller.start_gateway()

    def test_cloud_start_server_skips_ollama_and_starts_gateway(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": temp}):
            home = os.path.join(temp, "hermes")
            os.makedirs(home)
            with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as handle:
                handle.write("model:\n  default: gpt-5.6-sol\n  provider: openai-codex\n")
            controller = ProcessController(AppConfig(hermes_home=home))
            controller.start_gateway = mock.Mock(return_value="gateway started")
            controller.start_ollama = mock.Mock(side_effect=AssertionError("Ollama must not start in cloud mode"))

            result = controller.start_server()

            controller.start_gateway.assert_called_once_with()
            controller.start_ollama.assert_not_called()
            self.assertIn("gpt-5.6-sol", result)
            self.assertIn("Ollama was left unloaded", result)

    def test_cloud_stop_server_leaves_ollama_untouched(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": temp}):
            home = os.path.join(temp, "hermes")
            os.makedirs(home)
            with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as handle:
                handle.write("model:\n  default: gpt-5.6-sol\n  provider: openai-codex\n")
            controller = ProcessController(AppConfig(hermes_home=home))
            controller.stop_gateway = mock.Mock(return_value="gateway stopped")
            controller.stop_managed_ollama = mock.Mock(side_effect=AssertionError("Ollama must be untouched"))

            result = controller.stop_server()

            controller.stop_gateway.assert_called_once_with()
            controller.stop_managed_ollama.assert_not_called()
            self.assertIn("Ollama was left untouched", result)


if __name__ == "__main__":
    unittest.main()
