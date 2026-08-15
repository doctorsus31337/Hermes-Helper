from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig, log_path
from .hardware import TemperatureReading, category_maxima, primary_cpu_temperature, read_temperatures
from .processes import CpuSampler, OllamaClient, ProcessInfo, find_processes
from .startup import StartupItem, audit_startup, graphical_login_start_enabled
from .telegram import TelegramHealth, check_health, parse_chat_ids, token_for_home


ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


@dataclass
class Snapshot:
    timestamp: float
    gateway: list[ProcessInfo] = field(default_factory=list)
    ollama_server: list[ProcessInfo] = field(default_factory=list)
    ollama_runner: list[ProcessInfo] = field(default_factory=list)
    ollama_reachable: bool = False
    installed_models: list[str] = field(default_factory=list)
    running_models: list[dict] = field(default_factory=list)
    telegram: TelegramHealth = field(default_factory=lambda: TelegramHealth(False, False))
    telegram_log_connected: bool = False
    telegram_updates: int = 0
    chat_ids: list[str] = field(default_factory=list)
    temperatures: list[TemperatureReading] = field(default_factory=list)
    startup_items: list[StartupItem] = field(default_factory=list)
    login_start_enabled: bool = False
    log_file: Path | None = None
    log_tail: str = ""
    log_issues: list[str] = field(default_factory=list)
    diagnostics: list[tuple[str, str]] = field(default_factory=list)

    @property
    def cpu_temp(self) -> TemperatureReading | None:
        return primary_cpu_temperature(self.temperatures)

    @property
    def temp_maxima(self) -> dict[str, TemperatureReading]:
        return category_maxima(self.temperatures)


@dataclass(frozen=True)
class StackReadiness:
    local_chat_ready: bool
    full_stack_ready: bool
    model_loaded: bool
    controllable_gateway: bool
    cloud_mode: bool
    active_model: str
    provider: str
    blockers: tuple[str, ...]


def stack_readiness(snap: Snapshot, config: AppConfig) -> StackReadiness:
    hermes_model = config.hermes_model_settings()
    cloud_mode = not hermes_model.is_local
    active_model = hermes_model.model or config.model
    provider = hermes_model.display_provider if hermes_model.model else "Ollama"
    running_names = [str(item.get("name", "")) for item in snap.running_models]
    model_loaded = active_model in running_names or any(
        name.split(":")[0] == active_model.split(":")[0] for name in running_names
    )
    chat_available = bool(config.chat_argv())
    controllable_gateway = any(item.controllable for item in snap.gateway)
    chat_ready = chat_available and (cloud_mode or (snap.ollama_reachable and model_loaded))
    blockers: list[str] = []
    if not cloud_mode:
        if not snap.ollama_reachable:
            blockers.append("Ollama API is offline")
        if not model_loaded:
            blockers.append("selected model is not loaded")
    if not chat_available:
        blockers.append("Hermes chat command was not found")
    if not controllable_gateway:
        blockers.append("no user-owned Hermes gateway is running")
    return StackReadiness(
        local_chat_ready=chat_ready,
        full_stack_ready=chat_ready and controllable_gateway,
        model_loaded=model_loaded,
        controllable_gateway=controllable_gateway,
        cloud_mode=cloud_mode,
        active_model=active_model,
        provider=provider,
        blockers=tuple(blockers),
    )


def sanitize_log_text(text: str) -> str:
    """Remove terminal escape sequences before rendering logs in Tk."""
    return ANSI_ESCAPE.sub("", text).replace("\r", "")


def _candidate_mtime(path: Path) -> float | None:
    """Return a log candidate's mtime only when it is a readable regular file."""
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        return None
    with path.open("rb"):
        pass
    return info.st_mtime


def _candidate_is_present(path: Path) -> bool:
    """Check the directory entry itself so a broken/inaccessible symlink is visible."""
    try:
        path.lstat()
        return True
    except OSError:
        return False


def find_log(config: AppConfig, issues: list[str] | None = None) -> Path | None:
    home = Path(config.hermes_home).expanduser()
    candidates: list[Path] = []
    try:
        candidates.append(log_path())
    except OSError as exc:
        if issues is not None:
            issues.append(f"Hermes-Helper log location is unavailable: {exc}")
    candidates.extend([
        home / "logs" / "gateway.log",
        home / "gateway.log",
        Path("/tmp/hermes-gateway.log"),
    ])
    readable: list[tuple[float, Path]] = []
    for path in dict.fromkeys(candidates):
        try:
            modified = _candidate_mtime(path)
        except OSError as exc:
            if issues is not None and _candidate_is_present(path):
                issues.append(f"Skipped unreadable gateway log {path}: {exc}")
            continue
        if modified is not None:
            readable.append((modified, path))
    return max(readable, key=lambda item: item[0], default=(0.0, None))[1]


def read_log_tail(path: Path | None, limit_bytes: int = 160_000, issues: list[str] | None = None) -> str:
    if path is None:
        return ""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - limit_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        if issues is not None:
            issues.append(f"Could not read selected gateway log {path}: {exc}")
        return ""


