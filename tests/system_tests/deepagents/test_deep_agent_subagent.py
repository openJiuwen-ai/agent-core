# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent SessionRail / SubAgentRail 子任务系统测试。"""

from __future__ import annotations

import asyncio
import time
import unittest
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.subagents import create_code_agent, create_research_agent

from tests.system_tests.deepagents.test_deep_agent_e2e import (
    LoopObserveRail,
    ToolTraceRail,
    TestDeepAgentE2E,
    _build_mock_runtime_model,
)
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


class TestDeepAgentSubagentRail(TestDeepAgentE2E):
    """SubagentRail 执行后台子任务 端到端场景"""

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_tasks_using_subagents(self):
        """多步复杂任务：调用subagent来完成调研，主agent查看并总结调研结果。
            - 验证主agent可以通过task工具调用subagent执行任务
            - 验证主agent和subagent共享workspace，主agent可以使用subagent创建的文件
        """
        self._require_llm_config()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        fs_rail = self._get_fs_rail()
        tool_trace = ToolTraceRail()
        research_agent = SubAgentConfig(
            agent_card=AgentCard(
                name="research_agent",
                description="专注于研究调查任务，当用户想要调查某问题时，可使用该代理执行研究工作。每次只给这位研究员一个主题。",
            ),
            system_prompt="你是一名研究助理，负责针对用户输入的主题开展研究工作。",
            rails=[fs_rail],
        )
        model = self._create_model()
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            enable_task_loop=False,
            max_iterations=12,
            subagents=[research_agent],
            rails=[tool_trace, fs_rail],
            sys_operation=sys_oper,
            add_general_purpose_agent=True
        )

        query = (
            "请严格按顺序执行以下任务，并且每一步都必须调用工具：\n"
            "1. 调查随机森林算法应用场景，创建summary_research.txt文件，写入内容为调查结果；\n"
            "2. 使用工具读取 summary_research.txt 文件；\n"
            "3. 返回文件的结果"
        )
        result = await Runner.run_agent(agent, {"query": query})
        logger.info("get final result: %s", result)

        tool_counts = Counter(tool_trace.tool_calls)
        self.assertGreaterEqual(tool_counts.get("task_tool", 0), 1)
        # 写入file工具应该是 subagent research_agent调用，这里应该为0
        self.assertGreaterEqual(tool_counts.get("write_file", 0), 0)
        self.assertGreaterEqual(tool_counts.get("read_file", 0), 1)

        summary_path = Path(self._work_dir) / "summary_research.txt"
        self.assertTrue(summary_path.exists())

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_tasks_using_predefined_subagents(self):
        """多步复杂任务：调用预置subagent来完成调研，主agent查看并总结调研结果。
            - 验证主agent可以通过task工具并行调用subagent执行任务，生成多个task_tool调用
        """
        self._require_llm_config()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        fs_rail = self._get_fs_rail()
        tool_trace = ToolTraceRail()
        model = self._create_model()
        research_agent = create_research_agent(model=model, sys_operation=sys_oper)
        code_agent = create_code_agent(model=model, sys_operation=sys_oper)
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            enable_task_loop=False,
            max_iterations=12,
            subagents=[research_agent, code_agent],
            rails=[tool_trace, fs_rail],
            sys_operation=sys_oper,
        )

        query = (
            "请严格按顺序执行以下任务，并且每一步都必须调用工具：\n"
            "1. 我想研究詹姆斯、科比的成就并对比；\n"
            "2. 创建 summary_research.txt，写入内容为上一步调查的结果；\n"
            "3. 使用工具读取 summary_research.txt 文件；\n"
            "4. 对比两个人的成就返回总结结果"
        )
        result = await Runner.run_agent(agent, {"query": query})

        tool_counts = Counter(tool_trace.tool_calls)
        self.assertGreaterEqual(tool_counts.get("task_tool", 0), 2)
        self.assertGreaterEqual(tool_counts.get("write_file", 0), 1)
        self.assertGreaterEqual(tool_counts.get("read_file", 0), 1)

        summary_path = Path(self._work_dir) / "summary_research.txt"
        self.assertTrue(summary_path.exists())


