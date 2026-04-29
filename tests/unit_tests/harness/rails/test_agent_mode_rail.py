# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from openjiuwen.harness.rails import AgentModeRail
from openjiuwen.harness.schema.state import DeepAgentState


class _ToolInfo:
    def __init__(self, name: str):
        self.name = name


class _PromptBuilder:
    def __init__(self) -> None:
        self.language = "en"
        self.added_sections = []
        self.removed_sections = []

    def add_section(self, section) -> None:
        self.added_sections.append(section)

    def remove_section(self, section_name) -> None:
        self.removed_sections.append(section_name)


def _make_ctx(tool_name: str, *, mode: str = "plan", tool_args=None, tools=None):
    state = DeepAgentState()
    state.plan_mode.mode = mode

    agent = Mock()
    agent.load_state.return_value = state
    agent.get_plan_file_path.return_value = Path("/tmp/.plans/mock-plan.md")
    agent.deep_config = SimpleNamespace(subagents=[SimpleNamespace()])
    agent.ability_manager = Mock()

    inputs = SimpleNamespace(
        tool_name=tool_name,
        tool_args=tool_args if tool_args is not None else {},
        tool_call=SimpleNamespace(id="tc_1"),
        tool_result=None,
        tool_msg=None,
        tools=tools if tools is not None else [],
    )

    ctx = SimpleNamespace(
        session=SimpleNamespace(),
        inputs=inputs,
        extra={},
    )

    rail = AgentModeRail()
    rail._agent = agent
    rail.system_prompt_builder = _PromptBuilder()
    return rail, ctx, agent


class TestAgentModeRail(IsolatedAsyncioTestCase):
    async def test_before_tool_call_passes_through_when_not_plan_mode(self) -> None:
        rail, ctx, _ = _make_ctx("some_random_tool", mode="auto")

        await rail.before_tool_call(ctx)

        self.assertIsNone(ctx.extra.get("_skip_tool"))
        self.assertIsNone(ctx.inputs.tool_result)

    async def test_before_tool_call_rejects_hidden_todo_or_session_tools_in_plan_mode(self) -> None:
        rail, ctx, _ = _make_ctx("todo_create", mode="plan")

        await rail.before_tool_call(ctx)

        self.assertTrue(ctx.extra.get("_skip_tool"))
        self.assertIn("hidden in plan mode", ctx.inputs.tool_result["error"])

    async def test_before_tool_call_rejects_non_whitelist_tool_in_plan_mode(self) -> None:
        rail, ctx, _ = _make_ctx("non_whitelist_tool", mode="plan")

        await rail.before_tool_call(ctx)

        self.assertTrue(ctx.extra.get("_skip_tool"))
        self.assertIn("not available in plan mode", ctx.inputs.tool_result["error"])

    async def test_before_tool_call_write_or_edit_only_plan_file(self) -> None:
        rail, ctx_bad, _ = _make_ctx(
            "write_file",
            mode="plan",
            tool_args={"file_path": "/tmp/not-plan.md", "content": "x"},
        )

        await rail.before_tool_call(ctx_bad)

        self.assertTrue(ctx_bad.extra.get("_skip_tool"))
        self.assertIn("can only target the plan file", ctx_bad.inputs.tool_result["error"])

        rail2, ctx_ok, _ = _make_ctx(
            "edit_file",
            mode="plan",
            tool_args={"file_path": "/tmp/.plans/mock-plan.md", "old_string": "a", "new_string": "b"},
        )

        await rail2.before_tool_call(ctx_ok)

        self.assertIsNone(ctx_ok.extra.get("_skip_tool"))
        self.assertIsNone(ctx_ok.inputs.tool_result)

    async def test_enter_exit_plan_mode_tools_are_only_allowed_in_plan_mode(self) -> None:
        rail1, enter_ctx, _ = _make_ctx("enter_plan_mode", mode="auto")
        await rail1.before_tool_call(enter_ctx)
        self.assertTrue(enter_ctx.extra.get("_skip_tool"))
        self.assertIn("enter_plan_mode can only be called in plan mode", enter_ctx.inputs.tool_result["error"])

        rail2, exit_ctx, _ = _make_ctx("exit_plan_mode", mode="auto")
        await rail2.before_tool_call(exit_ctx)
        self.assertTrue(exit_ctx.extra.get("_skip_tool"))
        self.assertIn("exit_plan_mode can only be called in plan mode", exit_ctx.inputs.tool_result["error"])

    async def test_before_model_call_filters_hidden_tools_and_injects_mode_section(self) -> None:
        tools = [_ToolInfo("todo_create"), _ToolInfo("sessions_spawn"), _ToolInfo("read_file")]
        rail, ctx, _ = _make_ctx("noop", mode="plan", tools=tools)

        with patch("openjiuwen.harness.rails.agent_mode_rail.build_plan_mode_section", return_value="MODE_SECTION"):
            await rail.before_model_call(ctx)

        visible_tool_names = [t.name for t in ctx.inputs.tools]
        self.assertNotIn("todo_create", visible_tool_names)
        self.assertNotIn("sessions_spawn", visible_tool_names)
        self.assertIn("read_file", visible_tool_names)

        self.assertIn("MODE_SECTION", rail.system_prompt_builder.added_sections)

    async def test_before_model_call_in_auto_mode_removes_mode_section(self) -> None:
        rail, ctx, _ = _make_ctx("noop", mode="auto", tools=[_ToolInfo("read_file")])

        await rail.before_model_call(ctx)

        self.assertGreaterEqual(len(rail.system_prompt_builder.removed_sections), 1)

    async def test_after_tool_call_register_unregister_task_tool_and_respect_skip(self) -> None:
        rail, ctx_enter, agent = _make_ctx("enter_plan_mode", mode="plan")
        with patch.object(rail, "_register_task_tool") as mock_register, patch.object(rail, "_unregister_task_tool") as mock_unregister:
            await rail.after_tool_call(ctx_enter)
            mock_register.assert_called_once_with(agent)
            mock_unregister.assert_not_called()

        rail2, ctx_exit, agent2 = _make_ctx("exit_plan_mode", mode="plan")
        with patch.object(rail2, "_register_task_tool") as mock_register2, patch.object(rail2, "_unregister_task_tool") as mock_unregister2:
            await rail2.after_tool_call(ctx_exit)
            mock_unregister2.assert_called_once_with(agent2)
            mock_register2.assert_not_called()

        rail3, ctx_skip, _ = _make_ctx("enter_plan_mode", mode="plan")
        ctx_skip.extra["_skip_tool"] = True
        with patch.object(rail3, "_register_task_tool") as mock_register3:
            await rail3.after_tool_call(ctx_skip)
            mock_register3.assert_not_called()
