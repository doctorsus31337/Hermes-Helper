from __future__ import annotations

import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

from hermes_control.tray import TrayWindowController


class TrayWindowControllerTests(unittest.TestCase):
    def test_successful_tray_start_marks_window_as_utility_and_hides_it(self) -> None:
        root = mock.Mock()
        backend = mock.Mock()
        backend.start.return_value = True
        controller = TrayWindowController(root, Path("icon.png"), mock.Mock(), backend=backend)

        self.assertTrue(controller.start())
        root.attributes.assert_called_once_with("-type", "utility")
        controller.hide()
        root.withdraw.assert_called_once_with()

    def test_show_restores_tray_window_without_forcing_focus(self) -> None:
        root = mock.Mock()
        backend = mock.Mock()
        backend.start.return_value = True
        controller = TrayWindowController(root, Path("icon.png"), mock.Mock(), backend=backend)
        controller.start()

        controller.show()

        root.deiconify.assert_called_once_with()
        root.lift.assert_called_once_with()
        self.assertFalse(root.focus_force.called)

    def test_failed_tray_start_keeps_normal_taskbar_window(self) -> None:
        root = mock.Mock()
        backend = mock.Mock()
        backend.start.return_value = False
        controller = TrayWindowController(root, Path("icon.png"), mock.Mock(), backend=backend)

        self.assertFalse(controller.start())
        root.attributes.assert_not_called()
        controller.hide()
        root.iconify.assert_called_once_with()

    def test_dead_backend_restores_normal_visible_window(self) -> None:
        root = mock.Mock()
        backend = mock.Mock()
        backend.start.return_value = True
        backend.is_alive.return_value = False
        controller = TrayWindowController(root, Path("icon.png"), mock.Mock(), backend=backend)
        controller.start()
        root.reset_mock()

        controller._drain_commands()

        self.assertFalse(controller.available)
        backend.stop.assert_called_once_with()
        root.attributes.assert_called_once_with("-type", "normal")
        root.deiconify.assert_called_once_with()
        root.lift.assert_called_once_with()
        root.after.assert_not_called()

    def test_backend_callback_is_queued_until_tk_thread_drains_it(self) -> None:
        callbacks: dict[str, Callable[[], None]] = {}
        root = mock.Mock()
        backend = mock.Mock()

        def start(show, hide, quit_app):
            callbacks.update(show=show, hide=hide, quit_app=quit_app)
            return True

        backend.start.side_effect = start
        controller = TrayWindowController(root, Path("icon.png"), mock.Mock(), backend=backend)
        controller.start()
        root.reset_mock()

        callbacks["show"]()
        root.deiconify.assert_not_called()

        controller._drain_commands()
        root.deiconify.assert_called_once_with()
        root.lift.assert_called_once_with()
        root.after.assert_called_once_with(75, controller._drain_commands)

    def test_exit_is_blocked_while_configuration_transaction_is_busy(self) -> None:
        root = mock.Mock()
        backend = mock.Mock()
        backend.start.return_value = True
        on_exit = mock.Mock()
        controller = TrayWindowController(root, Path("icon.png"), on_exit, backend=backend, can_exit=lambda: False)
        controller.start()
        backend.reset_mock()

        controller.exit()

        self.assertTrue(controller.available)
        backend.stop.assert_not_called()
        on_exit.assert_not_called()

    def test_exit_stops_tray_before_destroying_window(self) -> None:
        order: list[str] = []
        root = mock.Mock()
        backend = mock.Mock()
        backend.start.return_value = True
        backend.stop.side_effect = lambda: order.append("stop")
        on_exit = mock.Mock(side_effect=lambda: order.append("exit"))
        controller = TrayWindowController(root, Path("icon.png"), on_exit, backend=backend)
        controller.start()

        controller.exit()

        self.assertEqual(order, ["stop", "exit"])


if __name__ == "__main__":
    unittest.main()
