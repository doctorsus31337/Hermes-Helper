from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from hermes_control.models import HermesModelManager, ModelSettings, ModelSettingsError


class ModelSettingsTests(unittest.TestCase):
    def test_load_uses_hermes_config_json_without_a_shell(self) -> None:
        payload = {
            "provider": "openai-codex",
            "default": "gpt-5.6-sol",
            "context_length": 131072,
            "max_tokens": 16384,
        }
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
        manager = HermesModelManager("/opt/hermes", runner=runner)

        settings = manager.load()

        self.assertEqual(settings, ModelSettings("openai-codex", "gpt-5.6-sol", 131072, 16384))
        argv = runner.call_args.args[0]
        self.assertEqual(argv, ["/opt/hermes", "config", "get", "model", "--json"])
        self.assertNotIn("shell", runner.call_args.kwargs)

    def test_partial_model_mapping_uses_safe_token_defaults(self) -> None:
        payload = {"provider": "openrouter", "default": "nous/hermes"}
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
        settings = HermesModelManager("hermes", runner=runner).load()
        self.assertEqual(settings, ModelSettings("openrouter", "nous/hermes", 65536, 8192))

    def test_apply_validates_then_uses_argument_arrays_and_config_check(self) -> None:
        old = {"provider": "openrouter", "default": "old/model", "context_length": 32768, "max_tokens": 4096}
        runner = mock.Mock(side_effect=[
            subprocess.CompletedProcess([], 0, json.dumps(old), ""),
            *[subprocess.CompletedProcess([], 0, "", "") for _ in range(4)],
            subprocess.CompletedProcess([], 0, "Configuration valid", ""),
        ])
        manager = HermesModelManager("hermes", runner=runner)

        result = manager.apply(ModelSettings("openai-codex", "gpt-5.6-sol", 131072, 16384))

        self.assertIn("gpt-5.6-sol", result)
        calls = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(calls[1:5], [
            ["hermes", "config", "set", "model.provider", "openai-codex"],
            ["hermes", "config", "set", "model.default", "gpt-5.6-sol"],
            ["hermes", "config", "set", "model.context_length", "131072"],
            ["hermes", "config", "set", "model.max_tokens", "16384"],
        ])
        self.assertEqual(calls[5], ["hermes", "config", "check"])
        self.assertTrue(all("shell" not in call.kwargs for call in runner.call_args_list))

    def test_apply_rejects_invalid_values_before_running_hermes(self) -> None:
        runner = mock.Mock()
        manager = HermesModelManager("hermes", runner=runner)
        invalid = ModelSettings("openrouter", "--dangerous", 4096, 8192)

        with self.assertRaises(ModelSettingsError):
            manager.apply(invalid)

        runner.assert_not_called()

    def test_empty_model_block_can_be_initialized(self) -> None:
        responses = [
            subprocess.CompletedProcess([], 0, '""', ""),
            *(subprocess.CompletedProcess([], 0, "", "") for _ in range(5)),
            subprocess.CompletedProcess([], 0, json.dumps({
                "provider": "openrouter",
                "default": "nous/hermes",
                "context_length": 32768,
                "max_tokens": 4096,
            })),
        ]
        runner = mock.Mock(side_effect=responses)
        manager = HermesModelManager("/usr/bin/hermes", runner=runner)
        settings = ModelSettings("openrouter", "nous/hermes", 32768, 4096)

        manager.apply(settings)
        self.assertEqual(manager.load(), settings)

    def test_scalar_model_shorthand_loads_and_rolls_back_exactly(self) -> None:
        scalar = "openrouter/test-model"
        load_runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(scalar), ""))
        loaded = HermesModelManager("hermes", runner=load_runner).load()
        self.assertEqual(loaded, ModelSettings("openrouter", scalar, 65536, 8192))

        responses = [
            subprocess.CompletedProcess([], 0, json.dumps(scalar), ""),
            *[subprocess.CompletedProcess([], 0, "", "") for _ in range(4)],
            subprocess.CompletedProcess([], 1, "", "invalid configuration"),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "Configuration valid", ""),
        ]
        runner = mock.Mock(side_effect=responses)
        manager = HermesModelManager("hermes", runner=runner)
        with self.assertRaisesRegex(ModelSettingsError, "restored"):
            manager.apply(ModelSettings("openai-codex", "gpt-5.6-sol", 131072, 16384))
        commands = [call.args[0] for call in runner.call_args_list]
        self.assertIn(["hermes", "config", "set", "model", scalar], commands)
        self.assertIn(["hermes", "config", "unset", "model"], commands)

    def test_failed_validation_rolls_back_every_setting(self) -> None:
        old = {"provider": "openrouter", "default": "old/model", "context_length": 32768, "max_tokens": 4096}
        responses = [
            subprocess.CompletedProcess([], 0, json.dumps(old), ""),
            *[subprocess.CompletedProcess([], 0, "", "") for _ in range(4)],
            subprocess.CompletedProcess([], 1, "", "invalid configuration"),
            *[subprocess.CompletedProcess([], 0, "", "") for _ in range(4)],
            subprocess.CompletedProcess([], 0, "Configuration valid", ""),
        ]
        runner = mock.Mock(side_effect=responses)
        manager = HermesModelManager("hermes", runner=runner)

        with self.assertRaisesRegex(ModelSettingsError, "restored"):
            manager.apply(ModelSettings("openai-codex", "gpt-5.6-sol", 131072, 16384))

        calls = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(calls[6:10], [
            ["hermes", "config", "set", "model.provider", "openrouter"],
            ["hermes", "config", "set", "model.default", "old/model"],
            ["hermes", "config", "set", "model.context_length", "32768"],
            ["hermes", "config", "set", "model.max_tokens", "4096"],
        ])


if __name__ == "__main__":
    unittest.main()
