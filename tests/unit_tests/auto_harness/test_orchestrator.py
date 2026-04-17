# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_orchestrator — AutoHarnessOrchestrator 单元测试。"""

from __future__ import annotations

import asyncio
import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from openjiuwen.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
)
from openjiuwen.auto_harness.infra.parsers import (
    parse_tasks,
    parse_learnings,
    extract_text,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CycleResult,
    Experience,
    ExperienceType,
    OptimizationTask,
    TaskStatus,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)

_ORCH_MOD = "openjiuwen.auto_harness.orchestrator"


async def _collect(agen):
    """消费 async generator，返回 list。"""
    items = []
    async for item in agen:
        items.append(item)
    return items


async def _noop_agen(*args, **kwargs):
    """空 async generator。"""
    return
    yield  # noqa: RET504


async def _fake_assess_stream(
    config, experience_store
):
    """mock run_assess_stream。"""
    yield OutputSchema(
        type="message", index=0,
        payload={"content": "# Report"},
    )


class TestOrchestratorRunSession(
    IsolatedAsyncioTestCase,
):
    def _make_orchestrator(self, tmpdir: str):
        cfg = AutoHarnessConfig(
            data_dir=tmpdir,
            session_budget_secs=3600.0,
            task_timeout_secs=600.0,
        )
        return AutoHarnessOrchestrator(
            cfg, agent=None,
        )

    @patch(f"{_ORCH_MOD}.run_learnings", new=_noop_agen)
    @patch(f"{_ORCH_MOD}.run_plan_stream", new=_noop_agen)
    @patch(
        f"{_ORCH_MOD}.run_assess",
        new_callable=AsyncMock,
        return_value="# Report\nOK",
    )
    @patch(
        f"{_ORCH_MOD}.run_assess_stream",
        new=_fake_assess_stream,
    )
    async def test_no_tasks_runs_assess_then_plan(
        self, mock_assess,
    ):
        """tasks=None 时走 assess→plan 流程。"""
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()
            results = await orch.run_session(
                tasks=None,
            )
            assert results == []

    @patch(f"{_ORCH_MOD}.run_learnings", new=_noop_agen)
    async def test_assess_and_plan_use_readonly_snapshot(
        self,
    ):
        """Phase A 应基于 origin/base 的只读快照。"""
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                workspace="/repo/local",
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )
            original_workspace = cfg.workspace
            seen_workspaces = []

            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(
                config, experience_store,
            ):
                seen_workspaces.append(config.workspace)
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "# Report"},
                )

            async def _fake_plan_stream(
                config, assessment, experience_store,
            ):
                seen_workspaces.append(config.workspace)
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={
                        "content": (
                            '```json\n[{"topic":"t1"}]\n```'
                        )
                    },
                )

            async def _fake_isolated(task):
                orch._results.append(
                    CycleResult(success=True),
                )
                return
                yield  # noqa: RET504

            orch._run_task_isolated_stream = (
                _fake_isolated
            )

            with patch(
                f"{_ORCH_MOD}.run_assess_stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_ORCH_MOD}.run_plan_stream",
                new=_fake_plan_stream,
            ):
                await orch.run_session(tasks=None)

            assert seen_workspaces == [
                f"{d}/worktrees/assess",
                f"{d}/worktrees/assess",
            ]
            assert cfg.workspace == original_workspace
            orch.worktree_mgr.cleanup.assert_awaited_once_with(
                f"{d}/worktrees/assess"
            )

    @patch(f"{_ORCH_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_ORCH_MOD}.run_assess",
        new_callable=AsyncMock,
        return_value="report",
    )
    @patch(
        f"{_ORCH_MOD}.run_assess_stream",
        new=_fake_assess_stream,
    )
    async def test_no_tasks_with_plan_results(
        self, mock_assess,
    ):
        """plan 生成任务后执行 implement。"""
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            plan_json = (
                '```json\n[{"topic":"planned-t1",'
                '"description":"d"}]\n```'
            )

            async def _fake_plan_stream(
                config, assessment, experience_store,
            ):
                yield OutputSchema(
                    type="llm_output", index=0,
                    payload={"content": plan_json},
                )

            async def _fake_isolated(task):
                orch._results.append(
                    CycleResult(success=True),
                )
                return
                yield  # noqa: RET504

            orch._run_task_isolated_stream = (
                _fake_isolated
            )

            with patch(
                f"{_ORCH_MOD}.run_plan_stream",
                new=_fake_plan_stream,
            ):
                results = await orch.run_session(
                    tasks=None,
                )
            assert len(results) == 1

    @patch(f"{_ORCH_MOD}.run_learnings", new=_noop_agen)
    @patch(
        f"{_ORCH_MOD}.run_assess",
        new_callable=AsyncMock,
        return_value="report",
    )
    @patch(
        f"{_ORCH_MOD}.run_assess_stream",
        new=_fake_assess_stream,
    )
    async def test_plan_artifact_saved_when_no_tasks(
        self, mock_assess,
    ):
        """原始 plan 输出应写入 runs_dir，便于排查解析失败。"""
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_plan_stream(
                config, assessment, experience_store,
            ):
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "not json plan"},
                )

            chunks = []
            with patch(
                f"{_ORCH_MOD}.run_plan_stream",
                new=_fake_plan_stream,
            ):
                chunks = await _collect(
                    orch.run_session_stream(tasks=None)
                )

            plan_path = f"{d}/runs/latest_plan.md"
            with open(plan_path, encoding="utf-8") as fh:
                assert fh.read() == "not json plan"
            msg_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert any(
                "规划原始输出已保存" in msg
                for msg in msg_chunks
            )

    @patch(f"{_ORCH_MOD}.run_learnings", new=_noop_agen)
    async def test_caps_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.max_tasks_per_session = 2

            async def _fake_isolated(task):
                orch._results.append(
                    CycleResult(success=True),
                )
                return
                yield  # noqa: RET504

            orch._run_task_isolated_stream = (
                _fake_isolated
            )
            tasks = [
                OptimizationTask(topic=f"t{i}")
                for i in range(5)
            ]
            results = await orch.run_session(
                tasks=tasks,
            )
            assert len(results) == 2

    @patch(f"{_ORCH_MOD}.run_learnings", new=_noop_agen)
    async def test_budget_stops_early(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                cost_limit_usd=1.0,
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )
            orch.budget.add_cost(2.0)
            tasks = [OptimizationTask(topic="t1")]
            results = await orch.run_session(
                tasks=tasks,
            )
            assert results == []


