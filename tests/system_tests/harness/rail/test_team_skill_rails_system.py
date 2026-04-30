# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System-style tests for TeamSkillCreateRail and TeamSkillRail."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from examples.agent_evolving import (
    team_skill_create_rail_example,
    team_skill_rail_example,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    TaskIterationInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillRail


@dataclass
class _MockResponse:
    content: str


class _MockLLM:
    async def invoke(self, messages: list[dict[str, Any]], model: str = "", **_: Any) -> _MockResponse:
        prompt = messages[0]["content"] if messages else ""
        if "如果存在不足，输出 JSON 数组" in prompt:
            return _MockResponse(
                '[{"issue_type":"coordination","description":"handoff is too loose","severity":"medium"}]'
            )
        return _MockResponse(
            """```json
{
  "need_patch": true,
  "section": "Workflow",
  "content": "### Experience: tighten handoff\\nRequire the leader to restate output format before merge.",
  "reason": "handoff quality drifted during collaboration"
}
```"""
        )


@dataclass
class _LoopController:
    follow_ups: list[str] = field(default_factory=list)

    def enqueue_follow_up(self, prompt: str) -> None:
        self.follow_ups.append(prompt)


@dataclass
class _Card:
    id: str = "team-leader"


@dataclass
class _Agent:
    card: _Card = field(default_factory=_Card)
    _loop_controller: _LoopController | None = None


def _ctx(agent: _Agent, inputs: Any) -> AgentCallbackContext:
    return AgentCallbackContext(agent=agent, inputs=inputs)


def _write_team_skill(skills_dir: Path, skill_name: str = "research-team") -> Path:
    skill_dir = skills_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: simple research team\n"
        "kind: team-skill\n"
        "---\n"
        "# Workflow\n"
        "1. leader assigns tasks\n"
        "2. members execute\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.mark.asyncio
async def test_team_skill_create_rail_queues_follow_up_after_spawn_threshold(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    rail = TeamSkillCreateRail(skills_dir=str(skills_dir), min_team_members_for_create=2)
    controller = _LoopController()
    agent = _Agent(_loop_controller=controller)

    await rail.before_invoke(_ctx(agent, InvokeInputs(query="build a team", conversation_id="team-create")))
    for idx in range(2):
        await rail.after_tool_call(
            _ctx(
                agent,
                ToolCallInputs(
                    tool_name="spawn_member",
                    tool_args={"name": f"worker-{idx}"},
                    tool_result={"status": "spawned"},
                ),
            )
        )
    await rail.after_task_iteration(
        _ctx(
            agent,
            TaskIterationInputs(
                iteration=1,
                loop_event=None,
                conversation_id="team-create",
                query="build a team",
            ),
        )
    )

    assert len(controller.follow_ups) == 1
    assert "ask_user" in controller.follow_ups[0]
    assert "team-skill-creator" in controller.follow_ups[0]


@pytest.mark.asyncio
async def test_team_skill_rail_generates_and_persists_patch_after_completion(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_dir = _write_team_skill(skills_dir)

    rail = TeamSkillRail(
        skills_dir=str(skills_dir),
        llm=_MockLLM(),
        model="mock-model",
        auto_save=False,
        async_evolution=False,
    )
    agent = _Agent()

    await rail.before_invoke(_ctx(agent, InvokeInputs(query="run team skill", conversation_id="team-evolve")))
    await rail.after_tool_call(
        _ctx(
            agent,
            ToolCallInputs(
                tool_name="read_file",
                tool_args=str(skill_dir / "SKILL.md"),
                tool_result="loaded",
            ),
        )
    )
    await rail.after_tool_call(
        _ctx(
            agent,
            ToolCallInputs(
                tool_name="spawn_member",
                tool_args={"name": "researcher"},
                tool_result={"status": "spawned"},
            ),
        )
    )
    await rail.after_tool_call(
        _ctx(
            agent,
            ToolCallInputs(
                tool_name="view_task",
                tool_args={},
                tool_result="task-a completed\ntask-b completed",
            ),
        )
    )

    events = await rail.drain_pending_approval_events()
    approval_events = [event for event in events if event.type == "chat.ask_user_question"]

    assert len(approval_events) == 1
    request_id = approval_events[0].payload["request_id"]

    await rail.on_approve_patch(request_id)

    evo_log = await rail.store.load_full_evolution_log("research-team")
    assert len(evo_log.entries) == 1
    assert evo_log.entries[0].change.section == "Workflow"
    assert "tighten handoff" in evo_log.entries[0].change.content


@pytest.mark.parametrize(
    "loader",
    [
        team_skill_create_rail_example.load_env_if_present,
        team_skill_rail_example.load_env_if_present,
    ],
)
def test_examples_can_load_model_env_from_local_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loader,
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MODEL_NAME=deepseek-chat",
                "API_KEY=test-key",
                "MODEL_PROVIDER=OpenAI",
                "API_BASE=https://example.test/v1",
            ]
        ),
        encoding="utf-8",
    )

    for key in ("MODEL_NAME", "API_KEY", "MODEL_PROVIDER", "API_BASE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    loader()

    assert os.getenv("MODEL_NAME") == "deepseek-chat"
    assert os.getenv("API_KEY") == "test-key"
    assert os.getenv("MODEL_PROVIDER") == "OpenAI"
    assert os.getenv("API_BASE") == "https://example.test/v1"
