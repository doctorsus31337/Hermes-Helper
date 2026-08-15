from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from hermes_control.presets import AgentNode, AgentTeam, ModelProfile, PresetStore, PresetValidationError


class PresetStoreTests(unittest.TestCase):
    def test_model_profiles_and_agent_teams_round_trip_privately(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "presets.json"
            store = PresetStore(path)
            profile = ModelProfile("alpha", "Alpha Reasoning", "openai-codex", "gpt-5.6-sol", 131072, 16384)
            worker = ModelProfile("worker", "Worker", "openrouter", "nous/hermes", 32768, 4096)
            team = AgentTeam(
                id="team-1",
                name="Code Council",
                head_profile_id="alpha",
                worker_profile_id="worker",
                max_concurrent=2,
                nodes=(AgentNode("review", "Security Reviewer", "Audit security boundaries and report evidence."),),
            )

            store.save([profile, worker], [team])
            profiles, teams = store.load()

            self.assertEqual(profiles, [profile, worker])
            self.assertEqual(teams, [team])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertNotIn("api_key", path.read_text(encoding="utf-8"))

    def test_invalid_or_duplicate_presets_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = PresetStore(Path(raw) / "presets.json")
            duplicate = ModelProfile("same", "One", "openrouter", "model/a", 32768, 4096)
            with self.assertRaises(PresetValidationError):
                store.save([duplicate, duplicate], [])
            with self.assertRaises(PresetValidationError):
                AgentNode("bad id!", "Reviewer", "Review").validate()

    def test_malformed_collection_entries_are_not_silently_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "presets.json"
            path.write_text('{"model_profiles": [42], "agent_teams": []}', encoding="utf-8")
            with self.assertRaises(PresetValidationError):
                PresetStore(path).load()

    def test_root_type_format_and_filesystem_failures_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "presets.json"
            for payload in ('[]', '{"format": 2, "model_profiles": [], "agent_teams": []}'):
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(PresetValidationError):
                    PresetStore(path).load()

            profile = ModelProfile("alpha", "Alpha", "openrouter", "model/a", 32768, 4096)
            invalid_null = {
                "format": 1,
                "model_profiles": [{"id": None, "name": None, "provider": None, "model": None, "context_length": 32768, "max_tokens": 4096}],
                "agent_teams": [],
            }
            path.write_text(json.dumps(invalid_null), encoding="utf-8")
            with self.assertRaises(PresetValidationError):
                PresetStore(path).load()

            broken_reference = {
                "format": 1,
                "model_profiles": [],
                "agent_teams": [{
                    "id": "team", "name": "Team", "head_profile_id": "missing",
                    "worker_profile_id": "missing", "max_concurrent": 1,
                    "nodes": [{"id": "node", "name": "Node", "instructions": "Review"}],
                }],
            }
            path.write_text(json.dumps(broken_reference), encoding="utf-8")
            with self.assertRaises(PresetValidationError):
                PresetStore(path).load()

            with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(PresetValidationError, "disk full"):
                    PresetStore(path).save([profile], [])

    def test_team_brief_maps_visual_branches_to_delegate_tasks(self) -> None:
        team = AgentTeam(
            id="team",
            name="Research Council",
            head_profile_id="alpha",
            worker_profile_id="worker",
            max_concurrent=2,
            nodes=(
                AgentNode("facts", "Evidence Scout", "Find verifiable evidence."),
                AgentNode("critic", "Red Team", "Challenge assumptions and identify risks."),
            ),
        )
        brief = team.build_brief("Assess the launch plan")
        self.assertIn("HEAD ALPHA", brief)
        self.assertIn("delegate_task", brief)
        self.assertIn("Evidence Scout", brief)
        self.assertIn("Red Team", brief)
        self.assertIn("Assess the launch plan", brief)


if __name__ == "__main__":
    unittest.main()
