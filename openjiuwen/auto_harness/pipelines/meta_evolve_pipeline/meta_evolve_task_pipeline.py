# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task-scoped pipeline for the meta evolve pipeline."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.contexts import (
    TaskContext,
    TaskRuntime,
    task_key,
)
from openjiuwen.auto_harness.pipelines.base import (
    BasePipeline,
)
from openjiuwen.auto_harness.schema import (
    CycleResult,
    Experience,
    ExperienceType,
    OptimizationTask,
    TaskStatus,
)
from openjiuwen.auto_harness.stages.commit import (
    CommitStage,
)
from openjiuwen.auto_harness.stages.implement import (
    ImplementStage,
)
from openjiuwen.auto_harness.stages.publish_pr import (
    PublishPRStage,
)
from openjiuwen.auto_harness.stages.verify import (
    VerifyStage,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.orchestrator import (
        AutoHarnessOrchestrator,
    )

logger = logging.getLogger(__name__)


class _StopTaskPipeline(Exception):
    """Stop the task pipeline after a failed stage."""


async def prepare_task_runtime(
    orchestrator: "AutoHarnessOrchestrator",
    task: OptimizationTask,
) -> TaskRuntime:
    """Prepare worktree, agents, and rails for one task."""
    from openjiuwen.auto_harness.agents import (
        create_auto_harness_agent,
        create_commit_agent,
    )
    from openjiuwen.auto_harness.rails.edit_safety_rail import (
        EditSafetyRail,
    )
    from openjiuwen.core.session.agent import (
        create_agent_session,
    )

    related = await orchestrator.experience_store.search(
        task.topic
    )
    wt_path = await orchestrator.worktree_mgr.prepare(
        task.topic
    )
    orchestrator.git.set_workspace(wt_path)
    orchestrator.ci_gate.set_workspace(wt_path)
    edit_safety_rail = EditSafetyRail()
    edit_safety_rail.reset()
    preexisting_dirty_files = (
        await orchestrator.git.list_dirty_files()
    )
    task_agent = create_auto_harness_agent(
        orchestrator.config,
        workspace_override=wt_path,
        edit_safety_rail=edit_safety_rail,
    )
    fix_agent = create_auto_harness_agent(
        orchestrator.config,
        workspace_override=wt_path,
        edit_safety_rail=edit_safety_rail,
        enable_task_loop=False,
        enable_task_planning=False,
        enable_progress_repeat=False,
    )
    commit_agent = create_commit_agent(
        orchestrator.config,
        workspace_override=wt_path,
    )
    task_session = create_agent_session(
        session_id=f"auto-harness-{Path(wt_path).name}",
        card=getattr(task_agent, "card", None),
        close_stream_on_post_run=False,
    )
    return TaskRuntime(
        related=related,
        wt_path=wt_path,
        edit_safety_rail=edit_safety_rail,
        preexisting_dirty_files=preexisting_dirty_files,
        task_agent=task_agent,
        commit_agent=commit_agent,
        task_session=task_session,
        fix_agent=fix_agent,
    )


class PRTaskPipeline(BasePipeline):
    """Explicit task-scoped pipeline for meta evolve work."""

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        try:
            async for chunk in self.run_implement_stage_stream(
                ctx
            ):
                yield chunk

            async for chunk in self.run_verify_stage_stream(
                ctx
            ):
                yield chunk

            async for chunk in self.run_commit_stage_stream(
                ctx
            ):
                yield chunk

            async for chunk in self.run_publish_pr_stage_stream(
                ctx
            ):
                yield chunk
        except _StopTaskPipeline:
            return

    async def run_implement_stage_stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        stage = ImplementStage()
        result_holder = []
        async for chunk in self._stream_stage(
            stage,
            ctx,
            result_holder=result_holder,
        ):
            yield chunk
        if self._did_stage_fail(stage, result_holder):
            raise _StopTaskPipeline

    async def run_verify_stage_stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        stage = VerifyStage()
        result_holder = []
        async for chunk in self._stream_stage(
            stage,
            ctx,
            result_holder=result_holder,
        ):
            yield chunk
        if self._did_stage_fail(stage, result_holder):
            raise _StopTaskPipeline

    async def run_commit_stage_stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        stage = CommitStage()
        result_holder = []
        async for chunk in self._stream_stage(
            stage,
            ctx,
            result_holder=result_holder,
        ):
            yield chunk
        if self._did_stage_fail(stage, result_holder):
            raise _StopTaskPipeline

    async def run_publish_pr_stage_stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        stage = PublishPRStage()
        result_holder = []
        async for chunk in self._stream_stage(
            stage,
            ctx,
            result_holder=result_holder,
        ):
            yield chunk
        if self._did_stage_fail(stage, result_holder):
            raise _StopTaskPipeline

    @classmethod
    async def run_isolated_stream(
        cls,
        orchestrator: "AutoHarnessOrchestrator",
        task: OptimizationTask,
    ) -> AsyncIterator[Any]:
        """Run a task inside timeout protection."""
        task.status = TaskStatus.RUNNING
        logger.info("Task started: %s", task.topic)
        try:
            queue: asyncio.Queue[Any] = asyncio.Queue()
            sentinel = object()

            async def _producer() -> CycleResult:
                try:
                    async for chunk in cls._run_task_stream(
                        orchestrator,
                        task,
                    ):
                        await queue.put(chunk)
                    return cls._resolve_task_result(
                        orchestrator,
                        task,
                    )
                finally:
                    await queue.put(sentinel)

            producer_task = asyncio.create_task(
                asyncio.wait_for(
                    _producer(),
                    timeout=orchestrator.config.task_timeout_secs,
                )
            )
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item
            result = await producer_task
        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMEOUT
            logger.error("Task timed out: %s", task.topic)
            await orchestrator.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic=task.topic,
                    summary="task timeout",
                    outcome="timeout",
                )
            )
            result = CycleResult(
                error="timeout",
                error_log="Task exceeded timeout",
            )
        except Exception as exc:
            task.status = TaskStatus.FAILED
            logger.exception("Task failed: %s", task.topic)
            await orchestrator.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic=task.topic,
                    summary=str(exc)[:200],
                    outcome="exception",
                )
            )
            result = CycleResult(
                error=str(exc)[:200],
                error_log=str(exc),
            )
        orchestrator.record_cycle_result(result)

    @classmethod
    async def _run_task_stream(
        cls,
        orchestrator: "AutoHarnessOrchestrator",
        task: OptimizationTask,
    ) -> AsyncIterator[Any]:
        runtime = await prepare_task_runtime(
            orchestrator,
            task,
        )
        ctx = TaskContext(
            orchestrator=orchestrator,
            task=task,
            runtime=runtime,
        )
        orchestrator.task_contexts[task_key(task)] = ctx
        try:
            async for chunk in cls().stream(ctx):
                yield chunk
        finally:
            await orchestrator.worktree_mgr.cleanup(
                runtime.wt_path
            )
            orchestrator.task_contexts.pop(
                task_key(task), None
            )

    @staticmethod
    def _resolve_task_result(
        orchestrator: "AutoHarnessOrchestrator",
        task: OptimizationTask,
    ) -> CycleResult:
        result = orchestrator.artifacts.get(
            "task_result",
            task_id=task_key(task),
        )
        if not isinstance(result, CycleResult):
            result = orchestrator.last_cycle_result
        if result is None:
            return CycleResult(
                error="missing result",
                error_log=(
                    "No cycle result recorded for completed task"
                ),
            )
        if result.success:
            task.status = TaskStatus.SUCCESS
        elif task.status == TaskStatus.RUNNING:
            task.status = TaskStatus.FAILED
        return result
