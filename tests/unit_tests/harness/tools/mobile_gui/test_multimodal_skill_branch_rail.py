# coding: utf-8
"""Tests for MultimodalSkillBranchRail."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_branch_rail import (
    MultimodalSkillBranchRail,
)
from openjiuwen.harness.tools.mobile_gui.skill_branch.runner import BranchResult


@pytest.mark.asyncio
async def test_branch_rail_rewrites_tool_message_with_planner_fields(tmp_path):
    """Successful branch consult replaces raw skill_tool body with structured planner memo."""
    settings = MobileGuiRuntimeSettings(
        skill_consult_mode="branch",
        skill_branch_max_images=2,
        skill_branch_max_consults_per_skill=2,
    )
    rail = MultimodalSkillBranchRail(settings)

    skill_dir = tmp_path / "skills" / "demo"
    img_dir = skill_dir / "images"
    img_dir.mkdir(parents=True)
    (img_dir / "step.png").write_bytes(b"x")

    tool_result = ToolOutput(
        success=True,
        data={
            "skill_directory": str(skill_dir),
            "skill_content": "Do task.\n\n![Step](images/step.png)\n",
        },
    )
    tool_msg = ToolMessage(tool_call_id="tc1", content="original")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="skill_tool",
            tool_args={"skill_name": "demo"},
            tool_result=tool_result,
            tool_msg=tool_msg,
        ),
    )
    ctx.extra["pinned_user_goal"] = "Find the repo"
    ctx.extra["vlm_grounding_base64"] = "abc123"

    planner = {
        "skill_applicability": "effective",
        "subgoal": "open page",
        "plan": "Tap search.",
        "do_not_do": "Do not scroll blindly.",
        "fallback_if_no_progress": "Go back and retry.",
        "expected_state": "Search visible.",
        "completion_scope": "local_only",
    }
    branch_result = BranchResult(success=True, planner=planner, selected_image_ids=["step"])

    mock_model = MagicMock()
    with patch(
        "openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_branch_rail.resolve_branch_model",
        return_value=mock_model,
    ):
        with patch(
            "openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_branch_rail.run_skill_branch",
            new=AsyncMock(return_value=branch_result),
        ) as run_branch:
            await rail.after_tool_call(ctx)

    run_branch.assert_awaited_once()
    assert "Skill consult: demo" in tool_msg.content
    assert "Applicability: effective" in tool_msg.content
    assert "Subgoal: open page" in tool_msg.content
    assert "Plan: Tap search." in tool_msg.content
    assert "Do not do: Do not scroll blindly." in tool_msg.content
    assert "Completion scope: local_only" in tool_msg.content
    assert "original" not in tool_msg.content


@pytest.mark.asyncio
async def test_branch_rail_noop_in_inline_mode():
    """Inline consult mode leaves skill_tool ToolMessage content untouched."""
    settings = MobileGuiRuntimeSettings(skill_consult_mode="inline")
    rail = MultimodalSkillBranchRail(settings)
    tool_msg = ToolMessage(tool_call_id="tc1", content="keep")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="skill_tool",
            tool_result=ToolOutput(success=True, data={}),
            tool_msg=tool_msg,
        ),
    )

    await rail.after_tool_call(ctx)

    assert tool_msg.content == "keep"


@pytest.mark.asyncio
async def test_branch_rail_ignores_non_skill_tool():
    settings = MobileGuiRuntimeSettings(skill_consult_mode="branch")
    rail = MultimodalSkillBranchRail(settings)
    tool_msg = ToolMessage(tool_call_id="tc1", content="unchanged")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="read_file",
            tool_result=ToolOutput(success=True, data={}),
            tool_msg=tool_msg,
        ),
    )
    await rail.after_tool_call(ctx)
    assert tool_msg.content == "unchanged"


@pytest.mark.asyncio
async def test_branch_rail_ignores_failed_skill_tool():
    settings = MobileGuiRuntimeSettings(skill_consult_mode="branch")
    rail = MultimodalSkillBranchRail(settings)
    tool_msg = ToolMessage(tool_call_id="tc1", content="failed")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="skill_tool",
            tool_result=ToolOutput(success=False, data={}),
            tool_msg=tool_msg,
        ),
    )
    await rail.after_tool_call(ctx)
    assert tool_msg.content == "failed"


@pytest.mark.asyncio
async def test_branch_rail_skips_when_skill_has_no_local_images(tmp_path):
    """No ``![](local)`` manifest entries → branch does not rewrite the tool message."""
    settings = MobileGuiRuntimeSettings(skill_consult_mode="branch")
    rail = MultimodalSkillBranchRail(settings)
    skill_dir = tmp_path / "skills" / "text-only"
    skill_dir.mkdir(parents=True)

    tool_msg = ToolMessage(tool_call_id="tc1", content="raw skill body")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="skill_tool",
            tool_args={"skill_name": "text-only"},
            tool_result=ToolOutput(
                success=True,
                data={"skill_directory": str(skill_dir), "skill_content": "# No images\n"},
            ),
            tool_msg=tool_msg,
        ),
    )
    await rail.after_tool_call(ctx)
    assert tool_msg.content == "raw skill body"


@pytest.mark.asyncio
async def test_branch_rail_enforces_per_skill_consult_limit(tmp_path):
    settings = MobileGuiRuntimeSettings(
        skill_consult_mode="branch",
        skill_branch_max_consults_per_skill=1,
    )
    rail = MultimodalSkillBranchRail(settings)
    skill_dir = tmp_path / "skills" / "demo"
    (skill_dir / "images").mkdir(parents=True)
    (skill_dir / "images" / "a.png").write_bytes(b"x")

    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="skill_tool",
            tool_args={"skill_name": "demo"},
            tool_result=ToolOutput(
                success=True,
                data={
                    "skill_directory": str(skill_dir),
                    "skill_content": "![A](images/a.png)",
                },
            ),
            tool_msg=ToolMessage(tool_call_id="tc1", content="original"),
        ),
    )
    ctx.extra["skill_branch_consult_counts"] = {"demo": 1}

    await rail.after_tool_call(ctx)

    assert "Consult limit reached" in ctx.inputs.tool_msg.content
    assert "original" not in ctx.inputs.tool_msg.content


@pytest.mark.asyncio
async def test_branch_rail_writes_failure_message_when_branch_fails(tmp_path):
    settings = MobileGuiRuntimeSettings(skill_consult_mode="branch")
    rail = MultimodalSkillBranchRail(settings)
    skill_dir = tmp_path / "skills" / "demo"
    (skill_dir / "images").mkdir(parents=True)
    (skill_dir / "images" / "a.png").write_bytes(b"x")

    tool_msg = ToolMessage(tool_call_id="tc1", content="original")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="skill_tool",
            tool_args={"skill_name": "demo"},
            tool_result=ToolOutput(
                success=True,
                data={
                    "skill_directory": str(skill_dir),
                    "skill_content": "![A](images/a.png)",
                },
            ),
            tool_msg=tool_msg,
        ),
    )
    ctx.extra["pinned_user_goal"] = "goal"
    ctx.extra["vlm_grounding_base64"] = "b64"

    with patch(
        "openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_branch_rail.resolve_branch_model",
        return_value=MagicMock(),
    ):
        with patch(
            "openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_branch_rail.run_skill_branch",
            new=AsyncMock(return_value=BranchResult(success=False, error="Stage 2 parse failed")),
        ):
            await rail.after_tool_call(ctx)

    assert "Branch consult failed: Stage 2 parse failed" in tool_msg.content
    assert "original" not in tool_msg.content


@pytest.mark.asyncio
async def test_branch_rail_noop_when_model_unavailable(tmp_path):
    settings = MobileGuiRuntimeSettings(skill_consult_mode="branch")
    rail = MultimodalSkillBranchRail(settings)
    skill_dir = tmp_path / "skills" / "demo"
    (skill_dir / "images").mkdir(parents=True)
    (skill_dir / "images" / "a.png").write_bytes(b"x")

    tool_msg = ToolMessage(tool_call_id="tc1", content="keep")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_name="skill_tool",
            tool_args={"skill_name": "demo"},
            tool_result=ToolOutput(
                success=True,
                data={
                    "skill_directory": str(skill_dir),
                    "skill_content": "![A](images/a.png)",
                },
            ),
            tool_msg=tool_msg,
        ),
    )

    with patch(
        "openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_branch_rail.resolve_branch_model",
        return_value=None,
    ):
        await rail.after_tool_call(ctx)

    assert tool_msg.content == "keep"