class StatusProbe:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.cpu = CpuSampler()
        self._telegram_cache: tuple[float, TelegramHealth] | None = None

    def _telegram(self) -> TelegramHealth:
        now = time.monotonic()
        if self._telegram_cache and now - self._telegram_cache[0] < 45:
            return self._telegram_cache[1]
        health = check_health(Path(self.config.hermes_home).expanduser())
        self._telegram_cache = (now, health)
        return health

    def snapshot(self) -> Snapshot:
        raw_gateway = find_processes("gateway")
        raw_ollama_server = find_processes("ollama_server")
        raw_ollama_runner = find_processes("ollama_runner")
        sampled = self.cpu.add_cpu(raw_gateway + raw_ollama_server + raw_ollama_runner)
        by_pid = {item.pid: item for item in sampled}
        gateway = [by_pid[item.pid] for item in raw_gateway if item.pid in by_pid]
        ollama_server = [by_pid[item.pid] for item in raw_ollama_server if item.pid in by_pid]
        ollama_runner = [by_pid[item.pid] for item in raw_ollama_runner if item.pid in by_pid]
        client = OllamaClient(self.config.ollama_url)
        reachable = client.is_reachable()
        log_issues: list[str] = []
        source_log = find_log(self.config, log_issues)
        tail = sanitize_log_text(read_log_tail(source_log, issues=log_issues))
        lower = tail.lower()
        connected = any(text in lower for text in ("telegram connected", "telegram polling", "telegram bot started", "getupdates"))
        disconnected = any(text in lower[-8000:] for text in ("telegram disconnected", "telegram error", "conflict: terminated by other getupdates"))

        snap = Snapshot(
            timestamp=time.time(),
            gateway=gateway,
            ollama_server=ollama_server,
            ollama_runner=ollama_runner,
            ollama_reachable=reachable,
            installed_models=client.tags() if reachable else [],
            running_models=client.running_models() if reachable else [],
            telegram=self._telegram(),
            telegram_log_connected=connected and not disconnected,
            telegram_updates=lower.count("getupdates") + lower.count("update_id"),
            chat_ids=parse_chat_ids(tail),
            temperatures=read_temperatures(),
            startup_items=audit_startup(),
            login_start_enabled=graphical_login_start_enabled(),
            log_file=source_log,
            log_tail=tail,
            log_issues=log_issues,
        )
        snap.diagnostics = self._diagnostics(snap)
        return snap

    def _diagnostics(self, snap: Snapshot) -> list[tuple[str, str]]:
        checks: list[tuple[str, str]] = []
        home = Path(self.config.hermes_home).expanduser()
        hermes_model = self.config.hermes_model_settings()
        cloud_mode = not hermes_model.is_local
        checks.append(("PASS" if home.is_dir() else "FAIL", f"Hermes home: {home}"))
        argv = self.config.gateway_argv()
        checks.append(("PASS" if argv else "FAIL", f"Gateway command: {' '.join(argv) if argv else 'not found'}"))
        if cloud_mode:
            checks.append(("PASS", f"Inference mode: CLOUD — {hermes_model.display_provider} / {hermes_model.model}"))
            checks.append(("PASS", "Ollama: not required by the active cloud provider"))
        else:
            checks.append(("PASS" if snap.ollama_reachable else "FAIL", f"Ollama API: {self.config.ollama_url}"))
            model_installed = self.config.model in snap.installed_models
            checks.append(("PASS" if model_installed else "WARN", f"Configured local model installed: {self.config.model}"))
        token_present = bool(token_for_home(home))
        token_detail = (
            f"Telegram token present for current Hermes home {home} (value never displayed)"
            if token_present
            else f"Telegram token not found for current Hermes home {home}"
        )
        checks.append(("PASS" if token_present else "WARN", token_detail))
        if snap.telegram.configured:
            checks.append(("PASS" if snap.telegram.reachable else "WARN", f"Telegram Bot API: {snap.telegram.bot_name or snap.telegram.detail}"))
        pre_login = [item for item in snap.startup_items if item.pre_login_risk]
        checks.append(("WARN" if pre_login else "PASS", f"Pre-login Hermes service entries: {len(pre_login)}"))
        foreign_gateway = [item for item in snap.gateway if not item.controllable]
        if foreign_gateway:
            checks.append(("FAIL", f"Gateway process(es) owned by another user: {', '.join(str(item.pid) for item in foreign_gateway)}"))
        cpu = snap.cpu_temp
        if cpu:
            level = "FAIL" if cpu.celsius >= self.config.temperature_critical_c else "WARN" if cpu.celsius >= self.config.temperature_warning_c else "PASS"
            checks.append((level, f"CPU temperature: {cpu.celsius:.1f} °C — {cpu.source} / {cpu.label}"))
        else:
            checks.append(("WARN", "CPU temperature sensor: unavailable"))
        for issue in snap.log_issues:
            checks.append(("WARN", issue))
        if snap.log_file:
            checks.append(("PASS", f"Gateway log: {snap.log_file}"))
        else:
            checks.append(("WARN", "Gateway log: no readable log found yet"))
        return checks
