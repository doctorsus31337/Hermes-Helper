import unittest
from unittest import mock

from hermes_control.gui import HermesHelperApp


class GuiControlTests(unittest.TestCase):
    def test_minimize_iconifies_without_closing_or_stopping(self):
        app = object.__new__(HermesHelperApp)
        app.root = mock.Mock()
        app.stop_event = mock.Mock()

        app.minimize_to_taskbar()

        app.root.iconify.assert_called_once_with()
        app.root.destroy.assert_not_called()
        app.stop_event.set.assert_not_called()

    def test_telegram_setup_launches_official_wizard(self):
        app = object.__new__(HermesHelperApp)
        app.config = mock.Mock()
        app.config.gateway_setup_argv.return_value = ["/usr/bin/hermes", "gateway", "setup"]
        app.action_status = mock.Mock()
        app._launch_terminal_command = mock.Mock(return_value=True)

        app.open_telegram_setup()

        app._launch_terminal_command.assert_called_once_with(
            ["/usr/bin/hermes", "gateway", "setup"],
            "Hermes Telegram Setup",
            "Configure Telegram",
        )

    def test_start_server_uses_provider_aware_background_action(self):
        app = object.__new__(HermesHelperApp)
        app.action_running = False
        app.awaiting_stack_ready = False
        app.readiness_announced = True
        app.controller = mock.Mock()
        app.run_action = mock.Mock()

        app.start_server()

        self.assertTrue(app.awaiting_stack_ready)
        self.assertFalse(app.readiness_announced)
        app.run_action.assert_called_once_with("Starting server", app.controller.start_server)


if __name__ == "__main__":
    unittest.main()
