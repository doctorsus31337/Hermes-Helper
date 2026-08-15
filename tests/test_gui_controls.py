import unittest
from unittest import mock

from hermes_control.gui import HermesHelperApp


class GuiControlTests(unittest.TestCase):
    def test_minimize_hides_through_tray_without_closing_or_stopping(self):
        app = object.__new__(HermesHelperApp)
        app.tray_window = mock.Mock()
        app.stop_event = mock.Mock()

        app.minimize_to_taskbar()

        app.tray_window.hide.assert_called_once_with()
        app.stop_event.set.assert_not_called()

    def test_window_close_hides_but_explicit_exit_stops_application(self):
        app = object.__new__(HermesHelperApp)
        app.tray_window = mock.Mock()
        app.root = mock.Mock()
        app.config = mock.Mock(window_geometry="1000x700")
        app.stop_event = mock.Mock()

        app.close()
        app.tray_window.hide.assert_called_once_with()
        app.stop_event.set.assert_not_called()

        app.exit_application()
        app.stop_event.set.assert_called_once_with()
        app.root.destroy.assert_called_once_with()

    def test_exit_is_blocked_during_configuration_transaction(self):
        app = object.__new__(HermesHelperApp)
        app.model_settings_busy = True
        app.agent_apply_busy = False
        app.tray_window = mock.Mock()
        with mock.patch("hermes_control.gui.messagebox.showwarning") as warning:
            self.assertFalse(app.can_exit_application())
        app.tray_window.show.assert_called_once_with()
        warning.assert_called_once()

    def test_model_and_agent_transactions_share_one_busy_gate(self):
        app = object.__new__(HermesHelperApp)
        app.model_settings_busy = False
        app.agent_apply_busy = False
        app.apply_agent_team_button = mock.Mock()
        app.reload_model_settings_button = mock.Mock()
        app.save_model_settings_button = mock.Mock()
        app.model_profile_combo = mock.Mock()
        profile_buttons = [mock.Mock(), mock.Mock()]
        app.__dict__["model_profile_buttons"] = profile_buttons

        app._set_agent_apply_busy(True)

        app.apply_agent_team_button.configure.assert_called_with(state="disabled")
        app.reload_model_settings_button.configure.assert_called_with(state="disabled")
        app.save_model_settings_button.configure.assert_called_with(state="disabled")
        app.model_profile_combo.configure.assert_called_with(state="disabled")
        for button in profile_buttons:
            button.configure.assert_called_with(state="disabled")
        app._model_manager = mock.Mock()
        app.save_model_settings()
        app._model_manager.assert_not_called()

        app.load_selected_model_profile = HermesHelperApp.load_selected_model_profile.__get__(app)
        app._selected_model_profile = mock.Mock()
        app._apply_model_settings = mock.Mock()
        app.load_selected_model_profile()
        app._selected_model_profile.assert_not_called()
        app._apply_model_settings.assert_not_called()

    def test_unreadable_preset_file_is_never_overwritten(self):
        app = object.__new__(HermesHelperApp)
        app.preset_load_error = "unsupported preset format version"
        app.preset_store = mock.Mock()
        with mock.patch("hermes_control.gui.messagebox.showerror") as error:
            self.assertFalse(app._persist_presets())
        app.preset_store.save.assert_not_called()
        error.assert_called_once()

    def test_model_settings_tab_is_built(self):
        import inspect

        build_source = inspect.getsource(HermesHelperApp._build)
        self.assertIn('text="MODEL SETTINGS"', build_source)
        self.assertIn("self._build_model_settings()", build_source)

    def test_agent_studio_tab_is_built_on_hermes_delegation(self):
        import inspect

        build_source = inspect.getsource(HermesHelperApp._build)
        studio_source = inspect.getsource(HermesHelperApp._build_agent_studio)
        apply_source = inspect.getsource(HermesHelperApp.apply_agent_team)
        self.assertIn('text="AGENT STUDIO"', build_source)
        self.assertIn("HEAD ALPHA", studio_source)
        self.assertIn("+  ADD AGENT BRANCH", studio_source)
        self.assertIn("HermesDelegationManager", inspect.getsource(HermesHelperApp._delegation_manager))
        self.assertIn("askyesno", apply_source)
        self.assertNotIn("shell=True", apply_source)

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