class TestOrchestratorCycle(IsolatedAsyncioTestCase):
    async def test_experience_is_isolated_by_data_dir(
        self,
    ):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            cfg1 = AutoHarnessConfig(data_dir=d1)
            cfg2 = AutoHarnessConfig(data_dir=d2)

            orch1 = AutoHarnessOrchestrator(
                cfg1, agent=None,
            )
            orch2 = AutoHarnessOrchestrator(
                cfg2, agent=None,
            )

            await orch1.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic="shared-topic",
                    summary="from first session",
                    outcome="recorded",
                )
            )

            hits_1 = await orch1.experience_store.search(
                "shared-topic"
            )
            hits_2 = await orch2.experience_store.search(
                "shared-topic"
            )

            assert len(hits_1) == 1
            assert hits_1[0].summary == "from first session"
            assert hits_2 == []

    async def test_success_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                git_remote="myfork",
                fork_owner="TestOwner",
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            orch.worktree_mgr.prepare = AsyncMock(
                return_value=f"{d}/worktrees/wt1",
            )
            orch.worktree_mgr.cleanup = AsyncMock()
            orch.git.list_dirty_files = AsyncMock(
                return_value=[]
            )

            async def _fake_wt(
                config, task, related, **kwargs,
            ):
                assert kwargs["agent"] is fake_agent
                assert kwargs["commit_agent"] is fake_commit_agent
                result_holder = kwargs["result_holder"]
                result_holder.append(CycleResult(
                    success=True,
                    pr_url="http://pr/1",
                ))
                yield OutputSchema(
                    type="message", index=0,
                    payload={"content": "done"},
                )

            fake_agent = object()
            fake_commit_agent = object()
            with patch(
                f"{_ORCH_MOD}.run_in_worktree_stream",
                new=_fake_wt,
            ), patch(
                "openjiuwen.auto_harness.agent.create_auto_harness_agent",
                return_value=fake_agent,
            ) as mock_create_agent, patch(
                "openjiuwen.auto_harness.agent.create_commit_agent",
                return_value=fake_commit_agent,
            ) as mock_create_commit_agent:
                task = OptimizationTask(
                    topic="test task",
                )
                await _collect(
                    orch._run_cycle_stream(task),
                )

            result = orch._last_cycle_result
            assert result.success is True
            assert result.pr_url == "http://pr/1"
            _, kwargs = mock_create_agent.call_args
            assert kwargs["workspace_override"] == (
                f"{d}/worktrees/wt1"
            )
            assert "edit_safety_rail" in kwargs
            _, commit_kwargs = mock_create_commit_agent.call_args
            assert commit_kwargs["workspace_override"] == (
                f"{d}/worktrees/wt1"
            )
            orch.worktree_mgr.cleanup\
                .assert_awaited_once()

    async def test_ci_failure_triggers_revert(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            orch.worktree_mgr.prepare = AsyncMock(
                return_value=f"{d}/worktrees/wt2",
            )
            orch.worktree_mgr.cleanup = AsyncMock()
            orch.git.list_dirty_files = AsyncMock(
                return_value=[]
            )

            async def _fake_wt(
                config, task, related, **kwargs,
            ):
                result_holder = kwargs["result_holder"]
                result_holder.append(CycleResult(
                    reverted=True,
                    error_log="lint error",
                ))
                yield OutputSchema(
                    type="message", index=0,
                    payload={"content": "reverted"},
                )

            with patch(
                f"{_ORCH_MOD}.run_in_worktree_stream",
                new=_fake_wt,
            ), patch(
                "openjiuwen.auto_harness.agent.create_auto_harness_agent",
                return_value=object(),
            ):
                task = OptimizationTask(
                    topic="fail ci",
                )
                await _collect(
                    orch._run_cycle_stream(task),
                )

            result = orch._last_cycle_result
            assert result.success is False
            assert result.reverted is True

    async def test_each_task_creates_a_fresh_agent_for_its_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            orch.worktree_mgr.prepare = AsyncMock(
                side_effect=[
                    f"{d}/worktrees/wt1",
                    f"{d}/worktrees/wt2",
                ],
            )
            orch.worktree_mgr.cleanup = AsyncMock()
            orch.git.list_dirty_files = AsyncMock(
                return_value=[]
            )

            created_agents = [object(), object()]
            created_commit_agents = [object(), object()]
            seen_agents = []
            seen_commit_agents = []

            async def _fake_wt(
                config, task, related, **kwargs,
            ):
                seen_agents.append(kwargs["agent"])
                seen_commit_agents.append(
                    kwargs["commit_agent"]
                )
                kwargs["result_holder"].append(
                    CycleResult(success=True)
                )
                yield OutputSchema(
                    type="message", index=0,
                    payload={"content": task.topic},
                )

            with patch(
                f"{_ORCH_MOD}.run_in_worktree_stream",
                new=_fake_wt,
            ), patch(
                "openjiuwen.auto_harness.agent.create_auto_harness_agent",
                side_effect=created_agents,
            ) as mock_create_agent, patch(
                "openjiuwen.auto_harness.agent.create_commit_agent",
                side_effect=created_commit_agents,
            ) as mock_create_commit_agent:
                await _collect(
                    orch._run_cycle_stream(
                        OptimizationTask(topic="t1")
                    ),
                )
                await _collect(
                    orch._run_cycle_stream(
                        OptimizationTask(topic="t2")
                    ),
                )

            assert seen_agents == created_agents
            assert seen_commit_agents == created_commit_agents
            workspaces = [
                call.kwargs["workspace_override"]
                for call in mock_create_agent.call_args_list
            ]
            assert workspaces == [
                f"{d}/worktrees/wt1",
                f"{d}/worktrees/wt2",
            ]
            commit_workspaces = [
                call.kwargs["workspace_override"]
                for call in mock_create_commit_agent.call_args_list
            ]
            assert commit_workspaces == [
                f"{d}/worktrees/wt1",
                f"{d}/worktrees/wt2",
            ]


class TestOrchestratorTaskIsolation(
    IsolatedAsyncioTestCase,
):
    async def test_timeout_handling(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                task_timeout_secs=0.01,
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            async def slow_cycle(_task):
                await asyncio.sleep(10)
                yield  # async generator

            orch._run_cycle_stream = slow_cycle
            task = OptimizationTask(topic="slow")
            await _collect(
                orch._run_task_isolated_stream(task),
            )
            assert orch._results[-1].error == "timeout"
            assert task.status == TaskStatus.TIMEOUT

    async def test_exception_handling(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            async def boom(_task):
                raise RuntimeError("kaboom")
                yield  # noqa: RET504

            orch._run_cycle_stream = boom
            task = OptimizationTask(topic="boom")
            await _collect(
                orch._run_task_isolated_stream(task),
            )
            assert "kaboom" in orch._results[-1].error
            assert task.status == TaskStatus.FAILED


class TestOrchestratorFixTaskCancellation(
    IsolatedAsyncioTestCase,
):
    async def test_fix_task_cancelled_on_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                task_timeout_secs=0.05,
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            fix_started = asyncio.Event()
            fix_cancelled = asyncio.Event()

            async def _slow_cycle(_task):
                yield orch._msg("准备就绪")
                fix_started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    fix_cancelled.set()
                    raise
                yield  # noqa: RET504

            orch._run_cycle_stream = _slow_cycle
            task = OptimizationTask(
                topic="fix-cancel",
            )
            await _collect(
                orch._run_task_isolated_stream(task),
            )

            assert orch._results[-1].error == "timeout"
            await asyncio.sleep(0.05)
            assert fix_cancelled.is_set()


class TestOrchestratorStream(IsolatedAsyncioTestCase):
    @patch(f"{_ORCH_MOD}.run_learnings", new=_noop_agen)
    async def test_stream_yields_progress_messages(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                session_budget_secs=3600.0,
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            async def _fake_isolated(task):
                orch._results.append(
                    CycleResult(success=True),
                )
                yield orch._msg("fake progress")

            orch._run_task_isolated_stream = (
                _fake_isolated
            )

            tasks = [OptimizationTask(topic="t1")]
            chunks = await _collect(
                orch.run_session_stream(tasks=tasks),
            )

            msg_chunks = [
                c for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert len(msg_chunks) >= 1
            assert "会话启动" in (
                msg_chunks[0].payload["content"]
            )

    async def test_implement_stream_forwards_agent(self):
        """run_implement_stream 透传 agent chunks。"""
        from openjiuwen.auto_harness.stages.implement import (
            run_implement_stream,
        )

        fake_chunks = [
            OutputSchema(
                type="llm_output", index=0,
                payload={"content": "hello"},
            ),
            OutputSchema(
                type="llm_output", index=0,
                payload={"content": " world"},
            ),
        ]

        class _FakeAgent:
            async def stream(self, inputs):
                for c in fake_chunks:
                    yield c

        task = OptimizationTask(topic="test")
        chunks = await _collect(
            run_implement_stream(
                _FakeAgent(), task, [],
            ),
        )
        assert len(chunks) == 2
        assert chunks[0].type == "llm_output"
        assert (
            chunks[0].payload["content"] == "hello"
        )

    async def test_implement_stream_prompt_forbids_git_commit(
        self,
    ):
        """实现阶段 prompt 应明确禁止提前提交。"""
        from openjiuwen.auto_harness.stages.implement import (
            run_implement_stream,
        )

        captured = {}

        class _FakeAgent:
            async def stream(self, inputs):
                captured.update(inputs)
                if False:
                    yield None

        await _collect(
            run_implement_stream(
                _FakeAgent(),
                OptimizationTask(
                    topic="test",
                    files=["openjiuwen/auto_harness/schema.py"],
                ),
                [],
            ),
        )
        prompt = captured["query"]
        assert "严禁执行 git add、git commit" in prompt
        assert "提交只允许在后续独立 commit phase 中进行" in prompt


class TestParseTasks(IsolatedAsyncioTestCase):
    def test_parse_json_block(self):
        raw = (
            '一些文字\n```json\n'
            '[{"topic":"t1","description":"d1",'
            '"files":["a.py"],'
            '"expected_effect":"e1"}]\n```\n'
        )
        tasks = parse_tasks(raw)
        assert len(tasks) == 1
        assert tasks[0].topic == "t1"
        assert tasks[0].files == ["a.py"]

    def test_parse_bare_json(self):
        raw = '[{"topic":"bare","description":"d"}]'
        tasks = parse_tasks(raw)
        assert len(tasks) == 1
        assert tasks[0].topic == "bare"

    def test_parse_empty(self):
        assert parse_tasks("no json here") == []

    def test_parse_invalid_json(self):
        raw = "```json\n{broken\n```"
        assert parse_tasks(raw) == []

    def test_parse_missing_topic(self):
        raw = '[{"description":"no topic field"}]'
        assert parse_tasks(raw) == []


class TestParseLearnings(IsolatedAsyncioTestCase):
    def testparse_learnings_json(self):
        raw = (
            '```json\n[{"type":"insight",'
            '"topic":"t","summary":"s"}]\n```'
        )
        result = parse_learnings(raw)
        assert len(result) == 1
        assert result[0]["topic"] == "t"

    def testparse_learnings_empty(self):
        raw = "```json\n[]\n```"
        assert parse_learnings(raw) == []

    def testparse_learnings_no_json(self):
        assert parse_learnings("nothing") == []


class TestExtractText(IsolatedAsyncioTestCase):
    def test_extract_from_output_schema(self):
        chunk = OutputSchema(
            type="message", index=0,
            payload={"content": "hello"},
        )
        assert extract_text(chunk) == "hello"

    def test_extract_no_payload(self):
        class _Bare:
            pass

        assert extract_text(_Bare()) == ""

    def test_extract_non_dict_payload(self):
        class _Obj:
            payload = "string"

        assert extract_text(_Obj()) == ""
