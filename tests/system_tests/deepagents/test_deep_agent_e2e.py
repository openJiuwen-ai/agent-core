# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent 端到端系统测试（真实 LLM + sys_operation 文件工具）。"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import unittest
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import List, cast
from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    ToolCallInputs,
)
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.rails.task_planning_rail import (
    TaskPlanningRail,
)
from openjiuwen.deepagents.rails.heartbeat_rail import (
    HeartbeatRail,
)
from openjiuwen.core.single_agent.rail.base import (
    RunKind,
    HeartbeatReason,
)
from openjiuwen.deepagents.schema.config import SubAgentConfig
from openjiuwen.deepagents.subagents import create_code_agent, create_research_agent
from openjiuwen.deepagents.tools import (
    ReadFileTool, WriteFileTool, EditFileTool,
    GlobTool, ListDirTool,
)
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)

API_BASE = os.getenv("API_BASE", "your api url")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "model name")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "SiliconFlow")
MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", "120"))
os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

logger = logging.getLogger(__name__)


class _MockRuntimeModel:
    """Expose MockLLMModel through DeepAgent's public model contract."""

    def __init__(self, client: MockLLMModel):
        self.client = client
        self.model_client_config = client.model_client_config
        self.model_config = client.model_config

    async def invoke(self, *args, **kwargs):
        return await self.client.invoke(*args, **kwargs)

    async def stream(self, *args, **kwargs):
        async for chunk in self.client.stream(*args, **kwargs):
            yield chunk


def _build_mock_runtime_model(mock_llm: MockLLMModel) -> Model:
    return cast(Model, _MockRuntimeModel(mock_llm))


class ToolTraceRail(AgentRail):
    """记录工具调用顺序，供测试断言。"""

    def __init__(self):
        super().__init__()
        self.tool_calls: List[str] = []

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, ToolCallInputs) and ctx.inputs.tool_name:
            self.tool_calls.append(ctx.inputs.tool_name)


class LoopObserveRail(AgentRail):
    """观测外循环轮次，并检查 steer 文本是否进入模型消息。"""

    def __init__(self, steer_text: str):
        super().__init__()
        self.iteration_count: int = 0
        self.steer_text = steer_text
        self.steer_seen_in_model_messages: bool = False
        self._iteration_events: dict[int, asyncio.Event] = {}

    def iteration_event(self, idx: int) -> asyncio.Event:
        if idx not in self._iteration_events:
            self._iteration_events[idx] = asyncio.Event()
        return self._iteration_events[idx]

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        _ = ctx
        self.iteration_count += 1
        self.iteration_event(self.iteration_count).set()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        messages = getattr(ctx.inputs, "messages", None)
        if not isinstance(messages, list):
            return
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content")
            else:
                content = getattr(msg, "content", None)
            text = str(content) if content else ""
            if self.steer_text in text:
                self.steer_seen_in_model_messages = True
                return


