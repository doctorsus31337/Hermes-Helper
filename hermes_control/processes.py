from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig, log_path, state_dir


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command: str
    rss_bytes: int = 0
    cpu_percent: float = 0.0
    owner_uid: int = -1

    @property
    def controllable(self) -> bool:
        return self.owner_uid == os.getuid()


def _read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return " ".join(part.decode(errors="replace") for part in raw.split(b"\0") if part)
    except (OSError, ValueError):
        return ""


def _read_rss(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _pids(owned_only: bool = False) -> Iterable[tuple[int, int]]:
    current_uid = os.getuid()
    try:
        items = list(Path("/proc").iterdir())
    except OSError:
        return
    for item in items:
        if not item.name.isdigit():
            continue
        try:
            owner_uid = item.stat().st_uid
            if not owned_only or owner_uid == current_uid:
                yield int(item.name), owner_uid
        except OSError:
            continue


def find_processes(kind: str, owned_only: bool = False) -> list[ProcessInfo]:
    own_pid = os.getpid()
    matches: list[ProcessInfo] = []
    for pid, owner_uid in _pids(owned_only):
        if pid == own_pid:
            continue
        command = _read_cmdline(pid)
        lower = command.lower()
        if not command or "hermes-control-center" in lower:
            continue
        if kind == "gateway":
            matched = (
                ("hermes" in lower and "gateway" in lower)
                or ("cli.py" in lower and "--gateway" in lower)
                or ("gateway/run.py" in lower)
            )
        elif kind == "ollama_server":
            matched = "ollama" in lower and "serve" in lower and "runner" not in lower
        elif kind == "ollama_runner":
            matched = "ollama" in lower and "runner" in lower
        else:
            matched = False
        if matched:
            matches.append(ProcessInfo(pid=pid, command=command, rss_bytes=_read_rss(pid), owner_uid=owner_uid))
    return sorted(matches, key=lambda item: item.pid)


class CpuSampler:
    def __init__(self) -> None:
        self._last_total: int | None = None
        self._last_process: dict[int, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _total_ticks() -> int:
        try:
            values = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            return sum(int(value) for value in values)
        except (OSError, ValueError, IndexError):
            return 0

    @staticmethod
    def _process_ticks(pid: int) -> int:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().split()
            return int(fields[13]) + int(fields[14])
        except (OSError, ValueError, IndexError):
            return 0

    def add_cpu(self, processes: list[ProcessInfo]) -> list[ProcessInfo]:
        with self._lock:
            total = self._total_ticks()
            cpu_count = os.cpu_count() or 1
            delta_total = total - self._last_total if self._last_total is not None else 0
            output: list[ProcessInfo] = []
            current: dict[int, int] = {}
            for item in processes:
                ticks = self._process_ticks(item.pid)
                current[item.pid] = ticks
                previous = self._last_process.get(item.pid)
                percent = 0.0
                if previous is not None and delta_total > 0:
                    percent = max(0.0, (ticks - previous) / delta_total * 100.0 * cpu_count)
                output.append(ProcessInfo(item.pid, item.command, item.rss_bytes, percent, item.owner_uid))
            self._last_total = total
            self._last_process = current
            return output


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 2.5) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def is_reachable(self) -> bool:
        try:
            self._request("/api/tags")
            return True
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def tags(self) -> list[str]:
        try:
            return [str(item.get("name", "")) for item in self._request("/api/tags").get("models", []) if item.get("name")]
        except (OSError, ValueError, urllib.error.URLError):
            return []

    def running_models(self) -> list[dict[str, Any]]:
        try:
            return list(self._request("/api/ps").get("models", []))
        except (OSError, ValueError, urllib.error.URLError):
            return []

    def load_model(self, model: str, num_ctx: int, keep_loaded: bool = True) -> None:
        self._request(
            "/api/generate",
            {
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": -1 if keep_loaded else "10m",
                "options": {"num_ctx": num_ctx},
            },
            timeout=240,
        )

    def unload_model(self, model: str) -> None:
        self._request("/api/generate", {"model": model, "prompt": "", "stream": False, "keep_alive": 0}, timeout=30)


class ProcessController:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.runtime = state_dir() / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)

    def _pid_file(self, name: str) -> Path:
        return self.runtime / f"{name}.json"

    def _remember(self, name: str, process: subprocess.Popen[Any], argv: list[str]) -> None:
        self._pid_file(name).write_text(json.dumps({"pid": process.pid, "argv": argv, "started": time.time()}), encoding="utf-8")

    def _start(self, name: str, argv: list[str], cwd: Path | None, output_path: Path) -> int:
        if not argv:
            raise RuntimeError(f"No {name} command was found. Open Settings and select the executable.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        handle = output_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd and cwd.is_dir() else None,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env={**os.environ, "HERMES_HOME": self.config.hermes_home},
        )
        handle.close()
        self._remember(name, process, argv)
        return process.pid

    def start_ollama(self) -> str:
        client = OllamaClient(self.config.ollama_url)
        if client.is_reachable():
            return "Ollama is already reachable; the existing server was left untouched."
        pid = self._start("ollama", self.config.ollama_argv(), None, state_dir() / "ollama.log")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if client.is_reachable():
                return f"Ollama started successfully (PID {pid})."
            time.sleep(0.5)
        raise RuntimeError("Ollama started but its API did not become reachable within 20 seconds. Check the Ollama log.")

    def start_gateway(self) -> str:
        running = find_processes("gateway")
        if running:
            foreign = [item for item in running if not item.controllable]
            if foreign:
                identities = ", ".join(f"PID {item.pid} (UID {item.owner_uid})" for item in foreign)
                raise RuntimeError(
                    "Hermes Gateway is already running under a different Linux account: "
                    f"{identities}. Hermes-Helper will not control or duplicate it. "
                    "Open Diagnostics and repair the legacy pre-login service first."
                )
            return f"Hermes Gateway is already running (PID {running[0].pid}); no duplicate was started."
        home = Path(self.config.hermes_home).expanduser() / "hermes-agent"
        pid = self._start("gateway", self.config.gateway_argv(), home, log_path())
        time.sleep(1)
        if not Path(f"/proc/{pid}").exists():
            raise RuntimeError("Hermes Gateway exited immediately. Open Live Console for the startup error.")
        return f"Hermes Gateway launch requested (PID {pid})."

    @staticmethod
    def _terminate(processes: list[ProcessInfo], label: str) -> str:
        processes = [item for item in processes if item.controllable]
        if not processes:
            return f"{label} has no process owned by this user to stop."
        for item in processes:
            try:
                os.kill(item.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        deadline = time.monotonic() + 7
        pending = {item.pid for item in processes}
        while pending and time.monotonic() < deadline:
            pending = {pid for pid in pending if Path(f"/proc/{pid}").exists()}
            if pending:
                time.sleep(0.15)
        return f"{label} stopped." if not pending else f"{label} received a stop request; {len(pending)} process(es) are still exiting."

    def stop_gateway(self) -> str:
        return self._terminate(find_processes("gateway", owned_only=True), "Hermes Gateway")

    def stop_managed_ollama(self) -> str:
        record = self._pid_file("ollama")
        try:
            pid = int(json.loads(record.read_text()).get("pid"))
        except (OSError, ValueError, TypeError):
            return "Ollama was not started by this app, so its server was left untouched."
        matches = [item for item in find_processes("ollama_server", owned_only=True) if item.pid == pid]
        return self._terminate(matches, "Managed Ollama server")

    def start_stack(self) -> str:
        """Start the explicit local Ollama stack."""
        notes = [self.start_ollama()]
        if self.config.auto_load_model:
            OllamaClient(self.config.ollama_url).load_model(self.config.model, self.config.num_ctx, self.config.keep_model_loaded)
            notes.append(f"Model loaded: {self.config.model}")
        if self.config.auto_start_gateway:
            notes.append(self.start_gateway())
        return "\n".join(notes)

    def start_server(self) -> str:
        """Start the correct background services for Hermes's active provider."""
        model = self.config.hermes_model_settings()
        if not model.is_local:
            active_model = model.model or "provider default"
            return (
                f"Cloud mode detected: {model.display_provider} / {active_model}.\n"
                "Ollama was left unloaded.\n"
                f"{self.start_gateway()}"
            )
        return self.start_stack()

    def stop_stack(self) -> str:
        notes = [self.stop_gateway()]
        try:
            if OllamaClient(self.config.ollama_url).is_reachable():
                OllamaClient(self.config.ollama_url).unload_model(self.config.model)
                notes.append(f"Model unloaded: {self.config.model}")
        except Exception as exc:
            notes.append(f"Model unload warning: {exc}")
        notes.append(self.stop_managed_ollama())
        return "\n".join(notes)

    def stop_server(self) -> str:
        """Stop only the services appropriate for Hermes's active provider."""
        model = self.config.hermes_model_settings()
        if not model.is_local:
            return f"Cloud mode: Ollama was left untouched.\n{self.stop_gateway()}"
        return self.stop_stack()
