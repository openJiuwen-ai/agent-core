# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CodeAgent 运行模式切换测试"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import List

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.harness.subagents.code_agent import create_code_agent

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", "120"))
os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")


class ToolTraceRail(AgentRail):
    """记录工具调用顺序，供测试断言。"""

    def __init__(self) -> None:
        super().__init__()
        self.tool_calls: List[str] = []

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, ToolCallInputs) and ctx.inputs.tool_name:
            self.tool_calls.append(ctx.inputs.tool_name)


class TestDeepAgentExecuteModeE2E(unittest.IsolatedAsyncioTestCase):
    """Plan 模式切换与执行模式接续端到端测试。"""

    async def asyncSetUp(self) -> None:
        await Runner.start()
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="deepagent_plan_mode_e2e_")
        self._work_dir = self._tmp_dir.name
        self._session = create_agent_session(
            session_id=f"deepagent_plan_mode_{uuid.uuid4().hex}",
            card=AgentCard(id="code_agent", name="code_agent", description="code agent test session"),
        )


    async def asyncTearDown(self) -> None:
        self._tmp_dir.cleanup()
        await Runner.stop()

    @staticmethod
    def _create_real_model() -> Model:
        model_client_config = ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            timeout=MODEL_TIMEOUT,
            verify_ssl=False,
        )
        model_request_config = ModelRequestConfig(
            model=MODEL_NAME,
            temperature=0.2,
            top_p=0.9,
        )
        return Model(
            model_client_config=model_client_config,
            model_config=model_request_config,
        )

    def _require_llm_config(self) -> None:
        if not API_KEY or not API_BASE or not MODEL_NAME or not MODEL_PROVIDER:
            self.fail(
                "Real-model E2E requires API_KEY/API_BASE/MODEL_NAME/"
                "MODEL_PROVIDER in environment."
            )

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_plan_mode_with_real_model_end_to_end(self) -> None:
        """真实模型用例：首次写入 plan，二次主动切换auto模式，按照plan执行。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        runtime_model = self._create_real_model()
        agent = create_code_agent(
            model=runtime_model,
            system_prompt=(
                "你是一个 AI 编程助手，当用户要求你写代码、创建文件、修改文件时，你**必须**调用相应的工具，**绝对不能**直接在回复中输出代码。"
                "你必须严格按照plan模式的工作流执行"
            ),
            rails=[trace],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
        )

        agent.switch_mode(self._session, "plan")
        first = await Runner.run_agent(agent, 
            {
                "query": "帮我规划一个简单的模块开发任务，创建一个动态展示城市信息的网页。记得要使用explore agent！"
                         "所有问题你自己决定即可！"
            },
            session=self._session,
        )
        self.assertEqual(first.get("result_type"), "answer")

        state_after_first = agent.load_state(self._session)
        plan_path = agent.get_plan_file_path(self._session)
        self.assertEqual(state_after_first.plan_mode.mode, "plan")
        self.assertTrue(bool(state_after_first.plan_mode.plan_slug))
        self.assertIsNotNone(plan_path)
        assert plan_path is not None
        self.assertTrue(plan_path.exists())
        self.assertTrue(bool(plan_path.read_text(encoding="utf-8").strip()))
        first_call_count = len(trace.tool_calls)

        agent.switch_mode(self._session, "normal")
        second = await Runner.run_agent(agent, 
            {
                "query": "按照计划执行把"
            },
            session=self._session,
        )
        self.assertEqual(second.get("result_type"), "answer")

        second_call_slice = trace.tool_calls[first_call_count:]
        self.assertNotIn("enter_plan_mode", second_call_slice)
        self.assertIn("todo_create", second_call_slice)
        state_after_second = agent.load_state(self._session)
        self.assertEqual(state_after_second.plan_mode.mode, "normal")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_plan_mode_with_real_model_end_to_end_modify_plan(self) -> None:
        """真实模型用例：首次写入 plan，二次不切换模式修正plan，三次切换模式根据修正后的plan执行。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        runtime_model = self._create_real_model()
        agent = create_code_agent(
            model=runtime_model,
            system_prompt=(
                "你是一个 AI 编程助手，当用户要求你写代码、创建文件、修改文件时，你**必须**调用相应的工具，**绝对不能**直接在回复中输出代码。"
            ),
            rails=[trace],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
        )

        agent.switch_mode(self._session, "plan")
        first = await Runner.run_agent(agent, 
            {
                "query": "帮我规划一个简单的模块开发任务，创建一个动态展示城市信息的网页。"
                         "所有问题你自己决定即可！"
            },
            session=self._session,
        )
        self.assertEqual(first.get("result_type"), "answer")

        state_after_first = agent.load_state(self._session)
        plan_path = agent.get_plan_file_path(self._session)
        slug_after_first = state_after_first.plan_mode.plan_slug
        self.assertEqual(state_after_first.plan_mode.mode, "plan")
        self.assertTrue(bool(slug_after_first))
        self.assertIsNotNone(plan_path)
        assert plan_path is not None
        self.assertTrue(plan_path.exists())
        content_after_first = plan_path.read_text(encoding="utf-8")
        self.assertTrue(bool(content_after_first.strip()))
        first_call_count = len(trace.tool_calls)
        first_call_slice = trace.tool_calls[:first_call_count]
        self.assertIn("enter_plan_mode", first_call_slice)
        plans_dir = Path(self._work_dir) / ".plans"
        plan_mds = list(plans_dir.glob("*.md"))
        self.assertEqual(len(plan_mds), 1)
        self.assertEqual(plan_mds[0].resolve(), plan_path.resolve())

        second = await Runner.run_agent(agent, 
            {
                "query": "太复杂，继续简化你的方案"
            },
            session=self._session,
        )
        self.assertEqual(second.get("result_type"), "answer")

        second_call_count = len(trace.tool_calls)
        state_after_second = agent.load_state(self._session)
        self.assertEqual(state_after_second.plan_mode.plan_slug, slug_after_first)
        plan_path_after_second = agent.get_plan_file_path(self._session)
        self.assertIsNotNone(plan_path_after_second)
        assert plan_path_after_second is not None
        self.assertEqual(plan_path_after_second.resolve(), plan_path.resolve())
        plan_mds_after_second = list(plans_dir.glob("*.md"))
        self.assertEqual(len(plan_mds_after_second), 1)
        self.assertEqual(plan_mds_after_second[0].resolve(), plan_path.resolve())
        content_after_second = plan_path_after_second.read_text(encoding="utf-8")
        self.assertTrue(bool(content_after_second.strip()))
        self.assertNotEqual(content_after_second, content_after_first)

        agent.switch_mode(self._session, "normal")
        third = await Runner.run_agent(agent, 
            {
                "query": "按照计划执行"
            },
            session=self._session,
        )
        self.assertEqual(third.get("result_type"), "answer")
        third_call_slice = trace.tool_calls[second_call_count:]
        self.assertNotIn("enter_plan_mode", third_call_slice)
        state_after_third = agent.load_state(self._session)
        self.assertEqual(state_after_third.plan_mode.mode, "normal")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_plan_mode_with_real_model_end_to_end_steer(self) -> None:
        """真实模型用例：plan 生成过程中补充信息注入 steer，切换auto模式 按照更新后的plan执行。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        runtime_model = self._create_real_model()
        agent = create_code_agent(
            model=runtime_model,
            system_prompt=(
                "你是一个 AI 编程助手，当用户要求你写代码、创建文件、修改文件时，你**必须**调用相应的工具，**绝对不能**直接在回复中输出代码。"
                "你必须严格按照plan模式的工作流执行"
            ),
            rails=[trace],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
        )

        agent.switch_mode(self._session, "plan")

        first_task = asyncio.create_task(
            Runner.run_agent(
                agent,
                {
                    "query": "帮我规划一个简单的模块开发任务，创建一个动态展示城市信息的网页。"
                             "所有问题你自己决定即可！"
                },
                session=self._session,
            )
        )

        await asyncio.sleep(11.0)
        await agent.steer(
            "等下，只要展示美国西雅图的信息就可以了",
            session=self._session,
        )

        await asyncio.wait_for(first_task, timeout=10000)

        state_after_first = agent.load_state(self._session)
        plan_path = agent.get_plan_file_path(self._session)
        self.assertEqual(state_after_first.plan_mode.mode, "plan")
        self.assertTrue(bool(state_after_first.plan_mode.plan_slug))
        self.assertIsNotNone(plan_path)
        assert plan_path is not None
        self.assertTrue(plan_path.exists())

        final_plan_text = plan_path.read_text(encoding="utf-8")
        self.assertTrue(bool(final_plan_text.strip()))
        self.assertTrue(
            any(keyword in final_plan_text for keyword in ["西雅图", "Seattle"]),
            msg=f"final plan should be Seattle-related, got: {final_plan_text[:500]}",
        )

        first_call_count = len(trace.tool_calls)

        agent.switch_mode(self._session, "normal")
        second = await Runner.run_agent(
            agent,
            {
                "query": "按照计划执行。"
            },
            session=self._session,
        )
        self.assertEqual(second.get("result_type"), "answer")

        second_call_slice = trace.tool_calls[first_call_count:]
        self.assertNotIn("enter_plan_mode", second_call_slice)
        self.assertIn("todo_create", second_call_slice)
        state_after_second = agent.load_state(self._session)
        self.assertEqual(state_after_second.plan_mode.mode, "normal")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_plan_mode_new_session_creates_distinct_plan_file(self) -> None:
        """真实模型用例：同一 workspace 下新 Session 仍会创建独立 plan 文件，不与旧会话冲突。"""
        self._require_llm_config()
        runtime_model = self._create_real_model()
        agent = create_code_agent(
            model=runtime_model,
            system_prompt=(
                "你是一个 AI 编程助手，当用户要求你写代码、创建文件、修改文件时，你**必须**调用相应的工具，**绝对不能**直接在回复中输出代码。"
                "你必须严格按照plan模式的工作流执行"
            ),
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
        )

        session_a = create_agent_session(
            session_id=f"deepagent_plan_mode_a_{uuid.uuid4().hex}",
            card=AgentCard(id="code_agent", name="code_agent", description="code agent test session"),
        )
        session_b = create_agent_session(
            session_id=f"deepagent_plan_mode_b_{uuid.uuid4().hex}",
            card=AgentCard(id="code_agent", name="code_agent", description="code agent test session"),
        )

        agent.switch_mode(session_a, "plan")
        first = await Runner.run_agent(agent, 
            {
                "query": "帮我规划一个简单的模块开发任务，创建一个动态展示城市信息的网页。"
                         "所有问题你自己决定即可！"
            },
            session=session_a,
        )
        self.assertEqual(first.get("result_type"), "answer")

        state_a = agent.load_state(session_a)
        plan_path_a = agent.get_plan_file_path(session_a)
        slug_a = state_a.plan_mode.plan_slug
        self.assertEqual(state_a.plan_mode.mode, "plan")
        self.assertTrue(bool(slug_a))
        self.assertIsNotNone(plan_path_a)
        assert plan_path_a is not None
        self.assertTrue(plan_path_a.exists())
        self.assertTrue(bool(plan_path_a.read_text(encoding="utf-8").strip()))

        plans_dir = Path(self._work_dir) / ".plans"
        plan_mds_after_a = list(plans_dir.glob("*.md"))
        self.assertEqual(len(plan_mds_after_a), 1)
        self.assertEqual(plan_mds_after_a[0].resolve(), plan_path_a.resolve())

        agent.switch_mode(session_b, "plan")
        second = await Runner.run_agent(agent, 
            {
                "query": "帮我规划另一个简单任务：给项目加一个 README，说明如何本地运行"
            },
            session=session_b,
        )
        self.assertEqual(second.get("result_type"), "answer")

        state_b = agent.load_state(session_b)
        plan_path_b = agent.get_plan_file_path(session_b)
        slug_b = state_b.plan_mode.plan_slug
        self.assertEqual(state_b.plan_mode.mode, "plan")
        self.assertTrue(bool(slug_b))
        self.assertNotEqual(slug_b, slug_a)
        self.assertIsNotNone(plan_path_b)
        assert plan_path_b is not None
        self.assertNotEqual(plan_path_b.resolve(), plan_path_a.resolve())
        self.assertTrue(plan_path_b.exists())
        self.assertTrue(bool(plan_path_b.read_text(encoding="utf-8").strip()))

        plan_mds_after_b = sorted(
            plans_dir.glob("*.md"), key=lambda p: str(p.resolve())
        )
        self.assertEqual(len(plan_mds_after_b), 2)
        self.assertSetEqual(
            {p.resolve() for p in plan_mds_after_b},
            {plan_path_a.resolve(), plan_path_b.resolve()},
        )

        state_a_again = agent.load_state(session_a)
        plan_path_a_again = agent.get_plan_file_path(session_a)
        self.assertEqual(state_a_again.plan_mode.plan_slug, slug_a)
        self.assertIsNotNone(plan_path_a_again)
        assert plan_path_a_again is not None
        self.assertEqual(plan_path_a_again.resolve(), plan_path_a.resolve())

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_plan_mode_ask_user_interrupt_and_resume(self) -> None:
        """Plan 模式下 ask_user 中断后，用户输入可恢复并继续规划。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        runtime_model = self._create_real_model()

        agent = create_code_agent(
            model=runtime_model,
            system_prompt=(
                "你是一个 AI 编程助手。"
                "在 plan 模式中，若需求缺少关键信息，你必须调用 ask_user 提问澄清；"
                "收到用户回答后继续完善计划。"
            ),
            rails=[trace],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
        )

        agent.switch_mode(self._session, "plan")

        # 1) 触发 ask_user 中断
        first = await Runner.run_agent(agent, 
            {
                "query": (
                    "我想要创建一个动态展示城市信息的简易网页，但我暂时不告诉你是哪个城市。"
                    "你最多只能询问用户一个城市相关问题。其他问题你自行决定即可。"
                )
            },
            session=self._session,
        )

        self.assertEqual(first.get("result_type"), "interrupt")
        interrupt_ids = first.get("interrupt_ids", [])
        self.assertEqual(len(interrupt_ids), 1)
        tool_call_id = interrupt_ids[0]

        # 2) 用 InteractiveInput 恢复
        interactive_input = InteractiveInput()
        interactive_input.update(
            tool_call_id,
            {"answer": "展示美国西雅图的信息"}
        )

        second = await Runner.run_agent(
            agent,
            {"query": interactive_input},
            session=self._session,
        )

        # 3) 恢复后应继续执行并产出正常回答
        self.assertEqual(second.get("result_type"), "answer")
        self.assertIn("ask_user", trace.tool_calls)

        state = agent.load_state(self._session)
        plan_path = agent.get_plan_file_path(self._session)
        self.assertEqual(state.plan_mode.mode, "plan")
        self.assertTrue(bool(state.plan_mode.plan_slug))
        self.assertIsNotNone(plan_path)
        assert plan_path is not None
        self.assertTrue(plan_path.exists())
        self.assertTrue(bool(plan_path.read_text(encoding="utf-8").strip()))

        # 关键回归：恢复阶段不应出现白名单拒绝 ask_user 的痕迹
        answer_text = str(second.get("answer", ""))
        self.assertNotIn("Tool 'ask_user' is not available in plan mode", answer_text)

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_query_switch_plan_to_auto_and_execute(self) -> None:
        """用户query 触发 plan->normal 切换并按计划执行。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        runtime_model = self._create_real_model()
        agent = create_code_agent(
            model=runtime_model,
            system_prompt=(
                "你是一个 AI 编程助手，你可以根据用户的意图自动切换模式"
            ),
            rails=[trace],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
        )

        agent.switch_mode(self._session, "plan")
        first = await Runner.run_agent(
            agent,
            {
                "query": "先帮我做一个简单开发计划：创建动态展示城市信息的网页，细节你自行决定。"
            },
            session=self._session,
        )
        self.assertEqual(first.get("result_type"), "answer")

        first_call_count = len(trace.tool_calls)
        second = await Runner.run_agent(
            agent,
            {
                "query": "按刚才的计划直接执行。"
            },
            session=self._session,
        )
        self.assertEqual(second.get("result_type"), "answer")

        second_call_slice = trace.tool_calls[first_call_count:]
        self.assertIn("switch_mode", second_call_slice)
        self.assertNotIn("enter_plan_mode", second_call_slice)
        self.assertIn("todo_create", second_call_slice)

        state_after_second = agent.load_state(self._session)
        self.assertEqual(state_after_second.plan_mode.mode, "normal")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_query_switch_auto_to_plan_and_generate_plan(self) -> None:
        """用户query 触发 normal->plan 切换并生成计划文件。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        runtime_model = self._create_real_model()
        agent = create_code_agent(
            model=runtime_model,
            rails=[trace],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
        )

        agent.switch_mode(self._session, "normal")

        first = await Runner.run_agent(
            agent,
            {
                "query": "给我一个计划：做一个简易城市信息展示网页。"
            },
            session=self._session,
        )

        if first.get("result_type") == "interrupt":
            interrupt_ids = first.get("interrupt_ids", [])
            self.assertTrue(bool(interrupt_ids))
            tool_call_id = interrupt_ids[0]

            interactive_input = InteractiveInput()
            interactive_input.update(tool_call_id, {"answer": "你自行决定方案，越简单越好"})

            second = await Runner.run_agent(
                agent,
                {"query": interactive_input},
                session=self._session,
            )
            self.assertEqual(second.get("result_type"), "answer")
        else:
            self.assertEqual(first.get("result_type"), "answer")

        self.assertIn("switch_mode", trace.tool_calls)
        self.assertIn("enter_plan_mode", trace.tool_calls)

        state = agent.load_state(self._session)
        plan_path = agent.get_plan_file_path(self._session)
        self.assertEqual(state.plan_mode.mode, "plan")
        self.assertTrue(bool(state.plan_mode.plan_slug))
        self.assertIsNotNone(plan_path)
        assert plan_path is not None
        self.assertTrue(plan_path.exists())
        self.assertTrue(bool(plan_path.read_text(encoding="utf-8").strip()))

        plans_dir = Path(self._work_dir) / ".plans"
        plan_mds = list(plans_dir.glob("*.md"))
        self.assertEqual(len(plan_mds), 1)
        self.assertEqual(plan_mds[0].resolve(), plan_path.resolve())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
