from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from . import __version__
from .config import AppConfig, application_root, load_config, save_config
from .monitor import Snapshot, StatusProbe, stack_readiness
from .processes import OllamaClient, ProcessController
from .startup import (
    audit_startup,
    backup_user_startup_items,
    graphical_login_start_enabled,
    set_graphical_login_start,
)
from .telegram import send_message
from .updater import UpdateInfo, check_for_update, download_update, launch_installer, stage_update


COLORS = {
    "bg": "#08090b",
    "panel": "#111318",
    "panel2": "#171a20",
    "border": "#3a3029",
    "text": "#ede6dc",
    "muted": "#a59b90",
    "red": "#c8342d",
    "red_bright": "#f04c3e",
    "gold": "#d2a24c",
    "gold_bright": "#f1c56d",
    "green": "#55b878",
    "orange": "#d98032",
    "critical": "#ff554d",
}


def format_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GiB"
    return f"{value / 1024**2:.0f} MiB"


def process_totals(processes) -> tuple[float, int]:
    return sum(item.cpu_percent for item in processes), sum(item.rss_bytes for item in processes)


class StatusCard(tk.Frame):
    def __init__(self, parent, title: str) -> None:
        super().__init__(parent, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["border"])
        self.columnconfigure(1, weight=1)
        self.dot = tk.Label(self, text="●", font=("DejaVu Sans", 16, "bold"), fg=COLORS["muted"], bg=COLORS["panel"])
        self.dot.grid(row=0, column=0, rowspan=2, padx=(16, 10), pady=14)
        tk.Label(self, text=title.upper(), font=("DejaVu Sans", 9, "bold"), fg=COLORS["muted"], bg=COLORS["panel"]).grid(row=0, column=1, sticky="sw", pady=(12, 0))
        self.value = tk.Label(self, text="CHECKING", font=("DejaVu Sans", 13, "bold"), fg=COLORS["text"], bg=COLORS["panel"])
        self.value.grid(row=1, column=1, sticky="nw", pady=(0, 12))
        self.detail = tk.Label(self, text="", font=("DejaVu Sans Mono", 8), fg=COLORS["muted"], bg=COLORS["panel"])
        self.detail.grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

    def set(self, value: str, detail: str, color: str) -> None:
        self.value.configure(text=value, fg=color)
        self.dot.configure(fg=color)
        self.detail.configure(text=detail)


