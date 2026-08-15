"""Private, non-secret model profiles and visual agent-team presets."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

from .config import config_dir
from .models import ModelSettings, ModelSettingsError


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class PresetValidationError(ValueError):
    """A local model or team preset is malformed."""


def _text(value: object, label: str, maximum: int, *, required: bool = True) -> str:
    text = str(value).strip()
    if (required and not text) or len(text) > maximum or any(ord(char) < 32 and char not in "\t" for char in text):
        qualifier = f"1-{maximum}" if required else f"0-{maximum}"
        raise PresetValidationError(f"{label} must contain {qualifier} normal text characters.")
    return text


def _identifier(value: object, label: str) -> str:
    identifier = str(value).strip()
    if not _ID_RE.fullmatch(identifier):
        raise PresetValidationError(f"{label} must be a simple 1-64 character identifier.")
    return identifier


def _json_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise PresetValidationError(f"{key} must be a JSON string.")
    return item


def _json_integer(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int):
        raise PresetValidationError(f"{key} must be a JSON integer.")
    return item


@dataclass(frozen=True)
class ModelProfile:
    id: str
    name: str
    provider: str
    model: str
    context_length: int
    max_tokens: int

    def validate(self) -> None:
        _identifier(self.id, "Profile ID")
        _text(self.name, "Profile name", 80)
        try:
            ModelSettings(self.provider, self.model, int(self.context_length), int(self.max_tokens)).validate()
        except (ModelSettingsError, TypeError, ValueError) as exc:
            raise PresetValidationError(str(exc)) from exc

    def settings(self) -> ModelSettings:
        self.validate()
        return ModelSettings(self.provider, self.model, int(self.context_length), int(self.max_tokens))

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "context_length": self.context_length,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ModelProfile":
        try:
            profile = cls(
                _json_string(value, "id"), _json_string(value, "name"),
                _json_string(value, "provider"), _json_string(value, "model"),
                _json_integer(value, "context_length"), _json_integer(value, "max_tokens"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PresetValidationError(f"Invalid model profile: {exc}") from exc
        profile.validate()
        return profile


@dataclass(frozen=True)
class AgentNode:
    id: str
    name: str
    instructions: str

    def validate(self) -> None:
        _identifier(self.id, "Agent ID")
        _text(self.name, "Agent name", 80)
        _text(self.instructions, "Agent instructions", 2_000)

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {"id": self.id, "name": self.name, "instructions": self.instructions}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentNode":
        try:
            node = cls(_json_string(value, "id"), _json_string(value, "name"), _json_string(value, "instructions"))
        except (KeyError, TypeError) as exc:
            raise PresetValidationError(f"Invalid agent branch: {exc}") from exc
        node.validate()
        return node


@dataclass(frozen=True)
class AgentTeam:
    id: str
    name: str
    head_profile_id: str
    worker_profile_id: str
    max_concurrent: int
    nodes: tuple[AgentNode, ...]

    def validate(self) -> None:
        _identifier(self.id, "Team ID")
        _text(self.name, "Team name", 80)
        _identifier(self.head_profile_id, "Head profile ID")
        _identifier(self.worker_profile_id, "Worker profile ID")
        if not 1 <= int(self.max_concurrent) <= 8:
            raise PresetValidationError("Agent concurrency must be between 1 and 8.")
        if not 1 <= len(self.nodes) <= 8:
            raise PresetValidationError("A team must contain between 1 and 8 worker branches.")
        ids: set[str] = set()
        for node in self.nodes:
            node.validate()
            if node.id in ids:
                raise PresetValidationError(f"Duplicate agent ID: {node.id}")
            ids.add(node.id)

    def build_brief(self, objective: str) -> str:
        self.validate()
        objective = _text(objective, "Team objective", 4_000)
        tasks = "\n".join(
            f"  {index}. {node.name}: {node.instructions}"
            for index, node in enumerate(self.nodes, start=1)
        )
        return (
            f"AGENT TEAM: {self.name}\n"
            f"OBJECTIVE: {objective}\n\n"
            "You are HEAD ALPHA. Coordinate the specialist branches below using Hermes's delegate_task tool. "
            f"Dispatch independent branches in parallel waves of at most {self.max_concurrent}, give each child the complete objective and its role, "
            "wait for every result, critically reconcile disagreements, verify important claims, and deliver one synthesized answer.\n\n"
            f"SPECIALIST BRANCHES:\n{tasks}\n\n"
            "Do not claim a branch ran unless delegate_task returned its result. Clearly label any blocked or uncertain finding."
        )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "id": self.id,
            "name": self.name,
            "head_profile_id": self.head_profile_id,
            "worker_profile_id": self.worker_profile_id,
            "max_concurrent": self.max_concurrent,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentTeam":
        try:
            raw_nodes = value["nodes"]
            if not isinstance(raw_nodes, list):
                raise TypeError("nodes must be a list")
            if any(not isinstance(node, dict) for node in raw_nodes):
                raise TypeError("every node must be an object")
            team = cls(
                _json_string(value, "id"), _json_string(value, "name"),
                _json_string(value, "head_profile_id"), _json_string(value, "worker_profile_id"),
                _json_integer(value, "max_concurrent"),
                tuple(AgentNode.from_dict(node) for node in raw_nodes),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PresetValidationError(f"Invalid agent team: {exc}") from exc
        team.validate()
        return team


class PresetStore:
    """Persist bounded, non-secret presets with owner-only permissions."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "presets.json")

    @staticmethod
    def _unique_ids(items: Iterable[ModelProfile | AgentTeam], label: str) -> None:
        seen: set[str] = set()
        for item in items:
            if item.id in seen:
                raise PresetValidationError(f"Duplicate {label} ID: {item.id}")
            seen.add(item.id)

    def load(self) -> tuple[list[ModelProfile], list[AgentTeam]]:
        if not self.path.exists():
            return [], []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("preset root must be an object")
            if payload.get("format") != 1:
                raise ValueError("unsupported preset format version")
            raw_profiles = payload.get("model_profiles", [])
            raw_teams = payload.get("agent_teams", [])
            if not isinstance(raw_profiles, list) or not isinstance(raw_teams, list):
                raise TypeError("preset collections must be lists")
            if any(not isinstance(item, dict) for item in raw_profiles + raw_teams):
                raise TypeError("every preset must be an object")
            profiles = [ModelProfile.from_dict(item) for item in raw_profiles]
            teams = [AgentTeam.from_dict(item) for item in raw_teams]
            self._unique_ids(profiles, "profile")
            self._unique_ids(teams, "team")
            profile_ids = {profile.id for profile in profiles}
            for team in teams:
                if team.head_profile_id not in profile_ids or team.worker_profile_id not in profile_ids:
                    raise PresetValidationError(f"Team {team.name} refers to a missing model profile.")
            return profiles, teams
        except (OSError, ValueError, TypeError) as exc:
            raise PresetValidationError(f"Could not read preset store: {exc}") from exc

    def save(self, profiles: list[ModelProfile], teams: list[AgentTeam]) -> None:
        if len(profiles) > 32 or len(teams) > 32:
            raise PresetValidationError("At most 32 model profiles and 32 agent teams may be saved.")
        for profile in profiles:
            profile.validate()
        for team in teams:
            team.validate()
        self._unique_ids(profiles, "profile")
        self._unique_ids(teams, "team")
        profile_ids = {profile.id for profile in profiles}
        for team in teams:
            if team.head_profile_id not in profile_ids or team.worker_profile_id not in profile_ids:
                raise PresetValidationError(f"Team {team.name} refers to a missing model profile.")
        payload = {
            "format": 1,
            "model_profiles": [profile.to_dict() for profile in profiles],
            "agent_teams": [team.to_dict() for team in teams],
        }
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise PresetValidationError(f"Could not save preset store: {exc}") from exc
