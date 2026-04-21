# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AutoHarnessOrchestrator unit tests."""

from __future__ import annotations

import asyncio
import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from openjiuwen.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
)
from openjiuwen.auto_harness.pipelines import (
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline import (
    PRTaskPipeline,
    prepare_task_runtime,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    CycleResult,
    OptimizationTask,
    TaskStatus,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)

_ASSESS_STAGE_MOD = (
    "openjiuwen.auto_harness.stages.assess"
)
_PLAN_STAGE_MOD = "openjiuwen.auto_harness.stages.plan"
_LEARNINGS_STAGE_MOD = (
    "openjiuwen.auto_harness.stages.learnings"
)
_TASK_PIPELINE_MOD = (
    "openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline"
)


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


async def _noop_agen(*args, **kwargs):
    del args, kwargs
    return
    yield  # noqa: RET504


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

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_session_selects_meta_pipeline_before_running(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)

            async def _fake_pipeline_stream(
                pipeline_name,
            ):
                assert pipeline_name == META_EVOLVE_PIPELINE
                assert orch.runtime.selected_pipeline == (
                    META_EVOLVE_PIPELINE
                )
                selection = orch.artifacts.require(
                    "pipeline_selection"
                )
                assert selection.pipeline_name == (
                    META_EVOLVE_PIPELINE
                )
                yield orch._msg("pipeline running")

            orch._run_pipeline_stream = _fake_pipeline_stream
            chunks = await _collect(
                orch.run_session_stream(tasks=None)
            )

            msg_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert (
                f"Session pipeline: {META_EVOLVE_PIPELINE}"
                in msg_chunks
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_assess_and_plan_use_readonly_snapshot(
        self,
    ):
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
                del experience_store
                seen_workspaces.append(config.workspace)
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "# Report"},
                )

            async def _fake_plan_stream(
                config, assessment, experience_store,
            ):
                del assessment, experience_store
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

            async def _fake_isolated(_orch, task):
                assert task.topic == "t1"
                orch._results.append(
                    CycleResult(success=True),
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.run_assess_stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.run_plan_stream",
                new=_fake_plan_stream,
            ), patch.object(
                PRTaskPipeline,
                "run_isolated_stream",
                new=_fake_isolated,
            ):
                await _collect(
                    orch.run_session_stream(tasks=None)
                )

            assert seen_workspaces == [
                f"{d}/worktrees/assess",
                f"{d}/worktrees/assess",
            ]
            assert cfg.workspace == original_workspace
            orch.worktree_mgr.cleanup.assert_awaited_once_with(
                f"{d}/worktrees/assess"
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_direct_tasks_skip_assess_and_plan(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)

            async def _fake_isolated(_orch, task):
                orch._results.append(
                    CycleResult(success=True, summary=task.topic),
                )
                yield orch._msg(task.topic)

            with patch.object(
                PRTaskPipeline,
                "run_isolated_stream",
                new=_fake_isolated,
            ), patch(
                f"{_ASSESS_STAGE_MOD}.run_assess_stream",
                new=AsyncMock(side_effect=AssertionError),
            ), patch(
                f"{_PLAN_STAGE_MOD}.run_plan_stream",
                new=AsyncMock(side_effect=AssertionError),
            ):
                chunks = await _collect(
                    orch.run_session_stream(
                        tasks=[OptimizationTask(topic="t1")]
                    )
                )

            msg_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert "t1" in msg_chunks

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_session_stream_passthroughs_assess_and_plan_chunks(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(
                config, experience_store,
            ):
                del config, experience_store
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "# streamed assess"},
                )

            async def _fake_plan_stream(
                config, assessment, experience_store,
            ):
                del config, assessment, experience_store
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={
                        "content": (
                            '```json\n[{"topic":"t1"}]\n```'
                        )
                    },
                )

            async def _fake_isolated(_orch, task):
                orch._results.append(
                    CycleResult(success=True, summary=task.topic),
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.run_assess_stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.run_plan_stream",
                new=_fake_plan_stream,
            ), patch.object(
                PRTaskPipeline,
                "run_isolated_stream",
                new=_fake_isolated,
            ):
                chunks = await _collect(
                    orch.run_session_stream(tasks=None)
                )

            llm_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "llm_output"
            ]
            message_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert "# streamed assess" in llm_chunks
            assert (
                '```json\n[{"topic":"t1"}]\n```'
                in llm_chunks
            )
            assert "# streamed assess" not in message_chunks
            assert (
                '```json\n[{"topic":"t1"}]\n```'
                not in message_chunks
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_plan_stage_keeps_only_first_planned_task(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.worktree_mgr.prepare_readonly_snapshot = (
                AsyncMock(return_value=f"{d}/worktrees/assess")
            )
            orch.worktree_mgr.cleanup = AsyncMock()

            async def _fake_assess_stream(
                config, experience_store,
            ):
                del config, experience_store
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": "# Report"},
                )

            async def _fake_plan_stream(
                config, assessment, experience_store,
            ):
                del config, assessment, experience_store
                yield OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={
                        "content": (
                            "```json\n"
                            "["
                            '{"topic":"t1"},'
                            '{"topic":"t2"}'
                            "]\n```"
                        )
                    },
                )

            seen_topics = []

            async def _fake_isolated(_orch, task):
                seen_topics.append(task.topic)
                orch._results.append(
                    CycleResult(success=True, summary=task.topic),
                )
                return
                yield  # noqa: RET504

            with patch(
                f"{_ASSESS_STAGE_MOD}.run_assess_stream",
                new=_fake_assess_stream,
            ), patch(
                f"{_PLAN_STAGE_MOD}.run_plan_stream",
                new=_fake_plan_stream,
            ), patch.object(
                PRTaskPipeline,
                "run_isolated_stream",
                new=_fake_isolated,
            ):
                chunks = await _collect(
                    orch.run_session_stream(tasks=None)
                )

            assert seen_topics == ["t1"]
            message_chunks = [
                c.payload["content"]
                for c in chunks
                if getattr(c, "type", "") == "message"
            ]
            assert (
                "规划阶段只保留最高优先级的 1 个任务"
                in message_chunks
            )

    @patch(f"{_LEARNINGS_STAGE_MOD}.run_learnings", new=_noop_agen)
    async def test_caps_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make_orchestrator(d)
            orch.config.max_tasks_per_session = 2

            async def _fake_isolated(_orch, task):
                orch._results.append(
                    CycleResult(success=True, summary=task.topic),
                )
                return
                yield  # noqa: RET504

            tasks = [
                OptimizationTask(topic=f"t{i}")
                for i in range(5)
            ]
            with patch.object(
                PRTaskPipeline,
                "run_isolated_stream",
                new=_fake_isolated,
            ):
                await _collect(
                    orch.run_session_stream(tasks=tasks)
                )
            assert len(orch.results) == 2


