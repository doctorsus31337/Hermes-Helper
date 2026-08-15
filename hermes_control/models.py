"""Safe Hermes model configuration through the supported CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
from typing import Callable, Mapping, Sequence


_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,255}$")


class ModelSettingsError(RuntimeError):
    """Raised when model settings are invalid or Hermes rejects them."""


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    model: str
    context_length: int
    max_tokens: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ModelSettings":
        try:
            provider_value = value.get("provider", "")
            model_value = value.get("default", "")
            if not isinstance(provider_value, str) or not isinstance(model_value, str):
                raise TypeError("provider and default must be strings")
            context_length = int(str(value.get("context_length", 65_536)))
            settings = cls(
                provider=provider_value,
                model=model_value,
                context_length=context_length,
                max_tokens=int(str(value.get("max_tokens", min(8_192, context_length)))),
            )
        except (TypeError, ValueError) as exc:
            raise ModelSettingsError(f"Hermes returned invalid model settings: {exc}") from exc
        if settings.provider and settings.model:
            settings.validate()
        return settings

    def validate(self) -> None:
        if not _PROVIDER_RE.fullmatch(self.provider):
            raise ModelSettingsError("Provider must start with a letter or number and contain only letters, numbers, '.', '_' or '-'.")
        if not _MODEL_RE.fullmatch(self.model):
            raise ModelSettingsError("Model must start with a letter or number and use a normal provider/model identifier.")
        if not 1_024 <= self.context_length <= 1_048_576:
            raise ModelSettingsError("Context length must be between 1,024 and 1,048,576 tokens.")
        if not 256 <= self.max_tokens <= self.context_length:
            raise ModelSettingsError("Max output tokens must be between 256 and the selected context length.")


Runner = Callable[..., subprocess.CompletedProcess[str]]


class HermesModelManager:
    """Read and transactionally update the active Hermes model configuration."""

    def __init__(
        self,
        hermes_executable: str,
        *,
        hermes_home: str | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        executable = hermes_executable.strip()
        if not executable:
            raise ModelSettingsError("The Hermes executable is not configured.")
        self.executable = executable
        self.runner = runner
        self.environment = os.environ.copy()
        if hermes_home:
            self.environment["HERMES_HOME"] = hermes_home

    def _run(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(
            list(argv),
            capture_output=True,
            text=True,
            timeout=20,
            env=self.environment,
        )

    @staticmethod
    def _failure(result: subprocess.CompletedProcess[str]) -> str:
        return (result.stderr or result.stdout or f"command exited {result.returncode}").strip()

    def _load_payload(self) -> object:
        result = self._run([self.executable, "config", "get", "model", "--json"])
        if result.returncode != 0:
            raise ModelSettingsError(f"Could not read Hermes model settings: {self._failure(result)}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ModelSettingsError("Hermes returned invalid JSON for the model configuration.") from exc

    def load(self) -> ModelSettings:
        value = self._load_payload()
        if value is None or value == "":
            return ModelSettings(provider="", model="", context_length=65_536, max_tokens=8_192)
        if isinstance(value, str):
            provider = value.split("/", 1)[0] if "/" in value else ""
            return ModelSettings(provider=provider, model=value, context_length=65_536, max_tokens=8_192)
        if not isinstance(value, dict):
            raise ModelSettingsError("Hermes returned an unexpected model configuration.")
        return ModelSettings.from_mapping(value)

    def snapshot(self) -> object:
        """Capture the exact model shape for a later rollback."""
        return self._load_payload()

    def _write(self, settings: ModelSettings) -> None:
        pairs = (
            ("model.provider", settings.provider),
            ("model.default", settings.model),
            ("model.context_length", str(settings.context_length)),
            ("model.max_tokens", str(settings.max_tokens)),
        )
        for key, value in pairs:
            result = self._run([self.executable, "config", "set", key, value])
            if result.returncode != 0:
                raise ModelSettingsError(f"Hermes rejected {key}: {self._failure(result)}")

    def _check(self) -> None:
        result = self._run([self.executable, "config", "check"])
        if result.returncode != 0:
            raise ModelSettingsError(f"Hermes configuration validation failed: {self._failure(result)}")

    def _restore(self, original: object) -> None:
        if isinstance(original, str) and original:
            unset = self._run([self.executable, "config", "unset", "model"])
            if unset.returncode != 0:
                raise ModelSettingsError(f"Could not clear the expanded model block for scalar rollback: {self._failure(unset)}")
            result = self._run([self.executable, "config", "set", "model", original])
            if result.returncode != 0:
                raise ModelSettingsError(f"Could not restore the scalar model setting: {self._failure(result)}")
            self._check()
            return
        if not isinstance(original, dict):
            result = self._run([self.executable, "config", "unset", "model"])
            if result.returncode != 0:
                raise ModelSettingsError(f"Could not remove the newly created model block: {self._failure(result)}")
            self._check()
            return
        keys = ("provider", "default", "context_length", "max_tokens")
        for name in keys:
            dotted = f"model.{name}"
            if name in original:
                result = self._run([self.executable, "config", "set", dotted, str(original[name])])
            else:
                result = self._run([self.executable, "config", "unset", dotted])
            if result.returncode != 0:
                raise ModelSettingsError(f"Could not restore {dotted}: {self._failure(result)}")
        self._check()

    def restore(self, snapshot: object) -> None:
        """Restore a snapshot captured by :meth:`snapshot`."""
        self._restore(snapshot)

    def apply(self, settings: ModelSettings) -> str:
        settings.validate()
        original = self._load_payload()
        try:
            self._write(settings)
            self._check()
        except Exception as exc:
            try:
                self._restore(original)
            except Exception as rollback_exc:
                raise ModelSettingsError(
                    f"The model update failed, and automatic rollback also failed: {rollback_exc}. Original error: {exc}"
                ) from exc
            raise ModelSettingsError(f"The model update failed; the previous settings were restored. {exc}") from exc
        return f"Active Hermes model saved: {settings.provider} / {settings.model}."
