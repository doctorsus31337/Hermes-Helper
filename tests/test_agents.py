from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from hermes_control.agents import DelegationSettings, HermesDelegationManager, AgentConfigurationError


def done(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, stderr)


def snapshot(values: dict[str, object]) -> list[subprocess.CompletedProcess[str]]:
    names = ("provider", "model", "max_concurrent_children", "max_spawn_depth", "orchestrator_enabled")
    return [done(json.dumps(values[name])) for name in names]


class DelegationManagerTests(unittest.TestCase):
    def test_load_reads_only_non_secret_delegation_fields(self) -> None:
        payload = {
            "provider": "openrouter",
            "model": "nous/hermes",
            "max_concurrent_children": 3,
            "max_spawn_depth": 1,
            "orchestrator_enabled": True,
        }
        runner = mock.Mock(side_effect=snapshot(payload))
        manager = HermesDelegationManager("hermes", runner=runner)

        settings = manager.load()

        self.assertEqual(settings, DelegationSettings("openrouter", "nous/hermes", 3))
        self.assertFalse(hasattr(settings, "api_key"))
        commands = [call.args[0] for call in runner.call_args_list]
        self.assertTrue(all("api_key" not in command for command in commands))
        self.assertTrue(all("shell" not in call.kwargs for call in runner.call_args_list))

    def test_apply_uses_supported_cli_keys_and_preserves_approval_policy(self) -> None:
        old = {"provider": "nous", "model": "old/model", "max_concurrent_children": 2, "max_spawn_depth": 1, "orchestrator_enabled": True}
        runner = mock.Mock(side_effect=[*snapshot(old), *(done() for _ in range(6))])
        manager = HermesDelegationManager("/usr/bin/hermes", runner=runner)

        manager.apply(DelegationSettings("openrouter", "nous/hermes", 3))

        commands = [call.args[0] for call in runner.call_args_list]
        self.assertIn(["/usr/bin/hermes", "config", "set", "delegation.provider", "openrouter"], commands)
        self.assertIn(["/usr/bin/hermes", "config", "set", "delegation.model", "nous/hermes"], commands)
        self.assertIn(["/usr/bin/hermes", "config", "set", "delegation.max_concurrent_children", "3"], commands)
        self.assertIn(["/usr/bin/hermes", "config", "set", "delegation.max_spawn_depth", "1"], commands)
        self.assertIn(["/usr/bin/hermes", "config", "set", "delegation.orchestrator_enabled", "true"], commands)
        self.assertTrue(all("subagent_auto_approve" not in command for command in commands))
        self.assertTrue(all("api_key" not in command for command in commands))

    def test_invalid_worker_settings_fail_before_any_command(self) -> None:
        runner = mock.Mock()
        manager = HermesDelegationManager("hermes", runner=runner)
        with self.assertRaises(AgentConfigurationError):
            manager.apply(DelegationSettings("--provider", "bad\nmodel", 99))
        runner.assert_not_called()

    def test_failed_validation_restores_previous_delegation(self) -> None:
        old = {"provider": "nous", "model": "old/model", "max_concurrent_children": 2, "max_spawn_depth": 1, "orchestrator_enabled": False}
        responses = [*snapshot(old), *(done() for _ in range(4)), done(stderr="invalid", code=1), *(done() for _ in range(6))]
        runner = mock.Mock(side_effect=responses)
        manager = HermesDelegationManager("hermes", runner=runner)

        with self.assertRaisesRegex(AgentConfigurationError, "restored"):
            manager.apply(DelegationSettings("openrouter", "nous/hermes", 3))

        commands = [call.args[0] for call in runner.call_args_list]
        self.assertIn(["hermes", "config", "set", "delegation.provider", "nous"], commands)
        self.assertIn(["hermes", "config", "set", "delegation.orchestrator_enabled", "false"], commands)


if __name__ == "__main__":
    unittest.main()