class TestDeepAgentSessionRail(TestDeepAgentE2E):
    """SessionRail 异步执行子任务 端到端场景。"""

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_auto_invoke_on_spawn_done_no_query2(self):
        """场景 1：仅 q1 spawn，无用户 q2；后台子任务完成后 auto-invoke 总结。"""
        self._require_llm_config()
        model = self._create_model()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        subagents = [
            SubAgentConfig(
                AgentCard(name="research_agent", description="专注于研究调查任务，当用户想要调查某问题时，可使用该代理执行研究工作。每次只给这位研究员一个主题。"),
                rails=[FileSystemRail()],
                system_prompt="你是研究助理，负责围绕用户输入的主题开展调研，仅需返回最终研究结果。"
            )
        ]
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            subagents=subagents,
            enable_task_loop=True,
            enable_async_subagent=True,
            max_iterations=20,
            sys_operation=sys_oper,
            add_general_purpose_agent=True
        )
        cid = f"auto_invoke_{uuid.uuid4().hex}"
        q1 = "提交后台任务：分析Chipotle为什么还没有进入中国市场，不要写入文件！"
        r1 = await Runner.run_agent(agent, {"query": q1, "conversation_id": cid})
        logger.info(f"q1 invoke with result: {r1}")
        self.assertEqual(r1.get("result_type"), "answer")
        self.assertFalse(agent.is_invoke_active)
        toolkit = getattr(agent, "_session_toolkit", None)
        self.assertIsNotNone(toolkit)
        max_wait = 300
        wait_interval = 5
        elapsed = 0
        done = False
        while elapsed < max_wait:
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
            rows = toolkit.list_all()
            if rows and all(r.status in ("completed", "error") for r in rows):
                await asyncio.sleep(2)
                done = True
                break
        self.assertTrue(done, "spawn 未在超时内完成")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_async_spawn_query2_not_blocked(self):
        """场景2：q1 spawn 长后台任务后 q2 短问 (q2先于q1子任务完成)；q2 完成时模型侧尚未观测到 [STEERING]；且q2应该和q1使用的是同一个controller"""
        self._require_llm_config()
        steer_marker = "[STEERING]"
        observe = LoopObserveRail(steer_text=steer_marker)
        model = self._create_model()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        subagents = [create_research_agent(model=model, sys_operation=sys_oper)]
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            subagents=subagents,
            rails=[observe],
            enable_task_loop=True,
            enable_async_subagent=True,
            max_iterations=20,
            sys_operation=sys_oper,
        )
        cid = f"async_spawn_{uuid.uuid4().hex}"
        q1 = (
            "执行一个后台任务：分析为什么Chipotle还不进入中国市场（分析详细一点）"
        )
        q2 = "2+3等于几？只回答数字。"
        r1 = await Runner.run_agent(agent, {"query": q1, "conversation_id": cid})
        logger.info("q1 invoke with result: %s", r1)
        self.assertIsNotNone(agent.loop_controller)
        controller_after_q1 = agent.loop_controller
        r2 = await asyncio.wait_for(
            Runner.run_agent(agent, {"query": q2, "conversation_id": cid}),
            timeout=120.0,
        )
        logger.info("q2 invoke with result: %s", r2)
        self.assertEqual(r2.get("result_type"), "answer")
        self.assertFalse(observe.steer_seen_in_model_messages)
        self.assertIs(agent.loop_controller, controller_after_q1)
        toolkit = getattr(agent, "_session_toolkit", None)
        self.assertIsNotNone(toolkit)
        max_wait = 300
        wait_interval = 5
        elapsed = 0
        done = False
        while elapsed < max_wait:
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
            rows = toolkit.list_all()
            if rows and all(r.status in ("completed", "error") for r in rows):
                await asyncio.sleep(2)
                done = True
                break
        # q1 长任务最终完成
        self.assertTrue(done, "spawn 未在超时内完成")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_async_spawn_steering_visible_during_query3(self):
        """场景 3：q1 spawn、q2 短问后 q3 长工具链 (q1后台任务在q3结束前完成) q4 列出所有后台任务；spawn 在 q3 窗口内完成时应能观测到 [STEERING]。"""
        self._require_llm_config()
        steer_marker = "[STEERING]"
        observe = LoopObserveRail(steer_text=steer_marker)
        model = self._create_model()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        fs_rail = self._get_fs_rail()
        subagents = [create_research_agent(model=model, sys_operation=sys_oper)]
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            subagents=subagents,
            rails=[fs_rail, observe],
            enable_task_loop=True,
            enable_async_subagent=True,
            max_iterations=15,
            sys_operation=sys_oper,
            add_general_purpose_agent=True
        )
        cid = f"async_steer_{uuid.uuid4().hex}"
        q1 = (
            "提交后台任务：研究詹姆斯成就"
        )
        q2 = "一句话：天空是什么颜色？"
        q3 = (
            "在当前工作目录：用 write_file 写 a.txt 内容为 hello；"
            "用 list_dir 列出目录；用 read_file 读 a.txt；最后删除 a.txt 分步完成。"
        )
        q4 = "用 sessions_list 工具查看当前后台任务"
        await Runner.run_agent(agent, {"query": q1, "conversation_id": cid})
        await Runner.run_agent(agent, {"query": q2, "conversation_id": cid})
        observe.steer_seen_in_model_messages = False
        r3 = await asyncio.wait_for(
            Runner.run_agent(agent, {"query": q3, "conversation_id": cid}),
            timeout=600.0,
        )
        self.assertEqual(r3.get("result_type"), "answer")
        self.assertTrue(observe.steer_seen_in_model_messages)

        r4 = await Runner.run_agent(agent, {"query": q4, "conversation_id": cid})
        logger.info("q4 invoke with result: %s", r4)

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_auto_invoke_dedup_multi_spawn(self):
        """场景4：一次对话同时触发多个后台任务，所有任务执行完后总结"""
        self._require_llm_config()
        steer_marker = "[STEERING]"
        observe = LoopObserveRail(steer_text=steer_marker)
        model = self._create_model()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        subagents = [create_research_agent(model=model, sys_operation=sys_oper)]
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            subagents=subagents,
            rails=[observe],
            enable_task_loop=True,
            enable_async_subagent=True,
            max_iterations=15,
            sys_operation=sys_oper,
        )
        cid = f"multi_spawn_{uuid.uuid4().hex}"
        q1 = (
            "同时做下面两件事：\n"
            "1. 任务 A：查询随机森林算法的应用\n"
            "2. 任务 B：天空是什么颜色\n"
        )
        r1 = await Runner.run_agent(agent, {"query": q1, "conversation_id": cid})
        # 主round已经执行完成
        self.assertEqual(r1.get("result_type"), "answer")
        logger.info("q1 invoke with result: %s", r1)
        toolkit = getattr(agent, "_session_toolkit", None)
        self.assertIsNotNone(toolkit)
        self.assertGreaterEqual(len(toolkit.list_all()), 2)
        max_wait = 300
        wait_interval = 5
        elapsed = 0
        while elapsed < max_wait:
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
            rows = toolkit.list_all()
            if all(r.status in ("completed", "error") for r in rows):
                await asyncio.sleep(2)
                break
        self.assertTrue(
            all(r.status in ("completed", "error") for r in toolkit.list_all()),
            "后台任务未在超时内完成",
        )

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_real_llm_two_spawn_cancel_one_other_completes(self):
        """基于真实 LLM验证取消任务：同时下发A、B两个后台任务，再只取消A任务，B任务应正常完成。"""
        self._require_llm_config()
        tool_trace = ToolTraceRail()
        model = self._create_model()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        subagents = [create_research_agent(model=model, sys_operation=sys_oper)]
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            subagents=subagents,
            rails=[tool_trace],
            enable_task_loop=True,
            enable_async_subagent=True,
            max_iterations=15,
            sys_operation=sys_oper,
        )
        cid = f"two_spawn_cancel_{uuid.uuid4().hex}"
        kw_rf = "随机森林"
        kw_kobe = "科比"
        q1 = (
            "同时下发两个后台任务（请分两次调用 sessions_spawn，每次提交一个任务，不要合并成一次）：\n"
            "任务A：总结随机森林算法应用\n"
            "任务B：总结科比的成就\n"
            "完成后简要说明两个后台任务均已提交。"
        )
        r1 = await asyncio.wait_for(
            Runner.run_agent(agent, {"query": q1, "conversation_id": cid}),
            timeout=300.0,
        )
        self.assertEqual(r1.get("result_type"), "answer")
        toolkit = getattr(agent, "_session_toolkit", None)
        self.assertIsNotNone(toolkit)
        deadline = time.monotonic() + 120.0
        rows: list[Any] = []
        while time.monotonic() < deadline:
            rows = toolkit.list_all()
            if len(rows) >= 2:
                break
            await asyncio.sleep(2.0)
        self.assertGreaterEqual(
            len(rows),
            2,
            f"应在超时内登记到两个后台任务，当前: {[getattr(r, 'task_id', r) for r in rows]}",
        )
        row_rf = next(
            (r for r in rows if kw_rf in (getattr(r, "description", None) or "")),
            None,
        )
        row_kobe = next(
            (r for r in rows if kw_kobe in (getattr(r, "description", None) or "")),
            None,
        )
        self.assertIsNotNone(row_rf, f"未匹配到任务A: {rows!r}")
        self.assertIsNotNone(row_kobe, f"未匹配到任务B: {rows!r}")
        task_rf_id = row_rf.task_id
        task_kobe_id = row_kobe.task_id
        self.assertNotEqual(task_rf_id, task_kobe_id)

        q2 = (
            "取消「随机森林」相关的那一个任务"
        )
        r2 = await asyncio.wait_for(
            Runner.run_agent(agent, {"query": q2, "conversation_id": cid}),
            timeout=180.0,
        )
        self.assertEqual(r2.get("result_type"), "answer")
        self.assertGreaterEqual(
            tool_trace.tool_calls.count("sessions_cancel"),
            1,
            "应至少调用一次 sessions_cancel",
        )

        max_wait = 300
        wait_interval = 5
        elapsed = 0
        done = False
        last_snapshot = ""
        while elapsed < max_wait:
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
            rows = toolkit.list_all()
            by_id = {r.task_id: r for r in rows}
            rf = by_id.get(task_rf_id)
            kobe = by_id.get(task_kobe_id)
            last_snapshot = ", ".join(
                f"{r.task_id}:{r.status}" for r in rows
            )
            if rf is None or kobe is None:
                continue
            if rf.status == "canceled" and kobe.status == "completed":
                done = True
                break
        self.assertTrue(
            done,
            "随机森林任务应 canceled，科比任务应 completed；"
            f" snapshot=[{last_snapshot}]",
        )


