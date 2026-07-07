# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for SkillExperienceOptimizer team profile."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.experience.types import EvolutionContext
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer
from openjiuwen.agent_evolving.signal.base import EvolutionTarget, make_evolution_signal
from openjiuwen.agent_evolving.trajectory.types import ToolCallDetail, LegacyTrajectory, TrajectoryStep


def _make_signal(signal_type: str = "execution_failure"):
    return make_evolution_signal(
        signal_type=signal_type,
        section="Troubleshooting" if signal_type == "execution_failure" else "Scripts",
        excerpt="tool failed" if signal_type == "execution_failure" else "print('ok')",
        skill_name="team-a",
        source="passive_conversation",
    )


def _make_record(record_id: str, *, target: EvolutionTarget = EvolutionTarget.BODY) -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source="execution_failure",
        timestamp="2026-01-01T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting" if target != EvolutionTarget.SCRIPT else "Scripts",
            action="append",
            content="existing record",
            target=target,
        ),
        applied=False,
    )


def test_team_profile_and_record_policy_properties() -> None:
    policy = LLMInvokePolicy(attempt_timeout_secs=12, total_budget_secs=24, max_attempts=1)
    optimizer = SkillExperienceOptimizer(
        llm=MagicMock(),
        model="dummy",
        language="en",
        generate_records_llm_policy=policy,
        profile="team",
    )

    assert optimizer.profile == "team"
    assert optimizer.record_llm_policy is policy
    assert optimizer.generate_records_llm_policy is policy


def test_invalid_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="profile"):
        SkillExperienceOptimizer(llm=MagicMock(), model="dummy", profile="unknown")


@pytest.mark.asyncio
async def test_team_profile_prompt_contains_trajectory_sections_and_existing_scripts() -> None:
    llm = MagicMock()
    llm.invoke = AsyncMock(
        return_value=SimpleNamespace(
            content=(
                '[{"action":"append","target":"body","section":"Workflow",'
                '"summary":"Coordinate reviewer handoff after tool failures.",'
                '"content":"### Reviewer handoff\\n- Send failure context before retrying.",'
                '"merge_target":null}]'
            )
        )
    )
    optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="en", profile="team")
    ctx = EvolutionContext(
        skill_name="team-a",
        signals=[_make_signal()],
        skill_content="# Team A\n\n## Workflow\n- Existing flow",
        messages=[],
        existing_desc_records=[_make_record("ev_desc", target=EvolutionTarget.DESCRIPTION)],
        existing_body_records=[_make_record("ev_body")],
        existing_script_records=[_make_record("ev_script", target=EvolutionTarget.SCRIPT)],
        trajectory=LegacyTrajectory(
            execution_id="exec-1",
            steps=[
                TrajectoryStep(
                    kind="tool",
                    detail=ToolCallDetail(
                        tool_name="send_message",
                        call_args={"to": "reviewer"},
                        call_result="sent",
                    ),
                )
            ]
        ),
    )

    records = await optimizer.generate_records(ctx)

    prompt = llm.invoke.await_args_list[0].kwargs["messages"][0]["content"]
    assert "Team Skill optimization expert" in prompt
    assert "## Trajectory Summary" in prompt
    assert "[Tool:send_message]" in prompt
    assert "Existing script experiences" in prompt
    assert "[ev_script]" in prompt
    assert "Roles | Collaboration | Workflow | Constraints | Instructions | Examples | Troubleshooting | Scripts" in prompt
    assert len(records) == 1
    assert records[0].change.section == "Workflow"
    assert records[0].summary == "Coordinate reviewer handoff after tool failures."


@pytest.mark.asyncio
async def test_team_profile_limits_text_and_script_records() -> None:
    llm = MagicMock()
    llm.invoke = AsyncMock(
        return_value=SimpleNamespace(
            content="""
[
  {"action":"append","target":"body","section":"Workflow","summary":"A","content":"A","merge_target":null},
  {"action":"append","target":"body","section":"Collaboration","summary":"B","content":"B","merge_target":null},
  {"action":"append","target":"body","section":"Constraints","summary":"C","content":"C","merge_target":null},
  {"action":"append","target":"script","section":"Scripts","summary":"S1","content":"print(1)","script_filename":"a.py","script_language":"python","script_purpose":"demo"},
  {"action":"append","target":"script","section":"Scripts","summary":"S2","content":"print(2)","script_filename":"b.py","script_language":"python","script_purpose":"demo"}
]
"""
        )
    )
    optimizer = SkillExperienceOptimizer(llm=llm, model="dummy", language="en", profile="team")
    ctx = EvolutionContext(
        skill_name="team-a",
        signals=[_make_signal(), _make_signal("script_artifact")],
        skill_content="# Team A",
        messages=[],
        existing_desc_records=[],
        existing_body_records=[],
        existing_script_records=[],
    )

    records = await optimizer.generate_records(ctx)

    text_records = [record for record in records if record.change.target != EvolutionTarget.SCRIPT]
    script_records = [record for record in records if record.change.target == EvolutionTarget.SCRIPT]
    assert [record.change.content for record in text_records] == ["A", "B"]
    assert [record.change.content for record in script_records] == ["print(1)"]
