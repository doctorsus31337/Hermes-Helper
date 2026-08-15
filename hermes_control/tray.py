"""XFCE-friendly system tray integration for the Tk dashboard."""

from __future__ import annotations

from pathlib import Path
import queue
import threading
from typing import Callable, Protocol


Callback = Callable[[], None]


class TrayBackend(Protocol):
    def start(self, show: Callback, hide: Callback, quit_app: Callback) -> bool: ...
    def stop(self) -> None: ...
    def is_alive(self) -> bool: ...


class AyatanaIndicatorBackend:
    """Native AppIndicator backend; imports GTK lazily so tests stay headless."""

    def __init__(self, icon_path: Path) -> None:
        self.icon_path = icon_path
        self.last_error = ""
        self._loop = None
        self._indicator = None
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._healthy = False
        self._watcher = None

    def start(self, show: Callback, hide: Callback, quit_app: Callback) -> bool:
        def worker() -> None:
            try:
                import gi

                gi.require_version("Gtk", "3.0")
                gi.require_version("AyatanaAppIndicator3", "0.1")
                from gi.repository import AyatanaAppIndicator3 as Indicator, Gio, GLib, Gtk

                watcher = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    "org.kde.StatusNotifierWatcher",
                    "/StatusNotifierWatcher",
                    "org.kde.StatusNotifierWatcher",
                    None,
                )
                host = watcher.get_cached_property("IsStatusNotifierHostRegistered")
                if host is None or not bool(host.unpack()):
                    raise RuntimeError("No active desktop status-notifier host is available.")

                def refresh_watcher_health(proxy) -> None:
                    value = proxy.get_cached_property("IsStatusNotifierHostRegistered")
                    self._healthy = bool(proxy.get_name_owner()) and value is not None and bool(value.unpack())

                def watcher_changed(proxy, _changed, _invalidated) -> None:
                    refresh_watcher_health(proxy)

                def watcher_owner_changed(proxy, _property) -> None:
                    refresh_watcher_health(proxy)

                watcher.connect("g-properties-changed", watcher_changed)
                watcher.connect("notify::g-name-owner", watcher_owner_changed)
                self._watcher = watcher

                indicator = Indicator.Indicator.new(
                    "hermes-helper",
                    str(self.icon_path),
                    Indicator.IndicatorCategory.APPLICATION_STATUS,
                )
                menu = Gtk.Menu()
                for label, callback in (
                    ("Open Hermes-Helper", show),
                    ("Hide Window", hide),
                    ("Exit", quit_app),
                ):
                    item = Gtk.MenuItem(label=label)
                    item.connect("activate", lambda _item, cb=callback: cb())
                    menu.append(item)
                menu.show_all()
                indicator.set_menu(menu)
                indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
                self._indicator = indicator
                self._loop = GLib.MainLoop()
                self._healthy = True
                self._ready.set()
                self._loop.run()
            except Exception as exc:  # platform-dependent optional integration
                self.last_error = str(exc)
                self._healthy = False
                self._ready.set()

        self._thread = threading.Thread(target=worker, name="hermes-helper-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        return self._indicator is not None and self._loop is not None and self._healthy

    def is_alive(self) -> bool:
        return bool(self._healthy and self._thread is not None and self._thread.is_alive())

    def stop(self) -> None:
        self._healthy = False
        if self._loop is None:
            return
        try:
            from gi.repository import AyatanaAppIndicator3 as Indicator, GLib

            def shutdown() -> bool:
                if self._indicator is not None:
                    self._indicator.set_status(Indicator.IndicatorStatus.PASSIVE)
                if self._loop is not None:
                    self._loop.quit()
                return False

            GLib.idle_add(shutdown)
        except Exception:
            pass


class TrayWindowController:
    """Coordinates a tray backend with taskbar-free Tk window behavior."""

    def __init__(
        self,
        root,
        icon_path: Path,
        on_exit: Callback,
        *,
        backend: TrayBackend | None = None,
        can_exit: Callable[[], bool] | None = None,
    ) -> None:
        self.root = root
        self.on_exit = on_exit
        self.can_exit = can_exit or (lambda: True)
        self.backend = backend or AyatanaIndicatorBackend(icon_path)
        self.available = False
        self._commands: queue.SimpleQueue[Callback] = queue.SimpleQueue()

    def _enqueue(self, callback: Callback) -> None:
        """GTK callbacks only enqueue; Tk methods remain on Tk's main thread."""
        self._commands.put(callback)

    def _drain_commands(self) -> None:
        if self.available and not self.backend.is_alive():
            self.available = False
            self.backend.stop()
            try:
                self.root.attributes("-type", "normal")
            except Exception:
                pass
            self.root.deiconify()
            self.root.lift()
            return
        while True:
            try:
                callback = self._commands.get_nowait()
            except queue.Empty:
                break
            callback()
        if self.available:
            self.root.after(75, self._drain_commands)

    def start(self) -> bool:
        self.available = bool(
            self.backend.start(
                lambda: self._enqueue(self.show),
                lambda: self._enqueue(self.hide),
                lambda: self._enqueue(self.exit),
            )
        )
        if not self.available:
            return False
        try:
            self.root.attributes("-type", "utility")
        except Exception:
            self.backend.stop()
            self.available = False
        if self.available:
            self.root.after(75, self._drain_commands)
        return self.available

    def hide(self) -> None:
        if self.available:
            self.root.withdraw()
        else:
            self.root.iconify()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()

    def exit(self) -> None:
        if not self.can_exit():
            return
        self.available = False
        self.backend.stop()
        self.on_exit()
