"""Safe configuration bridge for Hermes's built-in delegation engine."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
from typing import Callable, Sequence


_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,255}$")
_SAFE_FIELDS = (
    "provider",
    "model",
    "max_concurrent_children",
    "max_spawn_depth",
    "orchestrator_enabled",
)


class AgentConfigurationError(RuntimeError):
    """Hermes rejected an agent-team delegation configuration."""


@dataclass(frozen=True)
class DelegationSettings:
    provider: str
    model: str
    max_concurrent: int

    def validate(self) -> None:
        if not _PROVIDER_RE.fullmatch(self.provider):
            raise AgentConfigurationError("Worker provider must be a normal Hermes provider identifier.")
        if not _MODEL_RE.fullmatch(self.model):
            raise AgentConfigurationError("Worker model must be a normal provider/model identifier.")
        if not 1 <= int(self.max_concurrent) <= 8:
            raise AgentConfigurationError("Agent concurrency must be between 1 and 8.")


Runner = Callable[..., subprocess.CompletedProcess[str]]


class HermesDelegationManager:
    """Transactionally configure supported, non-secret Hermes delegation keys."""

    def __init__(
        self,
        hermes_executable: str,
        *,
        hermes_home: str | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        executable = hermes_executable.strip()
        if not executable:
            raise AgentConfigurationError("The Hermes executable is not configured.")
        self.executable = executable
        self.runner = runner
        self.environment = os.environ.copy()
        if hermes_home:
            self.environment["HERMES_HOME"] = str(hermes_home)

    def _run(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(list(argv), capture_output=True, text=True, timeout=20, env=self.environment)

    @staticmethod
    def _failure(result: subprocess.CompletedProcess[str]) -> str:
        return (result.stderr or result.stdout or f"command exited {result.returncode}").strip()

    def _get(self, name: str) -> object:
        if name not in _SAFE_FIELDS:
            raise AgentConfigurationError(f"Refusing to read unsupported delegation field: {name}")
        result = self._run([self.executable, "config", "get", f"delegation.{name}", "--json"])
        if result.returncode != 0:
            raise AgentConfigurationError(f"Could not read delegation.{name}: {self._failure(result)}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AgentConfigurationError(f"Hermes returned invalid JSON for delegation.{name}.") from exc

    def _snapshot(self) -> dict[str, object]:
        return {name: self._get(name) for name in _SAFE_FIELDS}

    def load(self) -> DelegationSettings:
        snapshot = self._snapshot()
        try:
            settings = DelegationSettings(
                provider=str(snapshot["provider"]),
                model=str(snapshot["model"]),
                max_concurrent=int(str(snapshot["max_concurrent_children"])),
            )
        except (TypeError, ValueError) as exc:
            raise AgentConfigurationError(f"Hermes returned incomplete delegation settings: {exc}") from exc
        return settings

    @staticmethod
    def _desired(settings: DelegationSettings) -> dict[str, object]:
        return {
            "provider": settings.provider,
            "model": settings.model,
            "max_concurrent_children": int(settings.max_concurrent),
            "max_spawn_depth": 1,
            "orchestrator_enabled": True,
        }

    def _set(self, name: str, value: object) -> None:
        if name not in _SAFE_FIELDS:
            raise AgentConfigurationError(f"Refusing to write unsupported delegation field: {name}")
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        result = self._run([self.executable, "config", "set", f"delegation.{name}", rendered])
        if result.returncode != 0:
            raise AgentConfigurationError(f"Hermes rejected delegation.{name}: {self._failure(result)}")

    def _check(self) -> None:
        result = self._run([self.executable, "config", "check"])
        if result.returncode != 0:
            raise AgentConfigurationError(f"Hermes configuration validation failed: {self._failure(result)}")

    def _write(self, values: dict[str, object]) -> None:
        for name in _SAFE_FIELDS:
            self._set(name, values[name])

    def apply(self, settings: DelegationSettings) -> str:
        settings.validate()
        original = self._snapshot()
        try:
            self._write(self._desired(settings))
            self._check()
        except Exception as exc:
            try:
                self._write(original)
                self._check()
            except Exception as rollback_exc:
                raise AgentConfigurationError(
                    f"The agent setup failed, and automatic rollback also failed: {rollback_exc}. Original error: {exc}"
                ) from exc
            raise AgentConfigurationError(f"The agent setup failed; previous delegation settings were restored. {exc}") from exc
        return f"Hermes delegation ready: {settings.provider} / {settings.model}, up to {settings.max_concurrent} parallel workers."
