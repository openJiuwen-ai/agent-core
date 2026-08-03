# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ``TeamSkillGenerator``."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

import openjiuwen.rsi.team_skill_generator.generator as generator_module
from openjiuwen.rsi.team_skill_generator import TeamSkillGenerator


@pytest.mark.asyncio
async def test_generate_materializes_structured_plan_without_creator_agent(
    tmp_path: Path,
) -> None:
    """The primary path should write a complete Team Skill from a bounded plan."""
    output_dir = tmp_path / "generated"
    plan_calls = 0

    async def fake_plan_runner(task: str, workspace: Path, previous_error: str) -> dict[str, Any]:
        nonlocal plan_calls
        plan_calls += 1
        assert "browser game" in task
        assert workspace == output_dir.resolve()
        assert previous_error == ""
        return {
            "team_skill_plan": {
                "team_name": "browser-game-team",
                "description": "Create a browser game through specialized planning, implementation, and review roles.",
                "roles": [
                    {
                        "id": "game-designer",
                        "purpose": "Design rules and game state.",
                        "motto": "I make the rules playable before anyone paints the board.",
                        "responsibilities": [
                            "Define the win and loss conditions.",
                            "Specify turn flow and state transitions.",
                        ],
                        "success_criteria": [
                            "Rules are playable from start to end.",
                            "State transitions are explicit.",
                        ],
                        "forbidden": [
                            "Do not write final HTML, CSS, or JavaScript files.",
                        ],
                        "mandatory": [
                            "You MUST provide concrete mechanics and state names.",
                        ],
                        "output_sections": ["Rules", "State Model", "Edge Cases"],
                        "skills": [],
                        "tools": [],
                    },
                    {
                        "id": "frontend-engineer",
                        "purpose": "Build the browser deliverable.",
                        "motto": "I ship the game only when the files actually run together.",
                        "responsibilities": [
                            "Implement the requested files.",
                            "Connect UI events to state updates.",
                        ],
                        "success_criteria": [
                            "The deliverable opens directly in a browser.",
                            "Interactions update visible state.",
                        ],
                        "forbidden": [
                            "Do not leave placeholder handlers.",
                        ],
                        "mandatory": [
                            "You MUST verify linked files and runtime flow.",
                        ],
                        "output_sections": ["Files Changed", "Runtime Checks"],
                        "skills": [],
                        "tools": [],
                    },
                ],
                "workflow_steps": [
                    {
                        "name": "Define game contract",
                        "executor": "game-designer",
                        "action": "Produce the mechanics, state model, and terminal conditions.",
                        "output": "Game contract",
                        "quality_gate": "Contract names the player actions, AI actions, and terminal outcomes.",
                    },
                    {
                        "name": "Build browser game",
                        "executor": "frontend-engineer",
                        "action": "Implement the files from the game contract.",
                        "output": "Playable browser files",
                        "quality_gate": "All requested files exist and the interaction loop is connected.",
                    },
                ],
                "acceptance_criteria": [
                    "All requested deliverable files are present.",
                    "The game has a complete start-to-finish play loop.",
                ],
            }
        }

    generator = TeamSkillGenerator(
        model_config_ref="",
        creator_resource_dir=generator_module._default_creator_resource_dir(),
        plan_runner=fake_plan_runner,
    )

    generated = Path(await generator.generate("Create a browser game.", output_dir))

    assert generated == (output_dir / "browser-game-team").resolve()
    assert plan_calls == 1
    assert (generated / "SKILL.md").is_file()
    assert (generated / "workflow.md").is_file()
    assert (generated / "bind.md").is_file()
    assert (generated / "dependencies.yaml").is_file()
    assert (generated / "roles" / "game-designer.md").is_file()
    assert (generated / "roles" / "frontend-engineer.md").is_file()
    designer_role = (generated / "roles" / "game-designer.md").read_text(encoding="utf-8")
    assert "artifacts/rules.md" in designer_role
    assert "artifacts/state_model.md" in designer_role
    assert "You MUST write each handoff output" in designer_role
    assert "claim_task(status=completed)" in designer_role