class HermesHelperApp:
    def __init__(self, startup: bool = False) -> None:
        self.config = load_config()
        self.controller = ProcessController(self.config)
        self.probe = StatusProbe(self.config)
        self.latest: Snapshot | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.action_running = False
        self.last_log = ""
        self.last_temp_alert = 0.0
        self.update_info: UpdateInfo | None = None
        self.awaiting_stack_ready = False
        self.readiness_announced = False

        self.root = tk.Tk()
        self.root.title(f"Hermes-Helper v{__version__}")
        self.root.geometry(self.config.window_geometry)
        self.root.minsize(980, 680)
        self.root.configure(bg=COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._set_icon()
        self._styles()
        self._menu()
        self._build()
        self._center()
        self._start_monitor()
        self.root.after(100, self._drain_events)
        if self.config.check_updates_on_start:
            if self.config.update_manifest_url.strip():
                self.root.after(1800, lambda: self.check_updates(True))
            else:
                self.root.after(300, lambda: self.update_status.configure(text="Update engine is ready; add a release manifest URL in Settings when the GitHub release channel is created."))
        if startup:
            self.root.after(250, self.root.iconify)

    def _set_icon(self) -> None:
        icon = application_root() / "assets" / "hermes_helper_icon.png"
        try:
            self._icon_image = tk.PhotoImage(file=str(icon))
            self.root.iconphoto(True, self._icon_image)
        except tk.TclError:
            self._icon_image = None

    def _styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("Dark.TNotebook.Tab", background=COLORS["panel"], foreground=COLORS["muted"], padding=(18, 9), borderwidth=0, font=("DejaVu Sans", 9, "bold"))
        style.map("Dark.TNotebook.Tab", background=[("selected", COLORS["panel2"])], foreground=[("selected", COLORS["gold_bright"])])
        style.configure("Dark.Vertical.TScrollbar", background=COLORS["panel2"], troughcolor=COLORS["bg"], bordercolor=COLORS["bg"], arrowcolor=COLORS["gold"])
        style.configure("Dark.TCombobox", fieldbackground=COLORS["panel2"], background=COLORS["panel2"], foreground=COLORS["text"])

    def _menu(self) -> None:
        menu = tk.Menu(self.root, bg=COLORS["panel"], fg=COLORS["text"], activebackground=COLORS["red"], activeforeground="white", tearoff=False)
        file_menu = tk.Menu(menu, tearoff=False, bg=COLORS["panel"], fg=COLORS["text"], activebackground=COLORS["red"], activeforeground="white")
        file_menu.add_command(label="Minimize to Taskbar", command=self.minimize_to_taskbar)
        file_menu.add_command(label="Export Diagnostic Report…", command=self.export_report)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="File", menu=file_menu)
        controls = tk.Menu(menu, tearoff=False, bg=COLORS["panel"], fg=COLORS["text"], activebackground=COLORS["red"], activeforeground="white")
        controls.add_command(label="Start Server", command=self.start_server)
        controls.add_command(label="Stop Server", command=lambda: self.run_action("Stopping server", self.controller.stop_server))
        controls.add_command(label="Restart Hermes Gateway", command=self.restart_gateway)
        controls.add_separator()
        controls.add_command(label="Launch Chat Terminal", command=self.open_terminal)
        menu.add_cascade(label="Controls", menu=controls)
        help_menu = tk.Menu(menu, tearoff=False, bg=COLORS["panel"], fg=COLORS["text"], activebackground=COLORS["red"], activeforeground="white")
        help_menu.add_command(label="About Hermes-Helper", command=self.about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menu)

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=COLORS["bg"])
        header.pack(fill=tk.X, padx=24, pady=(18, 8))
        left = tk.Frame(header, bg=COLORS["bg"])
        left.pack(side=tk.LEFT)
        tk.Label(left, text="HERMES", font=("DejaVu Serif", 25, "bold"), fg=COLORS["red_bright"], bg=COLORS["bg"]).pack(side=tk.LEFT)
        tk.Label(left, text="-HELPER", font=("DejaVu Serif", 25, "bold"), fg=COLORS["gold_bright"], bg=COLORS["bg"]).pack(side=tk.LEFT)
        tk.Label(left, text="  AI COMMAND SANCTUM", font=("DejaVu Sans Mono", 9, "bold"), fg=COLORS["muted"], bg=COLORS["bg"]).pack(side=tk.LEFT, pady=(8, 0))
        self._button(header, "—  MINIMIZE", self.minimize_to_taskbar, "#32343a").pack(side=tk.RIGHT, padx=(12, 0), pady=(4, 0))
        self.header_status = tk.Label(header, text="● INITIALIZING", font=("DejaVu Sans Mono", 9, "bold"), fg=COLORS["orange"], bg=COLORS["bg"])
        self.header_status.pack(side=tk.RIGHT, pady=(10, 0))

        tk.Frame(self.root, height=1, bg=COLORS["border"]).pack(fill=tk.X, padx=24)

        self.notebook = ttk.Notebook(self.root, style="Dark.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=18, pady=(8, 16))
        self.overview = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.console_tab = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.telegram_tab = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.updates_tab = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.diagnostics_tab = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.settings_tab = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.notebook.add(self.overview, text="OVERVIEW")
        self.notebook.add(self.console_tab, text="LIVE CONSOLE")
        self.notebook.add(self.telegram_tab, text="TELEGRAM")
        self.notebook.add(self.updates_tab, text="UPDATES")
        self.notebook.add(self.diagnostics_tab, text="DIAGNOSTICS")
        self.notebook.add(self.settings_tab, text="SETTINGS")
        self._build_overview()
        self._build_console()
        self._build_telegram()
        self._build_updates()
        self._build_diagnostics()
        self._build_settings()

    def _build_overview(self) -> None:
        page = self.overview
        cards = tk.Frame(page, bg=COLORS["bg"])
        cards.pack(fill=tk.X, padx=6, pady=(12, 10))
        for index in range(3):
            cards.columnconfigure(index, weight=1, uniform="cards")
        self.ollama_card = StatusCard(cards, "Ollama Engine")
        self.gateway_card = StatusCard(cards, "Hermes Gateway")
        self.telegram_card = StatusCard(cards, "Telegram Link")
        self.ollama_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.gateway_card.grid(row=0, column=1, sticky="nsew", padx=7)
        self.telegram_card.grid(row=0, column=2, sticky="nsew", padx=(7, 0))

        controls = self._panel(page, "POWER DECK")
        controls.pack(fill=tk.X, padx=6, pady=8)
        inner = tk.Frame(controls, bg=COLORS["panel"])
        inner.pack(fill=tk.X, padx=14, pady=(2, 14))
        self.action_buttons: list[tk.Button] = []
        self.action_buttons.append(self._button(inner, "▶  START SERVER", self.start_server, COLORS["red"]))
        self.action_buttons.append(self._button(inner, "■  STOP SERVER", lambda: self.run_action("Stopping server", self.controller.stop_server), "#4c2928"))
        self.action_buttons.append(self._button(inner, "↻  RESTART HERMES", self.restart_gateway, "#4b3822"))
        self.local_load_button = self._button(inner, "◈  LOAD LOCAL MODEL", self.load_model, "#2f4335")
        self.local_unload_button = self._button(inner, "◇  UNLOAD LOCAL MODEL", self.unload_model, "#32343a")
        self.action_buttons.append(self.local_load_button)
        self.action_buttons.append(self.local_unload_button)
        for button in self.action_buttons:
            button.pack(side=tk.LEFT, padx=(0, 8))

        self.launch_chat_button = self._button(inner, "⌘  LAUNCH CHAT TERMINAL", self.open_terminal, "#2f5a3d")
        self.launch_chat_button.configure(state=tk.DISABLED)

        lower = tk.Frame(page, bg=COLORS["bg"])
        lower.pack(fill=tk.BOTH, expand=True, padx=6, pady=8)
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        lower.rowconfigure(0, weight=1)

        resources = self._panel(lower, "LIVE WORKLOAD")
        resources.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.resource_labels: dict[str, tk.Label] = {}
        for index, (key, label) in enumerate((("model", "MODEL"), ("context", "CONTEXT"), ("gateway", "GATEWAY LOAD"), ("ollama", "OLLAMA LOAD"))):
            row = tk.Frame(resources, bg=COLORS["panel"])
            row.pack(fill=tk.X, padx=16, pady=(2, 9))
            tk.Label(row, text=label, width=15, anchor="w", font=("DejaVu Sans Mono", 8, "bold"), fg=COLORS["muted"], bg=COLORS["panel"]).pack(side=tk.LEFT)
            value = tk.Label(row, text="—", anchor="w", font=("DejaVu Sans Mono", 9), fg=COLORS["text"], bg=COLORS["panel"])
            value.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.resource_labels[key] = value

        thermals = self._panel(lower, "THERMAL WATCH")
        thermals.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.temperature_main = tk.Label(thermals, text="— °C", font=("DejaVu Sans Mono", 28, "bold"), fg=COLORS["green"], bg=COLORS["panel"])
        self.temperature_main.pack(anchor="w", padx=16, pady=(4, 0))
        self.temperature_source = tk.Label(thermals, text="Reading hardware sensors…", justify=tk.LEFT, anchor="w", wraplength=360, font=("DejaVu Sans Mono", 8), fg=COLORS["muted"], bg=COLORS["panel"])
        self.temperature_source.pack(fill=tk.X, padx=16, pady=(0, 8))
        self.temperature_others = tk.Label(thermals, text="", justify=tk.LEFT, anchor="nw", font=("DejaVu Sans Mono", 8), fg=COLORS["text"], bg=COLORS["panel"])
        self.temperature_others.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        self.action_status = tk.Label(page, text="Ready. Start Server follows Hermes's active provider and runs the gateway without a terminal window.", anchor="w", font=("DejaVu Sans Mono", 8), fg=COLORS["muted"], bg=COLORS["bg"])
        self.action_status.pack(fill=tk.X, padx=8, pady=(2, 0))

    def _build_console(self) -> None:
        tools = tk.Frame(self.console_tab, bg=COLORS["bg"])
        tools.pack(fill=tk.X, padx=8, pady=(12, 6))
        self.console_path = tk.Label(tools, text="Waiting for a readable gateway log…", font=("DejaVu Sans Mono", 8), fg=COLORS["muted"], bg=COLORS["bg"])
        self.console_path.pack(side=tk.LEFT)
        self.follow_var = tk.BooleanVar(value=True)
        tk.Checkbutton(tools, text="Follow output", variable=self.follow_var, selectcolor=COLORS["panel2"], activebackground=COLORS["bg"], activeforeground=COLORS["gold_bright"], bg=COLORS["bg"], fg=COLORS["text"]).pack(side=tk.RIGHT)
        self.console = tk.Text(self.console_tab, bg="#070809", fg="#d8d2ca", insertbackground=COLORS["gold"], selectbackground="#5b302c", font=("DejaVu Sans Mono", 9), relief=tk.FLAT, wrap=tk.NONE, padx=12, pady=12)
        scroll_y = ttk.Scrollbar(self.console_tab, command=self.console.yview, style="Dark.Vertical.TScrollbar")
        self.console.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 8))
        self.console.pack(fill=tk.BOTH, expand=True, padx=(8, 0), pady=(0, 8))
        self.console.tag_configure("error", foreground="#ff6a61")
        self.console.tag_configure("warning", foreground="#e7ae56")
        self.console.tag_configure("telegram", foreground="#72c990")

    def _build_telegram(self) -> None:
        top = self._panel(self.telegram_tab, "TELEGRAM CONTROL LINK")
        top.pack(fill=tk.X, padx=8, pady=(12, 8))
        self.tg_status = tk.Label(top, text="Checking bot configuration…", font=("DejaVu Sans", 13, "bold"), fg=COLORS["orange"], bg=COLORS["panel"])
        self.tg_status.pack(anchor="w", padx=16, pady=(4, 2))
        self.tg_detail = tk.Label(top, text="", font=("DejaVu Sans Mono", 8), fg=COLORS["muted"], bg=COLORS["panel"])
        self.tg_detail.pack(anchor="w", padx=16, pady=(0, 8))
        telegram_actions = tk.Frame(top, bg=COLORS["panel"])
        telegram_actions.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._button(telegram_actions, "⚙  CONFIGURE TELEGRAM", self.open_telegram_setup, COLORS["red"]).pack(side=tk.LEFT)
        self._button(telegram_actions, "↻  REFRESH STATUS", self.force_refresh, "#3b3025").pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(
            telegram_actions,
            text="Runs Hermes's official local setup wizard. Your bot token is never shown in Hermes-Helper.",
            font=("DejaVu Sans Mono", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side=tk.LEFT, padx=(14, 0))

        compose = self._panel(self.telegram_tab, "SEND A VERIFIED TEST MESSAGE")
        compose.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        form = tk.Frame(compose, bg=COLORS["panel"])
        form.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 14))
        tk.Label(form, text="KNOWN CHAT", font=("DejaVu Sans Mono", 8, "bold"), fg=COLORS["muted"], bg=COLORS["panel"]).grid(row=0, column=0, sticky="w", pady=5)
        self.chat_var = tk.StringVar()
        self.chat_combo = ttk.Combobox(form, textvariable=self.chat_var, style="Dark.TCombobox", width=28)
        self.chat_combo.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=5)
        tk.Label(form, text="MESSAGE", font=("DejaVu Sans Mono", 8, "bold"), fg=COLORS["muted"], bg=COLORS["panel"]).grid(row=1, column=0, sticky="nw", pady=5)
        self.message_text = tk.Text(form, height=7, bg=COLORS["panel2"], fg=COLORS["text"], insertbackground=COLORS["gold"], relief=tk.FLAT, font=("DejaVu Sans Mono", 9), wrap=tk.WORD, padx=8, pady=8)
        self.message_text.insert("1.0", "Hermes-Helper check: your Hermes gateway is online.")
        self.message_text.grid(row=1, column=1, sticky="nsew", padx=(12, 0), pady=5)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(1, weight=1)
        self._button(form, "SEND TEST MESSAGE", self.send_test, COLORS["red"]).grid(row=2, column=1, sticky="e", pady=(10, 0))
        tk.Label(form, text="Hermes-Helper never displays or writes your bot token. It only reads it when contacting Telegram.", font=("DejaVu Sans Mono", 8), fg=COLORS["muted"], bg=COLORS["panel"]).grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _build_diagnostics(self) -> None:
        toolbar = tk.Frame(self.diagnostics_tab, bg=COLORS["bg"])
        toolbar.pack(fill=tk.X, padx=8, pady=(12, 6))
        self._button(toolbar, "REFRESH", self.force_refresh, "#3b3025").pack(side=tk.LEFT)
        self._button(toolbar, "EXPORT REPORT", self.export_report, "#3b3025").pack(side=tk.LEFT, padx=8)
        self._button(toolbar, "BACK UP LEGACY USER STARTUP", self.repair_user_startup, "#4c2928").pack(side=tk.RIGHT)
        panel = self._panel(self.diagnostics_tab, "TRUTHFUL SYSTEM CHECKS")
        panel.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.diagnostic_text = tk.Text(panel, bg=COLORS["panel"], fg=COLORS["text"], font=("DejaVu Sans Mono", 9), relief=tk.FLAT, padx=14, pady=10, wrap=tk.WORD, state=tk.DISABLED)
        self.diagnostic_text.pack(fill=tk.BOTH, expand=True)
        self.diagnostic_text.tag_configure("PASS", foreground=COLORS["green"])
        self.diagnostic_text.tag_configure("WARN", foreground=COLORS["gold_bright"])
        self.diagnostic_text.tag_configure("FAIL", foreground=COLORS["critical"])
        self.diagnostic_text.tag_configure("heading", foreground=COLORS["gold_bright"], font=("DejaVu Sans Mono", 10, "bold"))

    def _build_updates(self) -> None:
        panel = self._panel(self.updates_tab, "VERIFIED UPDATE CHANNEL")
        panel.pack(fill=tk.BOTH, expand=True, padx=8, pady=(12, 8))
        content = tk.Frame(panel, bg=COLORS["panel"])
        content.pack(fill=tk.BOTH, expand=True, padx=22, pady=(12, 20))
        self.update_title = tk.Label(content, text=f"Hermes-Helper v{__version__}", font=("DejaVu Serif", 19, "bold"), fg=COLORS["gold_bright"], bg=COLORS["panel"])
        self.update_title.pack(anchor="w")
        self.update_status = tk.Label(content, text="No update check has run yet.", justify=tk.LEFT, wraplength=850, font=("DejaVu Sans Mono", 9), fg=COLORS["muted"], bg=COLORS["panel"])
        self.update_status.pack(anchor="w", fill=tk.X, pady=(8, 16))
        self.update_progress = ttk.Progressbar(content, mode="determinate", maximum=100)
        self.update_progress.pack(fill=tk.X, pady=(0, 14))
        buttons = tk.Frame(content, bg=COLORS["panel"])
        buttons.pack(fill=tk.X)
        self.check_update_button = self._button(buttons, "CHECK FOR UPDATES", lambda: self.check_updates(False), "#3b3025")
        self.check_update_button.pack(side=tk.LEFT)
        self.install_update_button = self._button(buttons, "DOWNLOAD & INSTALL VERIFIED UPDATE", self.install_update, COLORS["red"])
        self.install_update_button.pack(side=tk.LEFT, padx=10)
        self.install_update_button.configure(state=tk.DISABLED)
        tk.Label(
            content,
            text="SECURITY MODEL\n• HTTPS manifests only\n• SHA-256 must match before extraction\n• Archives are path-checked and may not contain symlinks\n• The existing installation is backed up before replacement\n• Updates are never silently installed",
            justify=tk.LEFT,
            font=("DejaVu Sans Mono", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(anchor="w", pady=(28, 0))

    def _build_settings(self) -> None:
        outer = self._panel(self.settings_tab, "RUNTIME CONFIGURATION")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=(12, 8))
        form = tk.Frame(outer, bg=COLORS["panel"])
        form.pack(fill=tk.BOTH, expand=True, padx=18, pady=(4, 16))
        self.setting_vars: dict[str, tk.Variable] = {
            "hermes_home": tk.StringVar(value=self.config.hermes_home),
            "hermes_executable": tk.StringVar(value=self.config.hermes_executable),
            "gateway_command": tk.StringVar(value=self.config.gateway_command),
            "ollama_executable": tk.StringVar(value=self.config.ollama_executable),
            "ollama_url": tk.StringVar(value=self.config.ollama_url),
            "model": tk.StringVar(value=self.config.model),
            "num_ctx": tk.IntVar(value=self.config.num_ctx),
            "auto_load_model": tk.BooleanVar(value=self.config.auto_load_model),
            "auto_start_gateway": tk.BooleanVar(value=self.config.auto_start_gateway),
            "keep_model_loaded": tk.BooleanVar(value=self.config.keep_model_loaded),
            "temperature_alerts": tk.BooleanVar(value=self.config.temperature_alerts),
            "temperature_warning_c": tk.IntVar(value=self.config.temperature_warning_c),
            "temperature_critical_c": tk.IntVar(value=self.config.temperature_critical_c),
            "check_updates_on_start": tk.BooleanVar(value=self.config.check_updates_on_start),
            "update_manifest_url": tk.StringVar(value=self.config.update_manifest_url),
        }
        entries = (
            ("Hermes home", "hermes_home"),
            ("Hermes executable", "hermes_executable"),
            ("Gateway command override", "gateway_command"),
            ("Ollama executable", "ollama_executable"),
            ("Ollama API URL", "ollama_url"),
            ("Local fallback model", "model"),
            ("Local runtime context (num_ctx)", "num_ctx"),
            ("Warning temperature °C", "temperature_warning_c"),
            ("Critical temperature °C", "temperature_critical_c"),
            ("Update manifest URL", "update_manifest_url"),
        )
        for row, (label, key) in enumerate(entries):
            tk.Label(form, text=label.upper(), font=("DejaVu Sans Mono", 8, "bold"), fg=COLORS["muted"], bg=COLORS["panel"]).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
            entry = tk.Entry(form, textvariable=self.setting_vars[key], bg=COLORS["panel2"], fg=COLORS["text"], insertbackground=COLORS["gold"], relief=tk.FLAT, font=("DejaVu Sans Mono", 9))
            entry.grid(row=row, column=1, sticky="ew", ipady=5, pady=5)
        form.columnconfigure(1, weight=1)

        checks = tk.Frame(form, bg=COLORS["panel"])
        checks.grid(row=len(entries), column=0, columnspan=2, sticky="ew", pady=(12, 6))
        for text, key in (
            ("Load local model when Hermes is using a local provider", "auto_load_model"),
            ("Start Hermes Gateway when Start Server is clicked", "auto_start_gateway"),
            ("Keep local model loaded until explicitly stopped", "keep_model_loaded"),
            ("Show temperature warning popups", "temperature_alerts"),
            ("Check for updates when Hermes-Helper opens", "check_updates_on_start"),
        ):
            tk.Checkbutton(checks, text=text, variable=self.setting_vars[key], bg=COLORS["panel"], fg=COLORS["text"], selectcolor=COLORS["panel2"], activebackground=COLORS["panel"], activeforeground=COLORS["gold_bright"], font=("DejaVu Sans", 9)).pack(anchor="w", pady=2)

        startup_row = tk.Frame(form, bg=COLORS["panel2"], highlightthickness=1, highlightbackground=COLORS["border"])
        startup_row.grid(row=len(entries) + 1, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        self.login_start_var = tk.BooleanVar(value=graphical_login_start_enabled())
        tk.Checkbutton(startup_row, text="Open Hermes-Helper after I log into the graphical desktop", variable=self.login_start_var, bg=COLORS["panel2"], fg=COLORS["gold_bright"], selectcolor=COLORS["panel"], activebackground=COLORS["panel2"], activeforeground=COLORS["gold_bright"], font=("DejaVu Sans", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(startup_row, text="Default: OFF. This uses desktop autostart, never a pre-login system service, and does not automatically start the LLM.", bg=COLORS["panel2"], fg=COLORS["muted"], font=("DejaVu Sans Mono", 8)).pack(anchor="w", padx=16, pady=(0, 10))
        self._button(form, "SAVE & APPLY SETTINGS", self.save_settings, COLORS["red"]).grid(row=len(entries) + 2, column=1, sticky="e", pady=(12, 0))

    def _panel(self, parent, title: str) -> tk.LabelFrame:
        return tk.LabelFrame(parent, text=f"  {title}  ", bg=COLORS["panel"], fg=COLORS["gold"], bd=1, relief=tk.FLAT, highlightthickness=1, highlightbackground=COLORS["border"], font=("DejaVu Sans Mono", 9, "bold"))

    def _button(self, parent, text: str, command: Callable[[], None], bg: str) -> tk.Button:
        button = tk.Button(parent, text=text, command=command, bg=bg, fg="white", activebackground=COLORS["red_bright"], activeforeground="white", relief=tk.FLAT, cursor="hand2", font=("DejaVu Sans", 8, "bold"), padx=13, pady=8)
        return button

    def _center(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _start_monitor(self) -> None:
        def worker() -> None:
            while not self.stop_event.is_set():
                try:
                    self.events.put(("snapshot", self.probe.snapshot()))
                except Exception as exc:
                    self.events.put(("error", f"Status probe failed: {exc}"))
                self.stop_event.wait(self.config.poll_seconds)
        threading.Thread(target=worker, name="hermes-helper-monitor", daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "snapshot":
                    self._apply_snapshot(payload)  # type: ignore[arg-type]
                elif kind == "action_done":
                    self._action_done(str(payload), False)
                elif kind == "action_error":
                    self._action_done(str(payload), True)
                elif kind == "error":
                    self.action_status.configure(text=str(payload), fg=COLORS["critical"])
                elif kind == "update_available":
                    self._show_update_available(payload)  # type: ignore[arg-type]
                elif kind == "update_current":
                    self.check_update_button.configure(state=tk.NORMAL)
                    self.update_status.configure(text=f"You are current. Hermes-Helper v{__version__} is the newest verified release.", fg=COLORS["green"])
                elif kind == "update_error":
                    self.check_update_button.configure(state=tk.NORMAL)
                    self.install_update_button.configure(state=tk.DISABLED)
                    self.update_status.configure(text=str(payload), fg=COLORS["orange"])
                elif kind == "update_progress":
                    received, total = payload  # type: ignore[misc]
                    percent = (received / total * 100.0) if total else 0.0
                    self.update_progress.configure(value=percent)
                    self.update_status.configure(text=f"Downloading verified package… {received / 1024**2:.1f} MiB" + (f" / {total / 1024**2:.1f} MiB" if total else ""), fg=COLORS["gold_bright"])
                elif kind == "update_staged":
                    self._finish_update_install(payload)  # type: ignore[arg-type]
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(120, self._drain_events)

    def _apply_snapshot(self, snap: Snapshot) -> None:
        self.latest = snap
        if not self.action_running and self.action_status.cget("text").startswith("Status probe failed:"):
            self.action_status.configure(
                text="Status monitoring recovered. See Diagnostics for any isolated warnings.",
                fg=COLORS["green"],
            )
        readiness = stack_readiness(snap, self.config)
        model_loaded = readiness.model_loaded
        if readiness.cloud_mode:
            if snap.running_models:
                self.ollama_card.set("UNUSED MODEL LOADED", f"{len(snap.running_models)} local model(s) in memory", COLORS["orange"])
            else:
                self.ollama_card.set("NOT REQUIRED", "Cloud inference is active • 0 models in memory", COLORS["green"])
        elif snap.ollama_reachable:
            self.ollama_card.set("MODEL LOADED" if model_loaded else "SERVER READY", f"{len(snap.running_models)} model(s) in memory", COLORS["green"] if model_loaded else COLORS["gold_bright"])
        else:
            self.ollama_card.set("OFFLINE", "API is not reachable", COLORS["critical"])
        if snap.gateway:
            owned = all(item.controllable for item in snap.gateway)
            foreign = [item for item in snap.gateway if not item.controllable]
            root_owned = foreign and all(item.owner_uid == 0 for item in foreign)
            detail = f"PID {snap.gateway[0].pid} • {len(snap.gateway)} process(es)" + (f" • owner UID {foreign[0].owner_uid}" if foreign else " • current user")
            gateway_value = "RUNNING" if owned else "ROOT-OWNED PROCESS" if root_owned else "OTHER USER PROCESS"
            self.gateway_card.set(gateway_value, detail, COLORS["green"] if owned else COLORS["orange"])
        else:
            self.gateway_card.set("STOPPED", "No gateway process found", COLORS["critical"])
        telegram_verified = bool(snap.gateway) and snap.telegram.reachable and snap.telegram_log_connected
        if snap.telegram.configured and snap.telegram.reachable:
            value = "CONNECTED" if telegram_verified else "BOT API READY"
            detail = f"@{snap.telegram.bot_name}" if snap.telegram.bot_name else snap.telegram.detail
            self.telegram_card.set(value, detail, COLORS["green"] if telegram_verified else COLORS["gold_bright"])
        elif snap.telegram.configured:
            self.telegram_card.set("UNREACHABLE", snap.telegram.detail, COLORS["orange"])
        elif snap.gateway and any(not item.controllable for item in snap.gateway):
            self.telegram_card.set("EXTERNAL / UNKNOWN", "Gateway configuration belongs to another Linux account", COLORS["orange"])
        else:
            self.telegram_card.set("NOT CONFIGURED", "Token not found", COLORS["critical"])
        if readiness.full_stack_ready:
            header = "● CLOUD SERVER READY" if readiness.cloud_mode else "● LOCAL STACK READY"
            self.header_status.configure(text=header, fg=COLORS["green"])
        elif readiness.local_chat_ready:
            self.header_status.configure(text="● LOCAL CHAT READY", fg=COLORS["gold_bright"])
        else:
            self.header_status.configure(text="● ATTENTION NEEDED", fg=COLORS["orange"])

        if readiness.local_chat_ready:
            if not self.launch_chat_button.winfo_manager():
                self.launch_chat_button.pack(side=tk.RIGHT)
            self.launch_chat_button.configure(state=tk.NORMAL)
            ready_to_announce = readiness.full_stack_ready if readiness.cloud_mode else readiness.local_chat_ready
            if ready_to_announce and self.awaiting_stack_ready and not self.readiness_announced:
                self.readiness_announced = True
                self.awaiting_stack_ready = False
                note = "Hermes cloud server is running in the background." if readiness.cloud_mode else "Hermes local chat is ready."
                if not readiness.full_stack_ready:
                    note += " The Telegram gateway still needs attention, but it does not block local chat."
                self.action_status.configure(text=note, fg=COLORS["green"])
                messagebox.showinfo("Hermes Is Ready", f"{note}\n\nYou may minimize or close any old gateway terminal. Telegram will remain online.")
        else:
            self.launch_chat_button.configure(state=tk.DISABLED)
            if self.launch_chat_button.winfo_manager():
                self.launch_chat_button.pack_forget()

        gateway_cpu, gateway_ram = process_totals(snap.gateway)
        ollama_cpu, ollama_ram = process_totals(snap.ollama_server + snap.ollama_runner)
        hermes_model = self.config.hermes_model_settings()
        self.resource_labels["model"].configure(
            text=f"{readiness.active_model}  •  {readiness.provider}  •  {readiness.cloud_mode and 'CLOUD' or 'LOCAL'}"
        )
        if hermes_model.context_length:
            context = f"{hermes_model.context_length:,} tokens"
            if hermes_model.max_tokens:
                context += f" • {hermes_model.max_tokens:,} max output"
        else:
            context = f"{self.config.num_ctx:,} local runtime tokens"
        self.resource_labels["context"].configure(text=context)
        self.resource_labels["gateway"].configure(text=f"{gateway_cpu:5.1f}% CPU  •  {format_bytes(gateway_ram)} RAM")
        self.resource_labels["ollama"].configure(text=f"{ollama_cpu:5.1f}% CPU  •  {format_bytes(ollama_ram)} RAM")
        if not self.action_running:
            self.local_load_button.configure(state=tk.DISABLED if readiness.cloud_mode else tk.NORMAL)
            self.local_unload_button.configure(state=tk.NORMAL if snap.running_models else tk.DISABLED)

        cpu = snap.cpu_temp
        if cpu:
            color = COLORS["critical"] if cpu.celsius >= self.config.temperature_critical_c else COLORS["orange"] if cpu.celsius >= self.config.temperature_warning_c else COLORS["green"]
            self.temperature_main.configure(text=f"{cpu.celsius:.1f} °C", fg=color)
            self.temperature_source.configure(text=f"CPU source: {cpu.source} / {cpu.label}\n{cpu.path}")
            if self.config.temperature_alerts and cpu.celsius >= self.config.temperature_warning_c and time.time() - self.last_temp_alert > 600:
                self.last_temp_alert = time.time()
                level = "CRITICAL" if cpu.celsius >= self.config.temperature_critical_c else "HIGH"
                messagebox.showwarning(f"{level} CPU temperature", f"{cpu.celsius:.1f} °C from {cpu.source} / {cpu.label}\n\nConsider unloading the model or stopping the stack. The exact sensor source is shown so you can verify the reading.")
        else:
            self.temperature_main.configure(text="N/A", fg=COLORS["muted"])
            self.temperature_source.configure(text="No readable CPU hwmon sensor was found.")
        lines = [f"{name}: {item.celsius:.1f} °C  ({item.source} / {item.label})" for name, item in sorted(snap.temp_maxima.items()) if name != "CPU"]
        self.temperature_others.configure(text="\n".join(lines) or "No additional sensor categories found.")

        telegram_status = (
            f"@{snap.telegram.bot_name} is reachable"
            if snap.telegram.reachable
            else "Telegram API is unreachable"
            if snap.telegram.configured
            else "Telegram is not configured"
        )
        self.tg_status.configure(text=telegram_status, fg=COLORS["green"] if snap.telegram.reachable else COLORS["orange"])
        self.tg_detail.configure(text=f"Configured: {'yes' if snap.telegram.configured else 'no'}  •  Gateway evidence: {'active' if snap.telegram_log_connected else 'not yet observed'}  •  Known chats: {len(snap.chat_ids)}")
        self.chat_combo.configure(values=snap.chat_ids)
        if not self.chat_var.get() and snap.chat_ids:
            self.chat_var.set(snap.chat_ids[-1])
        self._update_console(snap)
        self._update_diagnostics(snap)

    def _update_console(self, snap: Snapshot) -> None:
        if snap.log_file:
            console_source = f"Source: {snap.log_file}"
        elif snap.log_issues:
            console_source = "No readable gateway log; see Diagnostics for the skipped path"
        else:
            console_source = "No readable gateway log yet"
        self.console_path.configure(text=console_source)
        if snap.log_tail == self.last_log:
            return
        self.last_log = snap.log_tail
        lines = snap.log_tail.splitlines()[-700:]
        self.console.configure(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        for line in lines:
            lower = line.lower()
            tag = "error" if any(word in lower for word in ("error", "traceback", "failed")) else "warning" if any(word in lower for word in ("warn", "retry")) else "telegram" if "telegram" in lower else None
            self.console.insert(tk.END, line + "\n", tag)
        self.console.configure(state=tk.DISABLED)
        if self.follow_var.get():
            self.console.see(tk.END)

    def _update_diagnostics(self, snap: Snapshot) -> None:
        widget = self.diagnostic_text
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, "SYSTEM CHECKS\n", "heading")
        for level, detail in snap.diagnostics:
            widget.insert(tk.END, f"[{level:<4}] ", level)
            widget.insert(tk.END, detail + "\n")
        widget.insert(tk.END, "\nSTARTUP AUDIT\n", "heading")
        widget.insert(tk.END, f"Hermes-Helper graphical-login startup: {'ENABLED' if snap.login_start_enabled else 'disabled'}\n")
        if snap.startup_items:
            for item in snap.startup_items:
                risk = "PRE-LOGIN RISK" if item.pre_login_risk else "after login"
                widget.insert(tk.END, f"[{item.scope}] {item.kind} • {risk}\n  {item.path}\n")
        else:
            widget.insert(tk.END, "No legacy Hermes startup entries were found in the standard locations.\n")
        widget.insert(tk.END, "\nSENSOR SOURCES\n", "heading")
        for item in sorted(snap.temperatures, key=lambda value: (value.category, -value.celsius)):
            widget.insert(tk.END, f"{item.celsius:5.1f} °C  {item.category:<8} {item.source} / {item.label}\n")
        widget.configure(state=tk.DISABLED)

    def run_action(self, label: str, action: Callable[[], str]) -> None:
        if self.action_running:
            messagebox.showinfo("Hermes-Helper", "Another control action is still running.")
            return
        self.action_running = True
        self.action_status.configure(text=f"{label}…", fg=COLORS["gold_bright"])
        for button in self.action_buttons:
            button.configure(state=tk.DISABLED)
        def worker() -> None:
            try:
                self.events.put(("action_done", action()))
            except Exception as exc:
                self.events.put(("action_error", f"{label} failed: {exc}"))
        threading.Thread(target=worker, name="hermes-helper-action", daemon=True).start()

    def _action_done(self, message: str, failed: bool) -> None:
        self.action_running = False
        for button in self.action_buttons:
            button.configure(state=tk.NORMAL)
        if self.latest is not None:
            readiness = stack_readiness(self.latest, self.config)
            self.local_load_button.configure(state=tk.DISABLED if readiness.cloud_mode else tk.NORMAL)
            self.local_unload_button.configure(state=tk.NORMAL if self.latest.running_models else tk.DISABLED)
        self.action_status.configure(text=message.replace("\n", "  •  "), fg=COLORS["critical"] if failed else COLORS["green"])
        if failed:
            self.awaiting_stack_ready = False
            messagebox.showerror("Hermes-Helper", message)
        self.force_refresh()

    def start_server(self) -> None:
        if self.action_running:
            messagebox.showinfo("Hermes-Helper", "Another control action is still running.")
            return
        self.awaiting_stack_ready = True
        self.readiness_announced = False
        self.run_action("Starting server", self.controller.start_server)

    def start_full_stack(self) -> None:
        """Compatibility alias for older bindings and update-state callbacks."""
        self.start_server()

    def restart_gateway(self) -> None:
        def action() -> str:
            stopped = self.controller.stop_gateway()
            time.sleep(1)
            return f"{stopped}\n{self.controller.start_gateway()}"
        self.run_action("Restarting Hermes Gateway", action)

    def load_model(self) -> None:
        self.run_action("Loading model", lambda: self._load_model_action())

    def _load_model_action(self) -> str:
        client = OllamaClient(self.config.ollama_url)
        if not client.is_reachable():
            self.controller.start_ollama()
        client.load_model(self.config.model, self.config.num_ctx, self.config.keep_model_loaded)
        return f"Model loaded: {self.config.model} with num_ctx={self.config.num_ctx:,}."

    def unload_model(self) -> None:
        def action() -> str:
            OllamaClient(self.config.ollama_url).unload_model(self.config.model)
            return f"Model unloaded: {self.config.model}."
        self.run_action("Unloading model", action)

    def send_test(self) -> None:
        chat = self.chat_var.get().strip()
        message = self.message_text.get("1.0", tk.END).strip()
        if not chat or not message:
            messagebox.showwarning("Telegram", "Choose a chat ID and enter a message first.")
            return
        self.run_action("Sending Telegram message", lambda: send_message(Path(self.config.hermes_home), chat, message))

    def _launch_terminal_command(self, command: list[str], title: str, dialog_title: str) -> bool:
        terminals = (
            ["xfce4-terminal", f"--title={title}", "--execute", *command],
            ["x-terminal-emulator", "-T", title, "-e", *command],
            ["gnome-terminal", f"--title={title}", "--", *command],
            ["konsole", "-p", f"tabtitle={title}", "-e", *command],
        )
        for argv in terminals:
            if shutil.which(argv[0]):
                subprocess.Popen(argv, start_new_session=True)
                return True
        messagebox.showerror(dialog_title, "No supported terminal emulator was found.")
        return False

    def open_terminal(self) -> None:
        command = self.config.chat_argv()
        if not command:
            messagebox.showerror("Launch Chat Terminal", "The Hermes chat command was not found. Open Settings and select the Hermes executable.")
            return
        self._launch_terminal_command(command, "Hermes Chat", "Launch Chat Terminal")

    def open_telegram_setup(self) -> None:
        command = self.config.gateway_setup_argv()
        if not command:
            messagebox.showerror(
                "Configure Telegram",
                "The Hermes command was not found. Open Settings and select the Hermes executable.",
            )
            return
        if self._launch_terminal_command(command, "Hermes Telegram Setup", "Configure Telegram"):
            self.action_status.configure(
                text="Telegram setup opened in a terminal. Finish the wizard, then click Refresh Status.",
                fg=COLORS["gold_bright"],
            )

    def minimize_to_taskbar(self) -> None:
        """Minimize only the GUI; background stack processes and monitoring continue."""
        self.root.iconify()

    def force_refresh(self) -> None:
        def worker() -> None:
            try:
                self.events.put(("snapshot", self.probe.snapshot()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def save_settings(self) -> None:
        try:
            for key, variable in self.setting_vars.items():
                setattr(self.config, key, variable.get())
            self.config.normalized()
            save_config(self.config)
            launcher = Path(shutil.which("hermes-helper") or (application_root() / "launcher.py"))
            icon = application_root() / "assets" / "hermes_helper_icon.png"
            set_graphical_login_start(self.login_start_var.get(), launcher, icon)
            self.controller = ProcessController(self.config)
            self.probe = StatusProbe(self.config)
            self.action_status.configure(text="Settings saved and applied.", fg=COLORS["green"])
            self.force_refresh()
        except Exception as exc:
            messagebox.showerror("Save Settings", f"Could not save settings: {exc}")

    def check_updates(self, automatic: bool = False) -> None:
        if not self.config.update_manifest_url.strip():
            self.update_status.configure(text="No update channel is configured yet. Add your GitHub release manifest URL in Settings; the verification and installer engine are already built in.", fg=COLORS["orange"])
            if not automatic:
                self.notebook.select(self.updates_tab)
            return
        self.check_update_button.configure(state=tk.DISABLED)
        self.install_update_button.configure(state=tk.DISABLED)
        self.update_progress.configure(value=0)
        self.update_status.configure(text="Checking the verified release manifest…", fg=COLORS["gold_bright"])
        def worker() -> None:
            try:
                info = check_for_update(self.config.update_manifest_url, __version__)
                self.events.put(("update_available", info) if info else ("update_current", None))
            except Exception as exc:
                self.events.put(("update_error", f"Update check could not complete: {exc}"))
        threading.Thread(target=worker, name="hermes-helper-update-check", daemon=True).start()

    def _show_update_available(self, info: UpdateInfo) -> None:
        self.update_info = info
        self.check_update_button.configure(state=tk.NORMAL)
        self.install_update_button.configure(state=tk.NORMAL)
        notes = info.notes.strip() or "No release notes were supplied."
        self.update_status.configure(text=f"Hermes-Helper {info.version} is available.\n\n{notes}", fg=COLORS["gold_bright"])

    def install_update(self) -> None:
        info = self.update_info
        if info is None:
            messagebox.showinfo("Hermes-Helper Updates", "Check for updates first.")
            return
        if not messagebox.askyesno("Install Verified Update", f"Download, verify, and install Hermes-Helper {info.version}?\n\nThe current version will be retained as a recoverable backup. Hermes-Helper will restart after installation."):
            return
        self.check_update_button.configure(state=tk.DISABLED)
        self.install_update_button.configure(state=tk.DISABLED)
        def progress(received: int, total: int) -> None:
            self.events.put(("update_progress", (received, total)))
        def worker() -> None:
            try:
                package = download_update(info, progress)
                staged = stage_update(package)
                self.events.put(("update_staged", staged))
            except Exception as exc:
                self.events.put(("update_error", f"Verified update installation stopped safely: {exc}"))
        threading.Thread(target=worker, name="hermes-helper-update-install", daemon=True).start()

    def _finish_update_install(self, staged_project: Path) -> None:
        self.update_progress.configure(value=100)
        self.update_status.configure(text="Package verified. Installing with a recoverable backup…", fg=COLORS["green"])
        try:
            launch_installer(staged_project)
        except Exception as exc:
            self.update_status.configure(text=f"Verified package is staged, but the installer could not start: {exc}", fg=COLORS["critical"])
            self.check_update_button.configure(state=tk.NORMAL)
            return
        self.root.after(400, self.close)

    def repair_user_startup(self) -> None:
        items = [item for item in audit_startup() if item.scope == "User" and item.writable]
        if not items:
            messagebox.showinfo("Startup Repair", "No writable legacy Hermes user-startup entries were found.")
            return
        listing = "\n".join(str(item.path) for item in items)
        if not messagebox.askyesno("Startup Repair", f"Move these legacy user startup entries into a recoverable backup?\n\n{listing}\n\nSystem-wide services will not be changed."):
            return
        moved = backup_user_startup_items(items)
        messagebox.showinfo("Startup Repair", f"Backed up {len(moved)} item(s). Nothing was permanently deleted.")
        self.force_refresh()

    def export_report(self) -> None:
        if not self.latest:
            messagebox.showinfo("Export Report", "Wait for the first status check to finish.")
            return
        destination = filedialog.asksaveasfilename(title="Save Hermes-Helper diagnostic report", defaultextension=".txt", initialfile=f"hermes-helper-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.txt", filetypes=[("Text report", "*.txt")])
        if not destination:
            return
        snap = self.latest
        lines = [
            "Hermes-Helper Diagnostic Report",
            f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"Version: {__version__}",
            "",
            *[f"[{level}] {detail}" for level, detail in snap.diagnostics],
            "",
            "Processes:",
            *[f"Gateway PID {item.pid}: {item.command}" for item in snap.gateway],
            *[f"Ollama PID {item.pid}: {item.command}" for item in snap.ollama_server + snap.ollama_runner],
            "",
            "Temperatures:",
            *[f"{item.celsius:.1f} C | {item.category} | {item.source} | {item.label} | {item.path}" for item in snap.temperatures],
            "",
            "Startup entries:",
            *[f"{item.scope} | {item.kind} | pre_login={item.pre_login_risk} | {item.path}" for item in snap.startup_items],
            "",
            "Secrets and bot tokens are intentionally excluded.",
        ]
        Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.action_status.configure(text=f"Diagnostic report saved: {destination}", fg=COLORS["green"])

    def about(self) -> None:
        messagebox.showinfo("About Hermes-Helper", f"Hermes-Helper v{__version__}\n\nA cloud-aware control center for Hermes Agent, Ollama, Telegram, workload monitoring, and thermal safety.\n\nCreated for DoctorSUS by DoctorSUS & ChatGPT. 🖤")

    def close(self) -> None:
        try:
            self.config.window_geometry = self.root.geometry().split("+")[0]
            save_config(self.config)
        except Exception:
            pass
        self.stop_event.set()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
