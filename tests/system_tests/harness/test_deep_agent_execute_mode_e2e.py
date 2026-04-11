# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent 运行模式切换测试"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import List, cast
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail

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
            session_id=f"deepagent_plan_mode_{uuid.uuid4().hex}"
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
        """真实模型用例：首次写入 plan，二次切换auto模式，按照plan执行。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        fs_rail = FileSystemRail()
        agent = create_deep_agent(
            model=self._create_real_model(),
            system_prompt=(
                "你是一个 AI 编程助手，当用户要求你写代码、创建文件、修改文件时，你**必须**调用相应的工具，**绝对不能**直接在回复中输出代码。"
                "你必须严格按照plan模式的工作流执行"
            ),
            rails=[trace, fs_rail],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
            enable_plan_mode=True,
            enable_task_planning=True,
        )

        agent.switch_mode(self._session, "plan")
        first = await agent.invoke(
            {
                "query": "帮我规划一个简单的模块开发任务，创建一个动态展示城市信息的网页"
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

        agent.switch_mode(self._session, "auto")
        second = await agent.invoke(
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
        self.assertEqual(state_after_second.plan_mode.mode, "auto")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_plan_mode_with_real_model_end_to_end_modify_plan(self) -> None:
        """真实模型用例：首次写入 plan，二次不切换模式修正plan，三次切换模式根据修正后的plan执行。"""
        self._require_llm_config()
        trace = ToolTraceRail()
        fs_rail = FileSystemRail()
        agent = create_deep_agent(
            model=self._create_real_model(),
            system_prompt=(
                "你是一个 AI 编程助手，当用户要求你写代码、创建文件、修改文件时，你**必须**调用相应的工具，**绝对不能**直接在回复中输出代码。"
            ),
            rails=[trace, fs_rail],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
            enable_plan_mode=True,
            enable_task_planning=True,
        )

        agent.switch_mode(self._session, "plan")
        first = await agent.invoke(
            {
                "query": "帮我规划一个简单的模块开发任务，创建一个动态展示城市信息的网页"
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

        second = await agent.invoke(
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

        agent.switch_mode(self._session, "auto")
        third = await agent.invoke(
            {
                "query": "按照计划执行"
            },
            session=self._session,
        )
        self.assertEqual(third.get("result_type"), "answer")
        third_call_slice = trace.tool_calls[second_call_count:]
        self.assertNotIn("enter_plan_mode", third_call_slice)
        state_after_third = agent.load_state(self._session)
        self.assertEqual(state_after_third.plan_mode.mode, "auto")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_plan_mode_new_session_creates_distinct_plan_file(self) -> None:
        """真实模型用例：同一 workspace 下新 Session 仍会创建独立 plan 文件，不与旧会话冲突。"""
        self._require_llm_config()
        fs_rail = FileSystemRail()
        agent = create_deep_agent(
            model=self._create_real_model(),
            system_prompt=(
                "你是一个 AI 编程助手，当用户要求你写代码、创建文件、修改文件时，你**必须**调用相应的工具，**绝对不能**直接在回复中输出代码。"
                "你必须严格按照plan模式的工作流执行"
            ),
            rails=[fs_rail],
            enable_task_loop=True,
            max_iterations=24,
            workspace=self._work_dir,
            enable_plan_mode=True,
            enable_task_planning=True,
        )

        session_a = create_agent_session(
            session_id=f"deepagent_plan_mode_a_{uuid.uuid4().hex}"
        )
        session_b = create_agent_session(
            session_id=f"deepagent_plan_mode_b_{uuid.uuid4().hex}"
        )

        agent.switch_mode(session_a, "plan")
        first = await agent.invoke(
            {
                "query": "帮我规划一个简单的模块开发任务，创建一个动态展示城市信息的网页"
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
        second = await agent.invoke(
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
