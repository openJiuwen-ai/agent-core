# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for SkillExperienceOptimizer team profile."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.experience.types import EvolutionContext
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.optimizer.skill_call import SkillExperienceOptimizer
from openjiuwen.agent_evolving.signal.base import EvolutionTarget, make_evolution_signal
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv


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


def _tool_trajectory() -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map({TRAJECTORY_ID: "exec-1"})},
                    "scopeSpans": [
                        {
                            "scope": {"name": "test"},
                            "spans": [
                                {
                                    "traceId": "1" * 32,
                                    "spanId": "2" * 16,
                                    "name": "tool.send_message",
                                    "attributes": attributes_from_map(
                                        {
                                            semconv.GEN_AI_TOOL_NAME: "send_message",
                                            semconv.GEN_AI_TOOL_INPUT: {"to": "reviewer"},
                                            semconv.GEN_AI_TOOL_OUTPUT: "sent",
                                        }
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ]
        }
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
        trajectory=_tool_trajectory(),
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
  {"action":"append","target":"body","section":"Workflow","summary":"Workflow guidance alpha for team coordination","content":"Workflow alpha detail about reviewer routing after failures.","root_cause":"workflow alpha coordination gap","merge_target":null},
  {"action":"append","target":"body","section":"Collaboration","summary":"Collaboration guidance beta for reviewer handoff","content":"Collaboration beta detail about async handoff timing.","root_cause":"collaboration beta handoff gap","merge_target":null},
  {"action":"append","target":"body","section":"Constraints","summary":"Constraints guidance gamma for timeout policy","content":"Constraints gamma detail about hard review timeouts.","root_cause":"constraints gamma timeout gap","merge_target":null},
  {"action":"append","target":"body","section":"Instructions","summary":"Instructions guidance delta for audit steps","content":"Instructions delta detail about audit checklist ordering.","root_cause":"instructions delta audit gap","merge_target":null},
  {"action":"append","target":"body","section":"Examples","summary":"Examples guidance epsilon for failure replay","content":"Examples epsilon detail about replaying failed tool calls.","root_cause":"examples epsilon replay gap","merge_target":null},
  {"action":"append","target":"body","section":"Troubleshooting","summary":"Troubleshooting guidance zeta for retry policy","content":"Troubleshooting zeta detail about exponential backoff.","root_cause":"troubleshooting zeta retry gap","merge_target":null},
  {"action":"append","target":"script","section":"Scripts","summary":"Cleanup script for orphaned temp files","content":"print('cleanup orphaned temp files after timeout')","root_cause":"script one automation gap","script_filename":"a.py","script_language":"python","script_purpose":"demo"},
  {"action":"append","target":"script","section":"Scripts","summary":"Rollback script for failed deploy artifacts","content":"print('rollback deployment artifacts after failed deploy')","root_cause":"script two automation gap","script_filename":"b.py","script_language":"python","script_purpose":"demo"},
  {"action":"append","target":"script","section":"Scripts","summary":"Log collection script for tool stderr","content":"print('collect stderr logs after tool errors')","root_cause":"script three automation gap","script_filename":"c.py","script_language":"python","script_purpose":"demo"},
  {"action":"append","target":"script","section":"Scripts","summary":"Cache invalidation script for retry storms","content":"print('invalidate cache entries after retry storms')","root_cause":"script four automation gap","script_filename":"d.py","script_language":"python","script_purpose":"demo"}
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

    with patch(
        "openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer.filter_duplicate_drafts",
        side_effect=lambda drafts, existing: list(drafts),
    ), patch(
        "openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer.filter_duplicate_records",
        side_effect=lambda records, existing: list(records),
    ):
        records = await optimizer.generate_records(ctx)

    text_records = [record for record in records if record.change.target != EvolutionTarget.SCRIPT]
    script_records = [record for record in records if record.change.target == EvolutionTarget.SCRIPT]
    assert [record.change.content for record in text_records] == [
        "Workflow alpha detail about reviewer routing after failures.",
        "Collaboration beta detail about async handoff timing.",
        "Constraints gamma detail about hard review timeouts.",
        "Instructions delta detail about audit checklist ordering.",
        "Examples epsilon detail about replaying failed tool calls.",
    ]
    assert [record.change.content for record in script_records] == [
        "print('cleanup orphaned temp files after timeout')",
        "print('rollback deployment artifacts after failed deploy')",
        "print('collect stderr logs after tool errors')",
    ]