class TestTaskPipeline(IsolatedAsyncioTestCase):
    async def test_prepare_task_runtime_creates_task_session_and_fix_agent(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )
            orch.experience_store.search = AsyncMock(
                return_value=[]
            )
            orch.worktree_mgr.prepare = AsyncMock(
                return_value=f"{d}/worktrees/task-1"
            )
            orch.git.set_workspace = Mock()
            orch.ci_gate.set_workspace = Mock()
            orch.git.list_dirty_files = AsyncMock(
                return_value=["openjiuwen/harness/tools/filesystem.py"]
            )

            task_agent = type(
                "_TaskAgent",
                (),
                {"card": object()},
            )()
            fix_agent = object()
            commit_agent = object()
            task_session = object()

            with patch(
                "openjiuwen.auto_harness.agents.create_auto_harness_agent",
                side_effect=[task_agent, fix_agent],
            ) as create_task_agent, patch(
                "openjiuwen.auto_harness.agents.create_commit_agent",
                return_value=commit_agent,
            ) as create_commit_agent, patch(
                "openjiuwen.core.session.agent.create_agent_session",
                return_value=task_session,
            ) as create_agent_session:
                runtime = await prepare_task_runtime(
                    orch,
                    OptimizationTask(topic="task-1"),
                )

            assert runtime.task_agent is task_agent
            assert runtime.fix_agent is fix_agent
            assert runtime.commit_agent is commit_agent
            assert runtime.task_session is task_session
            assert runtime.preexisting_dirty_files == [
                "openjiuwen/harness/tools/filesystem.py"
            ]

            first_call = create_task_agent.call_args_list[0]
            assert (
                first_call.kwargs["workspace_override"]
                == f"{d}/worktrees/task-1"
            )
            assert "enable_task_loop" not in first_call.kwargs
            edit_safety_rail = first_call.kwargs[
                "edit_safety_rail"
            ]
            assert edit_safety_rail is runtime.edit_safety_rail

            second_call = create_task_agent.call_args_list[1]
            assert (
                second_call.kwargs["workspace_override"]
                == f"{d}/worktrees/task-1"
            )
            assert (
                second_call.kwargs["edit_safety_rail"]
                is edit_safety_rail
            )
            assert (
                second_call.kwargs["enable_task_loop"]
                is False
            )
            assert (
                second_call.kwargs["enable_task_planning"]
                is False
            )
            assert (
                second_call.kwargs["enable_progress_repeat"]
                is False
            )

            create_commit_agent.assert_called_once_with(
                orch.config,
                workspace_override=f"{d}/worktrees/task-1",
            )
            create_agent_session.assert_called_once_with(
                session_id="auto-harness-task-1",
                card=task_agent.card,
                close_stream_on_post_run=False,
            )

    async def test_timeout_handling(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(
                data_dir=d,
                task_timeout_secs=0.01,
            )
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            async def _slow_task_stream(
                orchestrator, task,
            ):
                del orchestrator, task
                await asyncio.sleep(10)
                yield  # async generator

            task = OptimizationTask(topic="slow")
            with patch.object(
                PRTaskPipeline,
                "_run_task_stream",
                new=_slow_task_stream,
            ):
                await _collect(
                    PRTaskPipeline.run_isolated_stream(
                        orch,
                        task,
                    )
                )
            assert orch._results[-1].error == "timeout"
            assert task.status == TaskStatus.TIMEOUT

    async def test_exception_handling(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            async def _boom_task_stream(
                orchestrator, task,
            ):
                del orchestrator, task
                raise RuntimeError("kaboom")
                yield  # noqa: RET504

            task = OptimizationTask(topic="boom")
            with patch.object(
                PRTaskPipeline,
                "_run_task_stream",
                new=_boom_task_stream,
            ):
                await _collect(
                    PRTaskPipeline.run_isolated_stream(
                        orch,
                        task,
                    )
                )
            assert "kaboom" in orch._results[-1].error
            assert task.status == TaskStatus.FAILED

    async def test_resolve_task_result_from_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )
            task = OptimizationTask(topic="done")
            orch.artifacts.put(
                "task_result",
                CycleResult(success=True, summary="done"),
                task_id="done",
            )
            result = PRTaskPipeline._resolve_task_result(
                orch, task
            )
            assert result.success is True

    async def test_orchestrator_initializes_task_contexts(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AutoHarnessConfig(data_dir=d)
            orch = AutoHarnessOrchestrator(
                cfg, agent=None,
            )

            assert orch.task_contexts == {}