@pytest.mark.asyncio
async def test_structured_plan_retries_malformed_model_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed plan JSON should feed the outer repair loop instead of aborting."""
    output_dir = tmp_path / "generated"
    creator_resource_dir = _write_creator_resources(tmp_path / "creator")

    valid_plan = {
        "team_skill_plan": {
            "team_name": "browser-game-team",
            "description": "Create a browser game through design and implementation roles.",
            "roles": [
                {
                    "id": "game-designer",
                    "purpose": "Design playable rules.",
                    "responsibilities": ["Define the turn loop."],
                    "success_criteria": ["The rules produce win and loss states."],
                    "forbidden": ["Do not implement final files."],
                    "mandatory": ["Specify concrete actions."],
                    "output_sections": ["Rules"],
                    "skills": [],
                    "tools": [],
                },
                {
                    "id": "frontend-engineer",
                    "purpose": "Implement browser files.",
                    "responsibilities": ["Build the requested files."],
                    "success_criteria": ["The files run together."],
                    "forbidden": ["Do not leave placeholder handlers."],
                    "mandatory": ["Verify linked files."],
                    "output_sections": ["Files"],
                    "skills": [],
                    "tools": [],
                },
            ],
            "workflow_steps": [
                {
                    "name": "Design game",
                    "executor": "game-designer",
                    "action": "Write the game mechanics.",
                    "output": "Game contract",
                    "quality_gate": "The contract names terminal states.",
                },
                {
                    "name": "Build game",
                    "executor": "frontend-engineer",
                    "action": "Implement the files from the contract.",
                    "output": "Playable files",
                    "quality_gate": "The requested files exist.",
                },
            ],
            "acceptance_criteria": ["The browser game can be played to completion."],
        }
    }

    class FakeResponse:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        def __init__(self) -> None:
            self.calls = 0
            self.user_prompts: list[str] = []

        async def invoke(self, **kwargs: Any) -> FakeResponse:
            self.calls += 1
            messages = kwargs["messages"]
            self.user_prompts.append(str(messages[-1]["content"]))
            if self.calls == 1:
                return FakeResponse('{"team_skill_plan": {"team_name": "bad", "roles": [')
            return FakeResponse(json.dumps(valid_plan))

    fake_model = FakeModel()
    monkeypatch.setattr(
        generator_module,
        "load_member_optimizer_model",
        lambda _: fake_model,
    )

    generator = TeamSkillGenerator(
        model_config_ref="models/glm.yaml",
        creator_resource_dir=creator_resource_dir,
        max_repair_attempts=1,
        validator_runner=lambda path: (True, "ok"),
    )

    generated = Path(await generator.generate("Create a browser game.", output_dir))

    assert generated == (output_dir / "browser-game-team").resolve()
    assert fake_model.calls == 2
    assert "raw_debug_path=" in fake_model.user_prompts[1]
    assert (output_dir / "_artifacts" / "failed_team_skill_plan_attempt_001.raw.txt").is_file()


@pytest.mark.asyncio
async def test_generate_invokes_creator_with_agent_session(tmp_path: Path) -> None:
    """Creator DeepAgent requires an agent Session with get_state support."""
    creator_resource_dir = _write_creator_resources(tmp_path / "creator")
    output_dir = tmp_path / "generated"
    seen_sessions: list[Any] = []

    class FakeCreatorAgent:
        async def invoke(self, inputs: dict[str, str], session: Any) -> dict[str, str]:
            seen_sessions.append(session)
            assert callable(getattr(session, "get_state", None))
            assert "CREATE a Team Skill" in inputs["query"]
            skill_dir = output_dir / "ppt_team"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nkind: team-skill\nname: ppt_team\nroles: []\n---\n",
                encoding="utf-8",
            )
            return {"output": "created"}

    generator = TeamSkillGenerator(
        model_config_ref="models/glm.yaml",
        creator_resource_dir=creator_resource_dir,
        creator_agent_factory=lambda **_: FakeCreatorAgent(),
        validator_runner=lambda path: (True, "ok"),
    )

    generated = await generator.generate("make a PPT", output_dir)

    assert Path(generated) == (output_dir / "ppt_team").resolve()
    assert seen_sessions


@pytest.mark.asyncio
async def test_generate_retries_creator_timeout() -> None:
    """Transient creator-agent timeout should consume a repair attempt, not abort."""
    root = Path(f".tmp_team_skill_generator_retry_{uuid.uuid4().hex}")
    creator_resource_dir = _write_creator_resources(root / "creator")
    output_dir = root / "generated"
    attempts = 0

    class FakeCreatorAgent:
        async def invoke(self, inputs: dict[str, str], session: Any) -> dict[str, str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise asyncio.TimeoutError("team skill creator model request timed out")
            skill_dir = output_dir / "ppt_team"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nkind: team-skill\nname: ppt_team\nroles: []\n---\n",
                encoding="utf-8",
            )
            return {"output": "created"}

    generator = TeamSkillGenerator(
        model_config_ref="models/glm.yaml",
        creator_resource_dir=creator_resource_dir,
        max_repair_attempts=1,
        creator_agent_factory=lambda **_: FakeCreatorAgent(),
        validator_runner=lambda path: (True, "ok"),
    )

    generated = await generator.generate("make a PPT", output_dir)

    assert Path(generated) == (output_dir / "ppt_team").resolve()
    assert attempts == 2


@pytest.mark.asyncio
async def test_generate_uses_fresh_session_for_repair_attempt(tmp_path: Path) -> None:
    creator_resource_dir = _write_creator_resources(tmp_path / "creator")
    output_dir = tmp_path / "generated"
    seen_session_ids: list[str] = []

    class FakeCreatorAgent:
        async def invoke(self, inputs: dict[str, str], session: Any) -> dict[str, str]:
            seen_session_ids.append(session.get_session_id())
            if len(seen_session_ids) == 1:
                return {"output": ""}

            skill_dir = output_dir / "ppt_team"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nkind: team-skill\nname: ppt_team\nroles: []\n---\n",
                encoding="utf-8",
            )
            return {"output": "created"}

    generator = TeamSkillGenerator(
        model_config_ref="models/glm.yaml",
        creator_resource_dir=creator_resource_dir,
        max_repair_attempts=1,
        creator_agent_factory=lambda **_: FakeCreatorAgent(),
        validator_runner=lambda path: (True, "ok"),
    )

    generated = await generator.generate("make a PPT", output_dir)

    assert Path(generated) == (output_dir / "ppt_team").resolve()
    assert len(seen_session_ids) == 2
    assert seen_session_ids[0] != seen_session_ids[1]


def test_creator_agent_can_read_creator_resources_outside_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    creator_resource_dir = _write_creator_resources(tmp_path / "creator")
    output_dir = tmp_path / "generated"
    seen_roots: list[Path] = []

    def fake_build_file_tools_for_workspace(**kwargs: Any) -> tuple[list[Any], Any]:
        seen_roots.extend(Path(root).resolve() for root in kwargs["sandbox_roots"])
        return [], object()

    monkeypatch.setattr(
        generator_module,
        "load_member_optimizer_model",
        lambda _: object(),
    )
    monkeypatch.setattr(
        generator_module,
        "_build_file_tools_for_workspace",
        fake_build_file_tools_for_workspace,
    )
    monkeypatch.setattr(
        generator_module,
        "create_deep_agent",
        lambda **_: object(),
    )

    generator = TeamSkillGenerator(
        model_config_ref="models/glm.yaml",
        creator_resource_dir=creator_resource_dir,
    )

    generator._create_creator_agent(
        workspace=output_dir,
        model_config_ref="models/glm.yaml",
    )

    assert output_dir.resolve() in seen_roots
    assert creator_resource_dir.resolve() in seen_roots


def _write_creator_resources(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text("# creator\n", encoding="utf-8")
    (root / "reference").mkdir()
    (root / "templates").mkdir()
    scripts_dir = root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "validate_swarmskill.py").write_text("print('ok')\n", encoding="utf-8")
    return root
