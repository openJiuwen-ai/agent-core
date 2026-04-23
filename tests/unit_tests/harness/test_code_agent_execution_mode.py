# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodeAgent 运行模式切换（Mock）测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import List

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ToolCallInputs
from openjiuwen.harness.subagents.code_agent import create_code_agent

from tests.unit_tests.fixtures.mock_llm import (
    create_text_response,
    create_tool_call_response,
    mock_llm_context,
)


class ToolTraceRail(AgentRail):
    """记录工具调用顺序，供测试断言。"""

    def __init__(self) -> None:
        super().__init__()
        self.tool_calls: List[str] = []

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, ToolCallInputs) and ctx.inputs.tool_name:
            self.tool_calls.append(ctx.inputs.tool_name)


class TestCodeAgentExecutionModeMock(unittest.IsolatedAsyncioTestCase):
    """参考 e2e 场景的 mock 版本单测。"""

    async def asyncSetUp(self) -> None:
        await Runner.start()
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="code_agent_mode_mock_")
        self._work_dir = self._tmp_dir.name
        self._session = create_agent_session(
            session_id=f"code_agent_mode_{uuid.uuid4().hex}",
            card=AgentCard(id="code_agent", name="code_agent", description="code agent test session"),
        )

    async def asyncTearDown(self) -> None:
        self._tmp_dir.cleanup()
        await Runner.stop()

    @staticmethod
    def _create_mock_model() -> Model:
        """创建可被 mock_llm_context 接管的 Model。"""
        return Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="mock-key",
                api_base="http://mock-base",
                verify_ssl=False,
            ),
            model_config=ModelRequestConfig(
                model="mock-model",
                temperature=0.2,
                top_p=0.9,
            ),
        )

    def _create_agent(self, trace: ToolTraceRail):
        return create_code_agent(
            model=self._create_mock_model(),
            rails=[trace],
            enable_task_loop=True,
            max_iterations=12,
            workspace=self._work_dir,
        )

    def _prepare_plan_file(self, agent, content: str = "# 初始计划\n- step 1") -> Path:
        """为 plan 模式准备一个已有计划文件，贴近 e2e 第二轮/第三轮场景。"""
        state = agent.load_state(self._session)
        if not state.plan_mode.plan_slug:
            state.plan_mode.plan_slug = "mock-plan"
            agent.save_state(self._session, state)

        plan_path = agent.get_plan_file_path(self._session)
        assert plan_path is not None
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(content, encoding="utf-8")
        return plan_path

    def _resume_answer(self, interrupt_result: dict, answer: str):
        """复用 ask_user 中断恢复逻辑，避免重复 mock 代码。"""
        self.assertEqual(interrupt_result.get("result_type"), "interrupt")
        interrupt_ids = interrupt_result.get("interrupt_ids", [])
        self.assertEqual(len(interrupt_ids), 1)

        interactive_input = InteractiveInput()
        interactive_input.update(interrupt_ids[0], {"answer": answer})
        return interactive_input

    @pytest.mark.asyncio
    async def test_manual_switch_mode_shows_switching_scene(self) -> None:
        """agent.switch_mode 显式切换场景。"""
        trace = ToolTraceRail()
        agent = self._create_agent(trace)

        origin_state = agent.load_state(self._session)
        self.assertEqual(origin_state.plan_mode.mode, "normal")

        agent.switch_mode(self._session, "plan")
        state = agent.load_state(self._session)
        self.assertEqual(state.plan_mode.mode, "plan")

        self._prepare_plan_file(agent)

        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([create_text_response("当前处于 plan 模式，等待你的下一步指令。")])
            result = await Runner.run_agent(agent, {"query": "当前是什么模式？"}, session=self._session)

        self.assertEqual(result.get("result_type"), "answer")
        self.assertNotIn("switch_mode", trace.tool_calls)

    @pytest.mark.asyncio
    async def test_user_interacts_via_ask_user_in_multi_session(self) -> None:
        """用户可通过 ask_user 交互更新计划。"""
        trace = ToolTraceRail()
        agent = self._create_agent(trace)
        session_1 = self._session
        session_2 = create_agent_session(
            session_id=session_1.get_session_id(),
            card=AgentCard(id="code_agent", name="code_agent", description="code agent test session"),
        )
        agent.switch_mode(session_1, "plan")
        self._prepare_plan_file(agent, content="# 初始计划\n- 城市待确认")

        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_tool_call_response(
                    "ask_user",
                    json.dumps({"question": "你希望展示哪个城市？"}, ensure_ascii=False),
                    tool_call_id="ask_city_1",
                ),
                create_text_response("收到你的反馈，计划已更新为展示上海城市信息。"),
            ])
            first = await Runner.run_agent(
                agent,
                {"query": "继续完善计划，城市你先问我"},
                session=session_1,
            )
            interactive_input = self._resume_answer(first, "上海")
            second = await Runner.run_agent(agent, {"query": interactive_input}, session=session_2)

        self.assertEqual(second.get("result_type"), "answer")
        self.assertIn("ask_user", trace.tool_calls)
        self.assertEqual(agent.load_state(session_2).plan_mode.mode, "plan")

    @pytest.mark.asyncio
    async def test_user_interacts_via_ask_user_to_update_plan(self) -> None:
        """用户可通过 ask_user 交互更新计划。"""
        trace = ToolTraceRail()
        agent = self._create_agent(trace)
        agent.switch_mode(self._session, "plan")
        self._prepare_plan_file(agent, content="# 初始计划\n- 城市待确认")

        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_tool_call_response(
                    "ask_user",
                    json.dumps({"question": "你希望展示哪个城市？"}, ensure_ascii=False),
                    tool_call_id="ask_city_1",
                ),
                create_text_response("收到你的反馈，计划已更新为展示上海城市信息。"),
            ])
            first = await Runner.run_agent(
                agent,
                {"query": "继续完善计划，城市你先问我"},
                session=self._session,
            )
            interactive_input = self._resume_answer(first, "上海")
            second = await Runner.run_agent(agent, {"query": interactive_input}, session=self._session)

        self.assertEqual(second.get("result_type"), "answer")
        self.assertIn("ask_user", trace.tool_calls)
        self.assertEqual(agent.load_state(self._session).plan_mode.mode, "plan")

    @pytest.mark.asyncio
    async def test_user_can_invoke_again_to_update_plan(self) -> None:
        """用户在 ask_user 交互后可再次 invoke 更新计划。"""
        trace = ToolTraceRail()
        agent = self._create_agent(trace)
        agent.switch_mode(self._session, "plan")
        plan_path = self._prepare_plan_file(agent, content="# 计划\n- 先确认城市")

        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_tool_call_response(
                    "ask_user",
                    json.dumps({"question": "是否需要天气模块？"}, ensure_ascii=False),
                    tool_call_id="ask_feature_1",
                ),
                create_text_response("已记录：需要天气模块。"),
                create_tool_call_response(
                    "read_file",
                    json.dumps({"file_path": str(plan_path)}, ensure_ascii=False),
                ),
                create_tool_call_response(
                    "write_file",
                    json.dumps(
                        {
                            "file_path": str(plan_path),
                            "content": "# 更新后计划\n- 展示北京\n- 增加天气模块",
                        },
                        ensure_ascii=False,
                    ),
                ),
                create_text_response("计划二次更新完成。"),
            ])

            first = await Runner.run_agent(agent, {"query": "先问我一个问题再继续"}, session=self._session)
            interactive_input = self._resume_answer(first, "需要")
            resumed_answer = await Runner.run_agent(agent, {"query": interactive_input}, session=self._session)
            self.assertEqual(resumed_answer.get("result_type"), "answer")

            second_invoke = await Runner.run_agent(agent, {"query": "继续更新计划并写入"}, session=self._session)

        self.assertEqual(second_invoke.get("result_type"), "answer")
        self.assertIn("ask_user", trace.tool_calls)
        self.assertIn("write_file", trace.tool_calls)
        self.assertIn("天气模块", plan_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
