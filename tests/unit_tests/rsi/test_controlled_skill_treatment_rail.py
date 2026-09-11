# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for candidate-only controlled Skill delivery."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.rsi.harness_rsi.evaluator.controlled_skill_treatment_rail import (
    ControlledSkillTreatmentRail,
)


def _context(inputs):
    return AgentCallbackContext(agent=SimpleNamespace(), inputs=inputs)


@pytest.mark.asyncio
async def test_treatment_exposes_only_skill_tool_until_exact_skill_completes() -> None:
    builder = SystemPromptBuilder(language="en")
    rail = ControlledSkillTreatmentRail("enum_contract_verify")
    rail.init(SimpleNamespace(system_prompt_builder=builder))

    model_ctx = _context(
        ModelCallInputs(
            tools=[
                SimpleNamespace(name="bash"),
                SimpleNamespace(name="skill_tool"),
                {"function": {"name": "write_file"}},
            ]
        )
    )
    await rail.before_model_call(model_ctx)

    assert [tool.name for tool in model_ctx.inputs.tools] == ["skill_tool"]
    assert builder.has_section(rail._SECTION_NAME)
    assert "enum_contract_verify" in builder.build()

    tool_ctx = _context(
        ToolCallInputs(
            tool_call=SimpleNamespace(id="call_1"),
            tool_name="skill_tool",
            tool_args={"skill_name": "wrong_skill"},
        )
    )
    await rail.before_tool_call(tool_ctx)
    assert tool_ctx.inputs.tool_args == {"skill_name": "enum_contract_verify"}

    tool_ctx.inputs.tool_result = {"success": True, "content": "loaded"}
    await rail.after_tool_call(tool_ctx)

    assert rail.evidence()["delivered"] is True
    assert rail.evidence()["rewritten_skill_names"] == ["wrong_skill"]
    assert not builder.has_section(rail._SECTION_NAME)

    restored_ctx = _context(
        ModelCallInputs(
            tools=[
                SimpleNamespace(name="bash"),
                SimpleNamespace(name="skill_tool"),
            ]
        )
    )
    await rail.before_model_call(restored_ctx)
    assert [tool.name for tool in restored_ctx.inputs.tools] == [
        "bash",
        "skill_tool",
    ]


@pytest.mark.asyncio
async def test_treatment_blocks_non_skill_tool_before_delivery() -> None:
    rail = ControlledSkillTreatmentRail("enum_contract_verify")
    tool_ctx = _context(
        ToolCallInputs(
            tool_call=SimpleNamespace(id="call_1"),
            tool_name="bash",
            tool_args={"command": "sed -n '1,80p' source.py"},
        )
    )

    await rail.before_tool_call(tool_ctx)

    assert tool_ctx.extra["_skip_tool"] is True
    assert "requires skill_tool" in tool_ctx.inputs.tool_result["error"]
    assert rail.evidence()["blocked_tool_names"] == ["bash"]
    assert rail.evidence()["delivered"] is False


@pytest.mark.asyncio
async def test_failed_skill_tool_output_is_not_counted_as_delivery() -> None:
    rail = ControlledSkillTreatmentRail("enum_contract_verify")
    tool_ctx = _context(
        ToolCallInputs(
            tool_call=SimpleNamespace(id="call_1"),
            tool_name="skill_tool",
            tool_args={"skill_name": "enum_contract_verify"},
            tool_result=SimpleNamespace(success=False, error="Skill not found"),
        )
    )

    await rail.after_tool_call(tool_ctx)

    assert rail.evidence()["delivered"] is False