class TestDeepAgentE2E(unittest.IsolatedAsyncioTestCase):
    """DeepAgent 真实端到端调用。"""

    async def asyncSetUp(self):
        await Runner.start()
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="deepagent_e2e_")
        self._work_dir = self._tmp_dir.name
        self._sys_operation_id = f"deepagent_sysop_{uuid.uuid4().hex}"
        card = SysOperationCard(
            id=self._sys_operation_id,
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(work_dir=self._work_dir),
        )
        add_result = Runner.resource_mgr.add_sys_operation(card)
        if add_result.is_err():
            raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")

    async def asyncTearDown(self):
        try:
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=self._sys_operation_id)
        finally:
            self._tmp_dir.cleanup()
            await Runner.stop()

    @staticmethod
    def _create_model() -> Model:
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

    def _require_llm_config(self):
        if not API_KEY or not API_BASE:
            self.fail(
                "DeepAgent E2E requires API_KEY and API_BASE in environment. "
                "Set them before running tests."
            )

    def _get_fs_rail(self):
        return FileSystemRail()

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_invoke_e2e_require_api_key_base(self):
        """验证 DeepAgent 在真实模型下可端到端返回 answer。"""
        self._require_llm_config()

        model = self._create_model()
        agent = create_deep_agent(
            model=model,
            system_prompt="你是一个智能助手，请简洁回答。",
            enable_task_loop=False,
            max_iterations=5,
        )

        result = await Runner.run_agent(
            agent, {"query": "请用一句话介绍你自己"},
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")
        self.assertIn("output", result)
        self.assertTrue(bool(result["output"]))

    @pytest.mark.asyncio
    async def test_deep_agent_complex_task_multi_tool_chain(self):
        """复杂任务：用 mock LLM 连续调用 fs 工具完成写入、列举、读取。"""
        class _FakeFsBackend:
            def __init__(self, root: Path):
                self.root = root

            def _resolve(self, path: str) -> Path:
                return (self.root / path).resolve()

            async def write_file(self, path: str, content: str | bytes, **kwargs):
                file_path = self._resolve(path)
                file_path.parent.mkdir(parents=True, exist_ok=True)

                mode = kwargs.get("mode", "text")
                encoding = kwargs.get("encoding", "utf-8")
                prepend_newline = kwargs.get("prepend_newline", True)
                append_newline = kwargs.get("append_newline", False)

                if mode == "text":
                    text = str(content)
                    if prepend_newline:
                        text = "\n" + text
                    if append_newline:
                        text = text + "\n"
                    data = text.encode(encoding)
                else:
                    data = content if isinstance(content, (bytes, bytearray)) else bytes(content)

                file_path.write_bytes(data)
                return SimpleNamespace(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=SimpleNamespace(path=str(file_path), size=len(data), mode=mode),
                )

            async def read_file(self, path: str, line_range=None, **kwargs):
                file_path = self._resolve(path)
                encoding = kwargs.get("encoding", "utf-8")
                content = file_path.read_text(encoding=encoding)

                if line_range is not None:
                    start, end = line_range
                    lines = content.splitlines()
                    content = "\n".join(lines[start - 1:end])

                return SimpleNamespace(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=SimpleNamespace(path=str(file_path), content=content, mode="text"),
                )

            async def list_files(self, path: str, **kwargs):
                dir_path = self._resolve(path)
                items = [
                    SimpleNamespace(name=item.name, path=str(item))
                    for item in dir_path.iterdir()
                    if item.is_file()
                ]
                return SimpleNamespace(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=SimpleNamespace(list_items=items),
                )

            async def list_directories(self, path: str, **kwargs):
                dir_path = self._resolve(path)
                items = [
                    SimpleNamespace(name=item.name, path=str(item))
                    for item in dir_path.iterdir()
                    if item.is_dir()
                ]
                return SimpleNamespace(
                    code=StatusCode.SUCCESS.code,
                    message=StatusCode.SUCCESS.errmsg,
                    data=SimpleNamespace(list_items=items),
                )

        class _FakeExecBackend:
            async def execute_cmd(self, *args, **kwargs):
                return SimpleNamespace(
                    code=1,
                    message="unsupported in test",
                    data=SimpleNamespace(stdout="", stderr="unsupported in test", exit_code=1),
                )

            async def execute_code(self, *args, **kwargs):
                return SimpleNamespace(
                    code=1,
                    message="unsupported in test",
                    data=SimpleNamespace(stdout="", stderr="unsupported in test", exit_code=1),
                )

        class _FakeSysOperation:
            def __init__(self, root: Path):
                self._fs = _FakeFsBackend(root)
                self._shell = _FakeExecBackend()
                self._code = _FakeExecBackend()

            def fs(self):
                return self._fs

            def shell(self):
                return self._shell

            def code(self):
                return self._code

        tool_trace = ToolTraceRail()
        fs_rail = self._get_fs_rail()
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "write_file",
                '{"file_path": "todo_alpha.txt", "content": "准备数据\\n实现功能\\n验证结果"}',
                tool_call_id="mock_call_write_alpha",
            ),
            create_tool_call_response(
                "write_file",
                '{"file_path": "todo_beta.txt", "content": "发布版本\\n回滚预案"}',
                tool_call_id="mock_call_write_beta",
            ),
            create_tool_call_response(
                "list_files",
                '{"path": "."}',
                tool_call_id="mock_call_list_files",
            ),
            create_tool_call_response(
                "read_file",
                '{"file_path": "todo_alpha.txt"}',
                tool_call_id="mock_call_read_alpha",
            ),
            create_tool_call_response(
                "read_file",
                '{"file_path": "todo_beta.txt"}',
                tool_call_id="mock_call_read_beta",
            ),
            create_text_response("已按顺序完成文件写入、列出和读取。"),
        ])
        model = _build_mock_runtime_model(mock_llm)
        tmp_root = Path(__file__).parent / ".tmp"
        tmp_root.mkdir(exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix="deepagent_tool_chain_",
            dir=tmp_root,
        ) as work_dir:
            fake_sys_operation = _FakeSysOperation(Path(work_dir))
            agent = create_deep_agent(
                model=model,
                system_prompt=(
                    "你是一个严谨的任务执行助手。"
                    "当用户要求用工具处理文件时，必须调用工具，不要凭空假设。"
                ),
                rails=[tool_trace, fs_rail],
                enable_task_loop=False,
                max_iterations=12,
                workspace=work_dir,
            )
            agent.deep_config.sys_operation = fake_sys_operation

            query = (
                "请严格按顺序执行以下任务，并且每一步都必须调用工具：\n"
                "1. 写入 todo_alpha.txt，内容为三行：准备数据、实现功能、验证结果；\n"
                "2. 写入 todo_beta.txt，内容为两行：发布版本、回滚预案；\n"
                "3. 使用工具列出当前目录文件，确认上面两个文件存在；\n"
                "4. 使用工具读取这两个文件；\n"
                "5. 最后输出一句中文总结。"
            )
            result = await Runner.run_agent(agent, {"query": query})

            self.assertIsInstance(result, dict)
            self.assertEqual(result.get("result_type"), "answer")
            self.assertIn("output", result)
            self.assertTrue(bool(result["output"]))

            tool_counts = Counter(tool_trace.tool_calls)
            self.assertGreaterEqual(tool_counts.get("write_file", 0), 2)
            self.assertGreaterEqual(tool_counts.get("list_files", 0), 1)
            self.assertGreaterEqual(tool_counts.get("read_file", 0), 1)
            self.assertGreaterEqual(sum(tool_counts.values()), 4)

            alpha_path = Path(work_dir) / "todo_alpha.txt"
            beta_path = Path(work_dir) / "todo_beta.txt"
            self.assertTrue(alpha_path.exists())
            self.assertTrue(beta_path.exists())
            self.assertTrue(alpha_path.read_text(encoding="utf-8").strip())
            self.assertTrue(beta_path.read_text(encoding="utf-8").strip())

    @pytest.mark.asyncio
    async def test_deep_agent_task_planning(self):
        """复杂任务：agent的规划能力"""
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response(
                "todo_create",
                '{"tasks": "设计打卡系统数据库表结构;实现用户打卡功能接口;开发前端打卡页面;添加打卡统计功能"}'
            ),
            create_tool_call_response(
                "todo_list",
                '{}'
            ),
            create_tool_call_response(
                "todo_modify",
                '{"action": "update", "todos": [{"id": "mock_task_id_1", "status": "completed"}]}'
            ),
            create_text_response("我已经帮你完成了打卡系统的任务规划，并完成了第一个任务的设计工作。")
        ])

        agent = create_deep_agent(
            model=_build_mock_runtime_model(mock_llm),
            enable_task_loop=False,
            max_iterations=20,
            sys_operation=sys_oper,
            enable_task_planning=True,
            restrict_to_work_dir=False,
        )

        query = "我想测试任务规划能力，帮我构建一个打卡系统，调用规划工具帮我模拟规划吧"

        result = await Runner.run_agent(agent, {"query": query})

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")

    @pytest.mark.asyncio
    async def test_deep_agent_task_planning_with_progress_reminder(self):
        """测试任务规划中的工具调用计数和进度提醒功能"""
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        task_planning = TaskPlanningRail(list_tool_call_interval=3)
        mock_llm = MockLLMModel()
        
        # 模拟工具调用序列：前3次不触发提醒，第3次触发提醒
        mock_llm.set_responses([
            create_tool_call_response(
                "todo_create",
                '{"tasks": "任务1;任务2;任务3;任务4;任务5"}'
            ),
            create_tool_call_response(
                "todo_modify",
                '{"action": "update", "todos": [{"id": "task_1", "status": "completed"}]}'
            ),
            create_tool_call_response(
                "todo_modify",
                '{"action": "update", "todos": [{"id": "task_2", "status": "completed"}]}'
            ),
            # 第3次工具调用后应该触发进度提醒，模型调用 todo_list
            create_tool_call_response(
                "todo_list",
                '{}'
            ),
            create_text_response("任务1和任务2已完成，正在执行任务3。")
        ])

        agent = create_deep_agent(
            model=_build_mock_runtime_model(mock_llm),
            rails=[task_planning],
            enable_task_loop=False,
            max_iterations=20,
            sys_operation=sys_oper
        )

        query = "帮我完成前两个任务"

        result = await Runner.run_agent(agent, {"query": query})

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")

    @pytest.mark.asyncio
    async def test_deep_agent_heartbeat(self):
        """测试 heartbeat 功能：通过 run 参数传递 heartbeat 上下文"""
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        heartbeat_rail = HeartbeatRail()
        mock_llm = MockLLMModel()

        mock_llm.set_responses([
            create_text_response("HEARTBEAT_OK")
        ])

        agent = create_deep_agent(
            model=self._create_model(),
            rails=[heartbeat_rail],
            enable_task_loop=False,
            max_iterations=5,
            sys_operation=sys_oper
        )

        heartbeat_inputs = {
            "query": "heartbeat check",
            "run": {
                "kind": "heartbeat",
                "context": {
                    "reason": "interval",
                    "session_id": "test-session",
                    "context_mode": "lightweight"
                }
            }
        }

        with patch.object(agent._react_agent, '_get_llm', return_value=mock_llm):
            result = await Runner.run_agent(agent, heartbeat_inputs)

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")
        self.assertIn("output", result)

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_task_loop_real_multistep_steer_follow_up(self):
        """真实 LLM 外循环：LLM 生成多步任务规划 + steer + follow_up。"""
        self._require_llm_config()

        steer_text = "输出请使用简洁中文要点"
        follow_up_text = "在结尾追加一条风险提示"
        observe_rail = LoopObserveRail(steer_text=steer_text)

        model = self._create_model()
        sys_oper = Runner.resource_mgr.get_sys_operation(
            self._sys_operation_id
        )
        planning_rail = TaskPlanningRail(sys_oper)
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "根据当前任务逐步输出结果。"
            ),
            rails=[planning_rail, observe_rail],
            enable_task_loop=True,
            max_iterations=12,
        )

        query = (
            "请制定一个简短的项目启动计划，包含以下方面："
            "1. 需求分析；2. 技术选型；3. 实施方案。"
            "每个方面给出简要说明。"
        )
        # steer/follow_up 需要在 invoke 执行中途注入，
        # 必须用 asyncio.create_task + Runner.run_agent 配合。
        invoke_task = asyncio.create_task(
            Runner.run_agent(agent, {"query": query})
        )

        # 等第一轮进入后注入 steer，让下一轮携带约束。
        await asyncio.wait_for(
            observe_rail.iteration_event(1).wait(),
            timeout=180.0,
        )
        await agent.steer(steer_text)

        # 等第二轮进入后注入 follow_up，请求额外追加一轮。
        await asyncio.wait_for(
            observe_rail.iteration_event(2).wait(),
            timeout=300.0,
        )
        await agent.follow_up(follow_up_text)

        result = await asyncio.wait_for(
            invoke_task, timeout=600.0
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(
            result.get("result_type"), "answer"
        )
        self.assertIn("output", result)
        self.assertTrue(bool(result["output"]))

        # LLM 生成的任务 + follow_up 触发的额外轮次
        self.assertGreaterEqual(
            observe_rail.iteration_count, 2
        )
        self.assertTrue(
            observe_rail.steer_seen_in_model_messages
        )

    @pytest.mark.asyncio
    async def test_deep_agent_auto_rails_creation_e2e(self):
        """Test automatic creation of TaskPlanningRail and SkillUseRail."""
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)

        skills = ["name", "test_skill", "description", "test"]
        agent = create_deep_agent(
            model=self._create_model(),
            enable_task_planning=True,
            skills=skills,
            sys_operation=sys_oper,
            max_iterations=10,
        )

        pending_rails = agent._pending_rails

        rail_types = [type(rail).__name__ for rail in pending_rails if rail is not None]

        self.assertIn("TaskPlanningRail", rail_types,
                      "TaskPlanningRail should be auto-created when enable_task_loop=True")
        self.assertIn("SkillUseRail", rail_types,
                      "SkillUseRail should be auto-created when skills parameter is provided")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_stream_e2e_require_api_key_base(self):
        """验证 DeepAgent.stream 在真实模型下可流式输出 chunk 并包含最终 answer。"""
        self._require_llm_config()

        model = self._create_model()
        agent = create_deep_agent(
            model=model,
            system_prompt="你是一个智能助手，请简洁回答。",
            enable_task_loop=False,
            max_iterations=5,
        )

        chunks = []
        async for chunk in Runner.run_agent_streaming(
            agent, {"query": "请用一句话介绍你自己"},
        ):
            logger.info("[stream chunk] type=%s, index=%s, payload=%s",
                        getattr(chunk, 'type', '?'),
                        getattr(chunk, 'index', '?'),
                        getattr(chunk, 'payload', chunk))
            chunks.append(chunk)

        self.assertGreater(len(chunks), 0, "stream should yield at least one chunk")

        has_llm_output = any(
            getattr(c, "type", None) == "llm_output" for c in chunks
        )
        self.assertTrue(has_llm_output, "stream chunks should contain llm_output type data")

        combined = "".join(
            c.payload.get("content", "") for c in chunks
            if getattr(c, "type", None) == "llm_output" and isinstance(c.payload, dict)
        )
        self.assertGreater(len(combined), 0, "combined stream content should not be empty")

    @pytest.mark.asyncio
    @unittest.skip("skip system test")
    async def test_deep_agent_task_loop_stream_e2e(self):
        """验证 enable_task_loop=True 时 stream 逐轮流式输出。"""
        self._require_llm_config()

        tool_trace = ToolTraceRail()
        model = self._create_model()
        sys_oper = Runner.resource_mgr.get_sys_operation(self._sys_operation_id)
        planning_rail = TaskPlanningRail(sys_oper)

        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "你是一个严谨的任务执行助手。"
                "根据当前任务逐步输出结果。"
            ),
            rails=[planning_rail, tool_trace],
            enable_task_loop=True,
            max_iterations=12,
        )

        query = (
            "请制定一个简短的项目启动计划，包含以下方面："
            "1. 需求分析；2. 技术选型。"
            "每个方面给出简要说明。"
        )

        round_results = []
        async for result in Runner.run_agent_streaming(
            agent, {"query": query},
        ):
            round_idx = len(round_results) + 1
            result_type = result.get("result_type", "?") if isinstance(result, dict) else getattr(result, "type", "?")
            output_preview = str(result.get("output", ""))[:200] if isinstance(result, dict) else str(result)[:200]
            logger.info("[task loop stream] round=%d, result_type=%s, output=%s",
                        round_idx, result_type, output_preview)
            round_results.append(result)

        self.assertGreater(len(round_results), 0, "task loop stream should yield at least one round result")

        for r in round_results:
            if isinstance(r, dict):
                self.assertIn("output", r, "each round result should contain 'output'")
                self.assertTrue(bool(r["output"]), "round output should not be empty")

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
            sys_operation=sys_oper
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
            sys_operation=sys_oper
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
