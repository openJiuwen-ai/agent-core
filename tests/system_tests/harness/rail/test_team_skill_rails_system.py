# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""System-style tests for TeamSkillCreateRail and TeamSkillRail."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    TaskIterationInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail
from openjiuwen.harness.rails import TeamSkillCreateRail, TeamSkillRail
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.sections import SectionName


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
    _registered_rails: list[Any] = field(default_factory=list)
    _pending_rails: list[Any] = field(default_factory=list)
    ability_manager: Any = None
    system_prompt_builder: SystemPromptBuilder = field(
        default_factory=lambda: SystemPromptBuilder(language="cn")
    )


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


def _attach_team_creator_capability(agent: _Agent, skills_dir: Path) -> None:
    creator_dir = skills_dir / "swarmskill-creator"
    creator_dir.mkdir(parents=True, exist_ok=True)
    skill_rail = SkillUseRail(skills_dir=str(skills_dir))
    skill_rail.skills = [
        Skill(
            name="swarmskill-creator",
            description="Create swarm skills",
            directory=creator_dir,
        )
    ]
    agent._registered_rails = [skill_rail]
    agent.ability_manager = SimpleNamespace(
        list_tool_info=AsyncMock(return_value=[SimpleNamespace(name="skill_tool")])
    )


@pytest.mark.asyncio
async def test_team_skill_create_rail_schedules_nudge_after_spawn_threshold(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    rail = TeamSkillCreateRail(skills_dir=str(skills_dir), min_team_members_for_create=2)
    controller = _LoopController()
    agent = _Agent(_loop_controller=controller)
    _attach_team_creator_capability(agent, skills_dir)

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
    assert await rail.notify_team_completed() is True
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
    follow_up_prompt = controller.follow_ups[0]
    assert follow_up_prompt.startswith("<auto_team_skill_creation_followup>\n")
    assert follow_up_prompt.endswith("\n</auto_team_skill_creation_followup>")
    assert "不是用户的新需求" in follow_up_prompt
    assert "常驻提示词中的“团队技能沉淀自检”规则" in follow_up_prompt
    assert "在本条普通回复末尾最多追加两句" in follow_up_prompt
    assert "是否创建 Team/Swarm Skill" in follow_up_prompt
    assert "不要提及自检、沉淀、无需创建、已检查、内部判断或本提醒" in follow_up_prompt
    assert "ask_user" not in follow_up_prompt
    assert "swarmskill-creator" not in follow_up_prompt
    assert "team-skill-creator" not in follow_up_prompt

    await rail.before_model_call(_ctx(agent, ModelCallInputs()))
    section = agent.system_prompt_builder.get_section(SectionName.TEAM_SKILL_CREATION_GUIDANCE)
    assert section is not None
    prompt = section.render("cn")
    assert "swarmskill-creator" in prompt
    assert "普通回复文本" in prompt
    assert not agent.system_prompt_builder.has_section(SectionName.TEAM_SKILL_CREATION_NUDGE)


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
        review_runtime=EvolutionReviewRuntime(),
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
                tool_name="send_message",
                tool_args={"to_member_name": "researcher"},
                tool_result="Error: researcher handoff failed because output format was missing",
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
    await rail.after_invoke(_ctx(agent, InvokeInputs(query="run team skill", conversation_id="team-evolve")))

    events = await rail.drain_pending_approval_events()
    approval_events = [event for event in events if event.type == "chat.ask_user_question"]

    assert len(approval_events) == 1
    request_id = approval_events[0].payload["request_id"]

    await rail.on_approve_record(request_id)

    evo_log = await rail.store.load_full_evolution_log("research-team")
    assert len(evo_log.entries) == 1
    assert evo_log.entries[0].change.section == "Workflow"
    assert "tighten handoff" in evo_log.entries[0].change.content