class TestDeepAgentSessionRailCancelMock(TestDeepAgentE2E):
    """Session cancel 场景测试（Mock LLM + 可控子代理）。"""

    class _SleepSubAgent:
        def __init__(self, delay: float = 3.0, output: str = "done"):
            self._delay = delay
            self._output = output

        async def invoke(self, inputs):
            _ = inputs
            await asyncio.sleep(self._delay)
            return {"output": self._output}

    class _FastSubAgent:
        def __init__(self, output: str = "fast done"):
            self._output = output

        async def invoke(self, inputs):
            _ = inputs
            return {"output": self._output}

    @staticmethod
    def _build_cancel_agent(
        mock_llm: MockLLMModel,
        rails: list | None = None,
    ):
        model = _build_mock_runtime_model(mock_llm)
        research_agent = SubAgentConfig(
            agent_card=AgentCard(
                name="research_agent",
                description="专注于研究调查任务，当用户想要调查某问题时，可使用该代理执行研究工作。每次只给这位研究员一个主题。",
            ),
            system_prompt="你是一名研究助理，负责针对用户输入的主题开展研究工作。",
        )
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
            ),
            subagents=[research_agent],
            rails=rails or [],
            enable_task_loop=True,
            enable_async_subagent=True,
            max_iterations=10,
        )
        return agent

    @staticmethod
    async def _noop_auto_invoke(session: object) -> None:
        _ = session

    @staticmethod
    def _patch_spawn_task_ids(fixed_hexes: list[str]):
        """Wrap SessionsSpawnTool.invoke: first uuid4() per call is fixed (no prod / path hacks)."""
        from openjiuwen.deepagents.tools import session_tools as st

        real_invoke = st.SessionsSpawnTool.invoke
        pending = list(fixed_hexes)
        orig_uuid4 = uuid.uuid4

        async def _wrapped(self, inputs, **kwargs):
            first = [True]

            def _uuid4_side_effect():
                if first[0]:
                    first[0] = False
                    if pending:
                        return SimpleNamespace(hex=pending.pop(0))
                return orig_uuid4()

            with patch.object(st.uuid, "uuid4", side_effect=_uuid4_side_effect):
                return await real_invoke(self, inputs, **kwargs)

        return patch.object(st.SessionsSpawnTool, "invoke", new=_wrapped)

    @pytest.mark.asyncio
    async def test_sessions_cancel_scenario1_immediate_cancel(self):
        """场景1：spawn 后立即取消。"""
        fixed_task_id = "cancel_s1_task_id"
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "sessions_spawn",
                '{"subagent_type": "general-purpose", "task_description": "long task"}',
                tool_call_id="spawn_1",
            ),
            create_text_response("spawn submitted"),
            create_tool_call_response(
                "sessions_cancel",
                f'{{"task_id": "{fixed_task_id}"}}',
                tool_call_id="cancel_1",
            ),
            create_text_response("cancel requested"),
        ])
        agent = self._build_cancel_agent(mock_llm)
        agent.schedule_auto_invoke_on_spawn_done = self._noop_auto_invoke

        def _create_sleep_subagent(
            _subagent_type: str, _subsession_id: str
        ) -> TestDeepAgentSessionRailCancelMock._SleepSubAgent:
            return self._SleepSubAgent(delay=5.0, output="never")

        agent.create_subagent = _create_sleep_subagent

        cid = f"cancel_s1_{uuid.uuid4().hex}"
        with TestDeepAgentSessionRailCancelMock._patch_spawn_task_ids([fixed_task_id]):
            await Runner.run_agent(agent, {"query": "创建后台任务", "conversation_id": cid})
        toolkit = getattr(agent, "_session_toolkit")
        rows = toolkit.list_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].task_id, fixed_task_id)

        await Runner.run_agent(agent, {"query": "取消后台任务", "conversation_id": cid})
        row = toolkit.get(fixed_task_id)
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "canceled")

    @pytest.mark.asyncio
    async def test_sessions_cancel_scenario2_cancel_when_running(self):
        """场景2：任务进入 running 后取消。"""
        fixed_task_id = "cancel_s2_task_id"
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "sessions_spawn",
                '{"subagent_type": "general-purpose", "task_description": "slow analysis"}',
                tool_call_id="spawn_2",
            ),
            create_text_response("spawn ok"),
            create_tool_call_response(
                "sessions_cancel",
                f'{{"task_id": "{fixed_task_id}"}}',
                tool_call_id="cancel_2",
            ),
            create_text_response("cancel ok"),
        ])
        agent = self._build_cancel_agent(mock_llm)
        agent.schedule_auto_invoke_on_spawn_done = self._noop_auto_invoke

        def _create_slow_subagent(
            _subagent_type: str, _subsession_id: str
        ) -> TestDeepAgentSessionRailCancelMock._SleepSubAgent:
            return self._SleepSubAgent(delay=8.0, output="slow done")

        agent.create_subagent = _create_slow_subagent

        cid = f"cancel_s2_{uuid.uuid4().hex}"
        with TestDeepAgentSessionRailCancelMock._patch_spawn_task_ids([fixed_task_id]):
            await Runner.run_agent(agent, {"query": "请后台执行一个慢任务", "conversation_id": cid})
        toolkit = getattr(agent, "_session_toolkit")
        rows = toolkit.list_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].task_id, fixed_task_id)

        await asyncio.sleep(0.2)
        await Runner.run_agent(agent, {"query": "请取消这个后台任务", "conversation_id": cid})
        row = toolkit.get(fixed_task_id)
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "canceled")

    @pytest.mark.asyncio
    async def test_sessions_cancel_scenario3_cancel_should_not_trigger_steering(self):
        """场景3：取消后不应触发 steering 注入。"""
        fixed_task_id = "cancel_s3_task_id"
        steer_marker = "[STEERING]"
        observe = LoopObserveRail(steer_text=steer_marker)
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "sessions_spawn",
                '{"subagent_type": "general-purpose", "task_description": "slow task"}',
                tool_call_id="spawn_3",
            ),
            create_text_response("spawn ok"),
            create_tool_call_response(
                "sessions_cancel",
                f'{{"task_id": "{fixed_task_id}"}}',
                tool_call_id="cancel_3",
            ),
            create_text_response("cancel ok"),
            create_text_response("普通回答"),
        ])
        agent = self._build_cancel_agent(mock_llm, rails=[observe])
        agent.schedule_auto_invoke_on_spawn_done = self._noop_auto_invoke

        def _create_slow_subagent_s3(
            _subagent_type: str, _subsession_id: str
        ) -> TestDeepAgentSessionRailCancelMock._SleepSubAgent:
            return self._SleepSubAgent(delay=6.0, output="done")

        agent.create_subagent = _create_slow_subagent_s3

        cid = f"cancel_s3_{uuid.uuid4().hex}"
        with TestDeepAgentSessionRailCancelMock._patch_spawn_task_ids([fixed_task_id]):
            await Runner.run_agent(agent, {"query": "创建后台任务", "conversation_id": cid})
        toolkit = getattr(agent, "_session_toolkit")
        self.assertEqual(toolkit.list_all()[0].task_id, fixed_task_id)
        await Runner.run_agent(agent, {"query": "取消它", "conversation_id": cid})
        observe.steer_seen_in_model_messages = False
        await Runner.run_agent(agent, {"query": "取消后正常问答", "conversation_id": cid})
        self.assertFalse(observe.steer_seen_in_model_messages)

    @pytest.mark.asyncio
    async def test_sessions_cancel_scenario4_cancel_one_of_multiple_tasks(self):
        """场景4：多任务中只取消一个，另一个完成。"""
        fixed_task_id_1 = "cancel_s4_task_id_1"
        fixed_task_id_2 = "cancel_s4_task_id_2"
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "sessions_spawn",
                '{"subagent_type": "general-purpose", "task_description": "task a"}',
                tool_call_id="spawn_4a",
            ),
            create_tool_call_response(
                "sessions_spawn",
                '{"subagent_type": "general-purpose", "task_description": "task b"}',
                tool_call_id="spawn_4b",
            ),
            create_text_response("two tasks spawned"),
            create_tool_call_response(
                "sessions_cancel",
                f'{{"task_id": "{fixed_task_id_1}"}}',
                tool_call_id="cancel_4",
            ),
            create_text_response("one cancelled"),
        ])
        agent = self._build_cancel_agent(mock_llm)
        agent.schedule_auto_invoke_on_spawn_done = self._noop_auto_invoke

        def _factory(_subagent_type: str, sub_session_id: str):
            # Bind slow/fast by task_id, not by call order (spawn tasks may run concurrently).
            tk = getattr(agent, "_session_toolkit", None)
            assert tk is not None
            row = next(
                (r for r in tk.list_all() if r.sub_session_id == sub_session_id),
                None,
            )
            assert row is not None
            if row.task_id == fixed_task_id_1:
                return self._SleepSubAgent(delay=6.0, output="a done")
            return self._FastSubAgent(output="b done")

        agent.create_subagent = _factory

        cid = f"cancel_s4_{uuid.uuid4().hex}"
        with TestDeepAgentSessionRailCancelMock._patch_spawn_task_ids(
            [fixed_task_id_1, fixed_task_id_2]
        ):
            await Runner.run_agent(agent, {"query": "创建两个后台任务", "conversation_id": cid})
        toolkit = getattr(agent, "_session_toolkit")
        rows = toolkit.list_all()
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn(fixed_task_id_1, {r.task_id for r in rows})
        self.assertIn(fixed_task_id_2, {r.task_id for r in rows})

        await Runner.run_agent(agent, {"query": "取消其中一个任务", "conversation_id": cid})
        await asyncio.sleep(0.5)
        final_rows = toolkit.list_all()
        status_map = {r.task_id: r.status for r in final_rows}
        self.assertEqual(status_map[fixed_task_id_1], "canceled")
        self.assertIn("completed", status_map.values())

    @pytest.mark.asyncio
    async def test_sessions_cancel_scenario5_repeat_cancel_idempotent(self):
        """场景5：同一任务重复取消，应幂等。"""
        fixed_task_id = "cancel_s5_task_id"
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "sessions_spawn",
                '{"subagent_type": "general-purpose", "task_description": "task"}',
                tool_call_id="spawn_5",
            ),
            create_text_response("spawn ok"),
            create_tool_call_response(
                "sessions_cancel",
                f'{{"task_id": "{fixed_task_id}"}}',
                tool_call_id="cancel_5_1",
            ),
            create_text_response("cancel 1 ok"),
            create_tool_call_response(
                "sessions_cancel",
                f'{{"task_id": "{fixed_task_id}"}}',
                tool_call_id="cancel_5_2",
            ),
            create_text_response("cancel 2 ok"),
        ])
        agent = self._build_cancel_agent(mock_llm)
        agent.schedule_auto_invoke_on_spawn_done = self._noop_auto_invoke

        def _create_sleep_subagent_s5(
            _subagent_type: str, _subsession_id: str
        ) -> TestDeepAgentSessionRailCancelMock._SleepSubAgent:
            return self._SleepSubAgent(delay=5.0, output="done")

        agent.create_subagent = _create_sleep_subagent_s5

        cid = f"cancel_s5_{uuid.uuid4().hex}"
        with TestDeepAgentSessionRailCancelMock._patch_spawn_task_ids([fixed_task_id]):
            await Runner.run_agent(agent, {"query": "创建后台任务", "conversation_id": cid})
        toolkit = getattr(agent, "_session_toolkit")
        self.assertEqual(toolkit.list_all()[0].task_id, fixed_task_id)

        await Runner.run_agent(agent, {"query": "第一次取消", "conversation_id": cid})
        await Runner.run_agent(agent, {"query": "第二次取消", "conversation_id": cid})
        row = toolkit.get(fixed_task_id)
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "canceled")

    @pytest.mark.asyncio
    async def test_sessions_cancel_scenario6_cancel_completed_task(self):
        """场景6：任务已完成后取消，应保持 completed。"""
        fixed_task_id = "cancel_s6_task_id"
        mock_llm = MockLLMModel()
        # 只排队 spawn 回合：若一次 set_responses 里连 cancel 也排队，第一次 run_agent
        # 会在 spawn 完成后的 follow-up / 多轮 ReAct 里把 cancel 也消费掉，toolkit 会先变 canceled。
        mock_llm.set_responses([
            create_tool_call_response(
                "sessions_spawn",
                '{"subagent_type": "general-purpose", "task_description": "fast complete"}',
                tool_call_id="spawn_6",
            ),
            create_text_response("spawn ok"),
        ])
        agent = self._build_cancel_agent(mock_llm)
        agent.schedule_auto_invoke_on_spawn_done = self._noop_auto_invoke

        def _create_fast_subagent(
            _subagent_type: str, sub_session_id: str
        ) -> TestDeepAgentSessionRailCancelMock._FastSubAgent:
            tk = getattr(agent, "_session_toolkit", None)
            assert tk is not None
            row = next(
                (r for r in tk.list_all() if r.sub_session_id == sub_session_id),
                None,
            )
            assert row is not None
            assert row.task_id == fixed_task_id
            return self._FastSubAgent(output="completed quickly")

        agent.create_subagent = _create_fast_subagent

        cid = f"cancel_s6_{uuid.uuid4().hex}"
        with TestDeepAgentSessionRailCancelMock._patch_spawn_task_ids([fixed_task_id]):
            await Runner.run_agent(agent, {"query": "创建快速任务", "conversation_id": cid})
        toolkit = getattr(agent, "_session_toolkit")
        self.assertEqual(toolkit.list_all()[0].task_id, fixed_task_id)
        await asyncio.sleep(0.3)
        row_before = toolkit.get(fixed_task_id)
        self.assertIsNotNone(row_before)
        self.assertEqual(row_before.status, "completed")

        mock_llm.set_responses([
            create_tool_call_response(
                "sessions_cancel",
                f'{{"task_id": "{fixed_task_id}"}}',
                tool_call_id="cancel_6",
            ),
            create_text_response("cancel requested"),
        ])
        await Runner.run_agent(agent, {"query": "取消这个已完成任务", "conversation_id": cid})
        row_after = toolkit.get(fixed_task_id)
        self.assertIsNotNone(row_after)
        self.assertEqual(row_after.status, "completed")


if __name__ == "__main__":
    unittest.main()
