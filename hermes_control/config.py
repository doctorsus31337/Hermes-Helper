from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


APP_NAME = "hermes-helper"


@dataclass(frozen=True)
class HermesModelSettings:
    """Non-secret model routing values read from Hermes's own configuration."""

    model: str = ""
    provider: str = ""
    base_url: str = ""
    context_length: int = 0
    max_tokens: int = 0

    @property
    def is_local(self) -> bool:
        provider = self.provider.strip().lower()
        if provider == "ollama":
            return True
        if self.base_url:
            try:
                hostname = (urlparse(self.base_url).hostname or "").lower()
            except ValueError:
                hostname = ""
            return hostname in {"127.0.0.1", "localhost", "::1"}
        return provider in {"", "custom"}

    @property
    def mode(self) -> str:
        return "local" if self.is_local else "cloud"

    @property
    def display_provider(self) -> str:
        return self.provider or ("local endpoint" if self.is_local else "cloud provider")


def _yaml_scalar(value: str) -> str:
    """Read the simple scalar values used by Hermes without adding a YAML dependency."""
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {'"', "'"} and value[-1:] == value[0]:
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def load_hermes_model_settings(hermes_home: str | Path) -> HermesModelSettings:
    """Read only the non-secret ``model`` block from Hermes config.yaml."""
    path = Path(hermes_home).expanduser() / "config.yaml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return HermesModelSettings()

    values: dict[str, str] = {}
    model_indent: int | None = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if model_indent is None:
            if stripped == "model:":
                model_indent = indent
            continue
        if indent <= model_indent:
            break
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        if key in {"default", "provider", "base_url", "context_length", "max_tokens"}:
            values[key] = _yaml_scalar(value)

    def integer(key: str) -> int:
        try:
            return max(0, int(values.get(key, "0")))
        except ValueError:
            return 0

    return HermesModelSettings(
        model=values.get("default", ""),
        provider=values.get("provider", ""),
        base_url=values.get("base_url", ""),
        context_length=integer("context_length"),
        max_tokens=integer("max_tokens"),
    )


def _xdg_dir(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name, "").strip()
    return Path(value).expanduser() if value else fallback


def config_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME


def state_dir() -> Path:
    base = _xdg_dir("XDG_STATE_HOME", Path.home() / ".local" / "state")
    return base / APP_NAME


@dataclass
class AppConfig:
    hermes_home: str = ""
    hermes_executable: str = ""
    gateway_command: str = ""
    ollama_executable: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3-4b-2507-abliterated-tools:latest"
    num_ctx: int = 16384
    poll_seconds: int = 4
    auto_load_model: bool = True
    auto_start_gateway: bool = True
    keep_model_loaded: bool = True
    close_to_tray: bool = False
    temperature_alerts: bool = True
    temperature_warning_c: int = 85
    temperature_critical_c: int = 95
    check_updates_on_start: bool = True
    update_manifest_url: str = "https://github.com/doctorsus31337/Hermes-Helper/releases/latest/download/update.json"
    window_geometry: str = "1180x760"

    @classmethod
    def defaults(cls) -> "AppConfig":
        home = Path(os.environ.get("HERMES_HOME", "")).expanduser() if os.environ.get("HERMES_HOME") else Path.home() / ".hermes"
        return cls(
            hermes_home=str(home),
            hermes_executable=discover_hermes_executable(home),
            ollama_executable=discover_ollama_executable(),
        )

    def normalized(self) -> "AppConfig":
        self.hermes_home = str(Path(self.hermes_home or Path.home() / ".hermes").expanduser())
        self.ollama_url = self.ollama_url.rstrip("/") or "http://127.0.0.1:11434"
        self.num_ctx = max(1024, min(int(self.num_ctx), 262144))
        self.poll_seconds = max(2, min(int(self.poll_seconds), 60))
        self.temperature_warning_c = max(50, min(int(self.temperature_warning_c), 110))
        self.temperature_critical_c = max(self.temperature_warning_c + 1, min(int(self.temperature_critical_c), 115))
        return self

    def gateway_argv(self) -> list[str]:
        if self.gateway_command.strip():
            return shlex.split(self.gateway_command)

        home = Path(self.hermes_home).expanduser()
        cli = home / "hermes-agent" / "cli.py"
        python = home / "hermes-agent" / "venv" / "bin" / "python"
        if cli.is_file() and python.is_file():
            return [str(python), str(cli), "--gateway"]
        executable = self.hermes_executable or discover_hermes_executable(Path(self.hermes_home))
        if executable:
            return [executable, "gateway", "start"]
        return []

    def hermes_model_settings(self) -> HermesModelSettings:
        return load_hermes_model_settings(self.hermes_home)

    def chat_argv(self) -> list[str]:
        """Return the interactive Hermes chat command without invoking a shell."""
        executable = self.hermes_executable or discover_hermes_executable(Path(self.hermes_home))
        if executable:
            return [executable, "chat"]

        home = Path(self.hermes_home).expanduser()
        cli = home / "hermes-agent" / "cli.py"
        python = home / "hermes-agent" / "venv" / "bin" / "python"
        if cli.is_file() and python.is_file():
            return [str(python), str(cli)]
        return []

    def gateway_setup_argv(self) -> list[str]:
        """Return Hermes's local interactive messaging setup wizard command."""
        executable = self.hermes_executable or discover_hermes_executable(Path(self.hermes_home))
        return [executable, "gateway", "setup"] if executable else []

    def ollama_argv(self) -> list[str]:
        executable = self.ollama_executable or discover_ollama_executable()
        return [executable, "serve"] if executable else []


def discover_hermes_executable(home: Path | None = None) -> str:
    home = (home or Path.home() / ".hermes").expanduser()
    candidates = [
        shutil.which("hermes"),
        str(Path.home() / ".local" / "bin" / "hermes"),
        str(home / "bin" / "hermes"),
        str(home / "hermes-agent" / "hermes"),
        str(home / "hermes-agent" / "venv" / "bin" / "hermes"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate))
    return ""


def discover_ollama_executable() -> str:
    candidates = [shutil.which("ollama"), "/usr/local/bin/ollama", str(Path.home() / ".local" / "bin" / "ollama")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate))
    return ""


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    defaults = AppConfig.defaults()
    target = path or config_path()
    try:
        raw: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return defaults.normalized()

    allowed = {item.name for item in fields(AppConfig)}
    values = asdict(defaults)
    values.update({key: value for key, value in raw.items() if key in allowed})
    try:
        return AppConfig(**values).normalized()
    except (TypeError, ValueError):
        return defaults.normalized()


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(config.normalized()), indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(target)
    return target


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def log_path() -> Path:
    target = state_dir() / "gateway.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
