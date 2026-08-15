import json
import tempfile
import unittest
from pathlib import Path

from hermes_control.config import AppConfig, load_config, load_hermes_model_settings, save_config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_safe_and_match_local_stack(self):
        config = AppConfig.defaults().normalized()
        self.assertFalse(config.close_to_tray)
        self.assertTrue(config.temperature_alerts)
        self.assertEqual(config.temperature_warning_c, 85)
        self.assertEqual(config.temperature_critical_c, 95)
        self.assertEqual(config.num_ctx, 16384)
        self.assertEqual(config.model, "qwen3-4b-2507-abliterated-tools:latest")
        self.assertIn("doctorsus31337/Hermes-Helper", config.update_manifest_url)

    def test_round_trip_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "config.json"
            original = AppConfig.defaults()
            original.model = "test-model:latest"
            save_config(original, target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            loaded = load_config(target)
            self.assertEqual(loaded.model, "test-model:latest")

    def test_unknown_fields_do_not_break_future_compatibility(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "config.json"
            target.write_text(json.dumps({"model": "known", "future_field": 42}))
            self.assertEqual(load_config(target).model, "known")

    def test_gateway_command_override_uses_shell_quoting(self):
        config = AppConfig.defaults()
        config.gateway_command = "/opt/hermes/bin/hermes gateway start --profile 'Doctor SUS'"
        self.assertEqual(config.gateway_argv()[-2:], ["--profile", "Doctor SUS"])

    def test_chat_command_explicitly_selects_chat_mode(self):
        config = AppConfig(hermes_executable="/opt/hermes/bin/hermes")
        self.assertEqual(config.chat_argv(), ["/opt/hermes/bin/hermes", "chat"])

    def test_gateway_setup_command_uses_official_interactive_wizard(self):
        config = AppConfig(hermes_executable="/opt/hermes/bin/hermes")
        self.assertEqual(
            config.gateway_setup_argv(),
            ["/opt/hermes/bin/hermes", "gateway", "setup"],
        )

    def test_hermes_cloud_model_block_is_detected_without_reading_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "config.yaml").write_text(
                "model:\n"
                "  default: gpt-5.6-sol\n"
                "  provider: openai-codex\n"
                "  base_url: https://chatgpt.com/backend-api/codex\n"
                "  context_length: 131072\n"
                "  max_tokens: 16384\n"
                "telegram:\n"
                "  token: must-not-be-read\n",
                encoding="utf-8",
            )
            model = load_hermes_model_settings(home)
            self.assertEqual(model.model, "gpt-5.6-sol")
            self.assertEqual(model.provider, "openai-codex")
            self.assertEqual(model.context_length, 131072)
            self.assertEqual(model.max_tokens, 16384)
            self.assertEqual(model.mode, "cloud")

    def test_localhost_custom_provider_is_local(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "config.yaml").write_text(
                "model:\n"
                "  default: qwen3:latest\n"
                "  provider: custom\n"
                "  base_url: http://127.0.0.1:11434/v1\n",
                encoding="utf-8",
            )
            self.assertTrue(load_hermes_model_settings(home).is_local)


if __name__ == "__main__":
    unittest.main()
