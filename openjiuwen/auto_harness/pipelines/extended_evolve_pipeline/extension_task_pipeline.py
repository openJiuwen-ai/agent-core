# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task-scoped pipeline for one generated runtime extension."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.contexts import (
    TaskContext,
    TaskRuntime,
    task_key,
)
from openjiuwen.auto_harness.pipelines.base import (
    BasePipeline,
    PipelineStageMap,
)
from openjiuwen.auto_harness.schema import (
    CycleResult,
    Experience,
    ExperienceType,
    ExtensionDesign,
    OptimizationTask,
    StageResult,
    StageSlot,
    TaskStatus,
)
from openjiuwen.auto_harness.stages.implement import (
    ExtendImplementStage,
)
from openjiuwen.auto_harness.stages.verify import (
    ExtendVerifyStage,
)
from openjiuwen.auto_harness.stages.activate import (
    ExtendActivateStage,
)
from openjiuwen.auto_harness.agents import (
    create_auto_harness_agent,
    create_commit_agent,
)
from openjiuwen.core.common.logging import (
    logger,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.orchestrator import (
        AutoHarnessOrchestrator,
    )


class _StopExtensionTaskPipeline(Exception):
    """Stop the extension task pipeline after a failed stage."""


def build_extension_task(
    design: ExtensionDesign,
) -> OptimizationTask:
    """Build a task wrapper so extension runs can reuse task context."""
    return OptimizationTask(
        topic=f"runtime-extension:{design.extension_name}",
        description=(
            "Implement and verify runtime extension "
            f"{design.extension_name}"
        ),
        files=[
            design.file_plan.get("root", ""),
            design.file_plan.get("manifest", ""),
        ],
    )


async def prepare_extension_task_runtime(
    orchestrator: "AutoHarnessOrchestrator",
    design: ExtensionDesign,
    *,
    configure_shared_workspace: bool = True,
) -> TaskRuntime:
    """Prepare a clean worktree for one extension build."""
    logger.info(
        "[AutoHarnessExtensionTask] preparing runtime: extension=%s base_branch=%s remote=%s",
        design.extension_name,
        orchestrator.config.git_base_branch,
        orchestrator.config.git_remote,
    )
    wt_path = await orchestrator.worktree_mgr.prepare(
        f"extension-{design.extension_name}"
    )
    logger.info(
        "[AutoHarnessExtensionTask] worktree ready: extension=%s wt_path=%s",
        design.extension_name,
        wt_path,
    )
    if configure_shared_workspace:
        orchestrator.git.set_workspace(wt_path)
        orchestrator.ci_gate.set_workspace(wt_path)
    task_agent = create_auto_harness_agent(
        orchestrator.config,
        workspace_override=wt_path,
        enable_edit_safety=False,
        skill_names=[
            "implement_ext",
            "verify",
            "verify_ext",
            "communicate",
        ],
        extra_rails=orchestrator.stream_rails or None,
    )
    from openjiuwen.core.session.agent import (
        create_agent_session,
    )

    task_session = create_agent_session(
        session_id=(
            f"auto-harness-{Path(wt_path).name}"
        ),
        card=getattr(task_agent, "card", None),
        close_stream_on_post_run=False,
    )
    commit_agent = create_commit_agent(
        orchestrator.config,
        workspace_override=wt_path,
        extra_rails=orchestrator.stream_rails or None,
    )
    return TaskRuntime(
        related=[],
        wt_path=wt_path,
        edit_safety_rail=None,
        preexisting_dirty_files=[],
        task_agent=task_agent,
        task_session=task_session,
        commit_agent=commit_agent,
    )


@dataclass
class VerifiedExtensionTask:
    """A verified extension task ready for serial activation."""

    design: ExtensionDesign
    task: OptimizationTask
    ctx: TaskContext


class ExtensionTaskPipeline(BasePipeline):
    """Build, verify, commit, and publish PR for one runtime extension."""

    stage_map = PipelineStageMap(mapping={
        StageSlot.IMPLEMENT: ExtendImplementStage,
        StageSlot.VERIFY: ExtendVerifyStage,
        StageSlot.ACTIVATE: ExtendActivateStage,
    })

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        design = ctx.require_artifact("extension_target")
        logger.info(
            "[AutoHarnessExtensionTask] pipeline start: extension=%s task=%s",
            design.extension_name,
            ctx.task.topic,
        )
        try:
            async for chunk in self._run_stage_stream(
                ExtendImplementStage(),
                ctx,
            ):
                yield chunk
            async for chunk in self._run_stage_stream(
                ExtendVerifyStage(),
                ctx,
            ):
                yield chunk
            async for chunk in self._run_stage_stream(
                ExtendActivateStage(),
                ctx,
            ):
                yield chunk
            # activate 通过后直接结束 pipeline
        except _StopExtensionTaskPipeline:
            logger.warning(
                "[AutoHarnessExtensionTask] pipeline stopped after failed stage: extension=%s task=%s",
                design.extension_name,
                ctx.task.topic,
            )
            return

        if ctx.get_artifact("task_result") is None:
            ctx.put_artifact(
                "task_result",
                CycleResult(
                    success=True,
                    summary=(
                        "Extension activated: "
                        f"{design.extension_name}"
                    ),
                ),
            )
        yield ctx.message(
            f"Extension activated: "
            f"{design.extension_name}"
        )
        logger.info(
            "[AutoHarnessExtensionTask] pipeline success: extension=%s task=%s",
            design.extension_name,
            ctx.task.topic,
        )

    async def run_build_verify_stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        """Run only implement_ext and verify_ext for parallel waves."""
        design = ctx.require_artifact("extension_target")
        logger.info(
            "[AutoHarnessExtensionTask] build/verify start: extension=%s task=%s",
            design.extension_name,
            ctx.task.topic,
        )
        try:
            async for chunk in self._run_stage_stream(
                ExtendImplementStage(),
                ctx,
            ):
                yield chunk
            async for chunk in self._run_stage_stream(
                ExtendVerifyStage(),
                ctx,
            ):
                yield chunk
        except _StopExtensionTaskPipeline:
            logger.warning(
                "[AutoHarnessExtensionTask] build/verify stopped after failed stage: extension=%s task=%s",
                design.extension_name,
                ctx.task.topic,
            )
            return

        logger.info(
            "[AutoHarnessExtensionTask] build/verify success: extension=%s task=%s",
            design.extension_name,
            ctx.task.topic,
        )

    async def _run_activate_stage_stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        """Run activate_ext for an already verified runtime extension."""
        design = ctx.require_artifact("extension_target")
        try:
            async for chunk in self._run_stage_stream(
                ExtendActivateStage(),
                ctx,
            ):
                yield chunk
        except _StopExtensionTaskPipeline:
            logger.warning(
                "[AutoHarnessExtensionTask] activate stopped after failed stage: extension=%s task=%s",
                design.extension_name,
                ctx.task.topic,
            )
            return

        if ctx.get_artifact("task_result") is None:
            ctx.put_artifact(
                "task_result",
                CycleResult(
                    success=True,
                    summary=(
                        "Extension activated: "
                        f"{design.extension_name}"
                    ),
                ),
            )
        yield ctx.message(
            f"Extension activated: "
            f"{design.extension_name}"
        )

    async def _run_stage_stream(
        self,
        stage,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        design = ctx.require_artifact("extension_target")
        started_at = time.monotonic()
        logger.info(
            "[AutoHarnessExtensionTask] stage begin: extension=%s stage=%s task=%s",
            design.extension_name,
            stage.name,
            ctx.task.topic,
        )
        yield _extension_stage_output(
            ctx,
            stage,
            status="running",
        )
        result_holder: list = []
        try:
            async for event in stage.stream(ctx):
                if isinstance(event, StageResult):
                    result_holder.append(event)
                    if event.artifacts:
                        ctx.put_artifacts(event.artifacts)
                    for message in event.messages:
                        yield ctx.message(
                            message,
                            stage=_parent_stage(stage),
                        )
                    continue
                yield event
        except Exception:
            logger.exception(
                "[AutoHarnessExtensionTask] stage exception: extension=%s stage=%s task=%s",
                design.extension_name,
                stage.name,
                ctx.task.topic,
            )
            raise
        if not self._did_stage_fail(stage, result_holder):
            logger.info(
                "[AutoHarnessExtensionTask] stage success: extension=%s stage=%s elapsed=%.1fs",
                design.extension_name,
                stage.name,
                time.monotonic() - started_at,
            )
            yield _extension_stage_output(
                ctx,
                stage,
                status="success",
            )
            return
        result = self._require_stage_result(
            stage,
            result_holder,
        )
        logger.warning(
            "[AutoHarnessExtensionTask] stage failed: extension=%s stage=%s elapsed=%.1fs error=%s messages=%s",
            design.extension_name,
            stage.name,
            time.monotonic() - started_at,
            result.error or "",
            list(result.messages or []),
        )
        if ctx.get_artifact("task_result") is None:
            ctx.put_artifact(
                "task_result",
                CycleResult(
                    success=False,
                    error=(
                        result.error
                        or f"Stage failed: {stage.name}"
                    ),
                ),
            )
        yield _extension_stage_output(
            ctx,
            stage,
            status="failed",
            error=result.error or f"Stage failed: {stage.name}",
            messages=list(result.messages or []),
        )
        raise _StopExtensionTaskPipeline

    @classmethod
    async def run_isolated_stream(
        cls,
        orchestrator: "AutoHarnessOrchestrator",
        design: ExtensionDesign,
    ) -> AsyncIterator[Any]:
        """Run one extension task inside timeout protection."""
        task = build_extension_task(design)
        task.status = TaskStatus.RUNNING
        timeout_secs = _remaining_task_timeout(orchestrator)
        started_at = time.monotonic()
        logger.info(
            "[AutoHarnessExtensionTask] task started: extension=%s "
            "kind=%s depends_on=%s timeout=%.1fs "
            "session_remaining=%.1fs task_timeout=%.1fs",
            design.extension_name,
            design.kind,
            list(design.depends_on or []),
            timeout_secs,
            orchestrator.budget.remaining_secs,
            orchestrator.config.task_timeout_secs,
        )
        try:
            queue: asyncio.Queue[Any] = asyncio.Queue()
            sentinel = object()

            async def _producer() -> CycleResult:
                try:
                    async for chunk in cls._run_task_stream(
                        orchestrator,
                        task,
                        design,
                        include_activate=True,
                        configure_shared_workspace=True,
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
                    timeout=timeout_secs,
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
            logger.error(
                "[AutoHarnessExtensionTask] task timed out: extension=%s "
                "elapsed=%.1fs timeout=%.1fs session_remaining=%.1fs "
                "task_timeout=%.1f",
                design.extension_name,
                time.monotonic() - started_at,
                timeout_secs,
                orchestrator.budget.remaining_secs,
                orchestrator.config.task_timeout_secs,
            )
            await orchestrator.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic=task.topic,
                    summary="task timeout",
                    outcome="timeout",
                )
            )
            result = CycleResult(
                success=False,
                error="timeout",
                error_log=(
                    "Extension task exceeded timeout"
                ),
            )
        except Exception as exc:
            task.status = TaskStatus.FAILED
            logger.exception(
                "[AutoHarnessExtensionTask] task exception: extension=%s elapsed=%.1fs error=%s",
                design.extension_name,
                time.monotonic() - started_at,
                exc,
            )
            await orchestrator.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic=task.topic,
                    summary=str(exc)[:200],
                    outcome="exception",
                )
            )
            result = CycleResult(
                success=False,
                error=str(exc)[:200],
                error_log=str(exc),
            )

        orchestrator.record_cycle_result(result)
        logger.info(
            "[AutoHarnessExtensionTask] task finished: extension=%s "
            "success=%s status=%s elapsed=%.1fs error=%s "
            "results_total=%d",
            design.extension_name,
            result.success,
            task.status,
            time.monotonic() - started_at,
            result.error or "",
            len(orchestrator.results),
        )

    @classmethod
    async def run_build_verify_isolated_stream(
        cls,
        orchestrator: "AutoHarnessOrchestrator",
        design: ExtensionDesign,
        *,
        verified_tasks: list[VerifiedExtensionTask] | None = None,
    ) -> AsyncIterator[Any]:
        """Run implement_ext and verify_ext in an isolated worktree."""
        task = build_extension_task(design)
        task.status = TaskStatus.RUNNING
        timeout_secs = _remaining_task_timeout(orchestrator)
        started_at = time.monotonic()
        logger.info(
            "[AutoHarnessExtensionTask] build/verify task started: extension=%s kind=%s depends_on=%s timeout=%.1fs",
            design.extension_name,
            design.kind,
            list(design.depends_on or []),
            timeout_secs,
        )
        ctx_holder: list[TaskContext] = []
        try:
            queue: asyncio.Queue[Any] = asyncio.Queue()
            sentinel = object()

            async def _producer() -> CycleResult:
                try:
                    async for chunk in cls._run_task_stream(
                        orchestrator,
                        task,
                        design,
                        include_activate=False,
                        configure_shared_workspace=False,
                        ctx_holder=ctx_holder,
                    ):
                        await queue.put(chunk)
                    return cls._resolve_build_verify_result(
                        orchestrator,
                        task,
                    )
                finally:
                    await queue.put(sentinel)

            producer_task = asyncio.create_task(
                asyncio.wait_for(
                    _producer(),
                    timeout=timeout_secs,
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
            logger.error(
                "[AutoHarnessExtensionTask] build/verify timed out: extension=%s elapsed=%.1fs timeout=%.1fs",
                design.extension_name,
                time.monotonic() - started_at,
                timeout_secs,
            )
            await orchestrator.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic=task.topic,
                    summary="task timeout",
                    outcome="timeout",
                )
            )
            result = CycleResult(
                success=False,
                error="timeout",
                error_log=(
                    "Extension build/verify exceeded timeout"
                ),
            )
        except Exception as exc:
            task.status = TaskStatus.FAILED
            logger.exception(
                "[AutoHarnessExtensionTask] build/verify exception: extension=%s elapsed=%.1fs error=%s",
                design.extension_name,
                time.monotonic() - started_at,
                exc,
            )
            await orchestrator.experience_store.record(
                Experience(
                    type=ExperienceType.FAILURE,
                    topic=task.topic,
                    summary=str(exc)[:200],
                    outcome="exception",
                )
            )
            result = CycleResult(
                success=False,
                error=str(exc)[:200],
                error_log=str(exc),
            )

        if result.success:
            task.status = TaskStatus.SUCCESS
            if verified_tasks is not None and ctx_holder:
                verified_tasks.append(
                    VerifiedExtensionTask(
                        design=design,
                        task=task,
                        ctx=ctx_holder[-1],
                    )
                )
        else:
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.FAILED
            orchestrator.record_cycle_result(result)
        logger.info(
            "[AutoHarnessExtensionTask] build/verify finished: "
            "extension=%s success=%s status=%s elapsed=%.1fs "
            "error=%s results_total=%d",
            design.extension_name,
            result.success,
            task.status,
            time.monotonic() - started_at,
            result.error or "",
            len(orchestrator.results),
        )

    @classmethod
    async def run_activate_stream(
        cls,
        orchestrator: "AutoHarnessOrchestrator",
        verified: VerifiedExtensionTask,
    ) -> AsyncIterator[Any]:
        """Activate one verified extension and record its final result."""
        async for chunk in cls()._run_activate_stage_stream(
            verified.ctx
        ):
            yield chunk
        result = cls._resolve_task_result(
            orchestrator,
            verified.task,
        )
        orchestrator.record_cycle_result(result)

    @classmethod
    async def _run_task_stream(
        cls,
        orchestrator: "AutoHarnessOrchestrator",
        task: OptimizationTask,
        design: ExtensionDesign,
        *,
        include_activate: bool,
        configure_shared_workspace: bool,
        ctx_holder: list[TaskContext] | None = None,
    ) -> AsyncIterator[Any]:
        logger.info(
            "[AutoHarnessExtensionTask] runtime setup begin: extension=%s task=%s",
            design.extension_name,
            task.topic,
        )
        runtime = await prepare_extension_task_runtime(
            orchestrator,
            design,
            configure_shared_workspace=configure_shared_workspace,
        )
        logger.info(
            "[AutoHarnessExtensionTask] runtime setup done: extension=%s wt_path=%s",
            design.extension_name,
            runtime.wt_path,
        )
        ctx = TaskContext(
            orchestrator=orchestrator,
            task=task,
            runtime=runtime,
        )
        ctx.put_artifact("extension_target", design)
        orchestrator.task_contexts[task_key(task)] = ctx
        if ctx_holder is not None:
            ctx_holder.append(ctx)
        try:
            pipeline = cls()
            stream = (
                pipeline.stream(ctx)
                if include_activate
                else pipeline.run_build_verify_stream(ctx)
            )
            async for chunk in stream:
                yield chunk
        finally:
            logger.info(
                "[AutoHarnessExtensionTask] cleanup begin: extension=%s wt_path=%s",
                design.extension_name,
                runtime.wt_path,
            )
            await orchestrator.worktree_mgr.cleanup(
                runtime.wt_path
            )
            orchestrator.task_contexts.pop(
                task_key(task), None
            )
            logger.info(
                "[AutoHarnessExtensionTask] cleanup done: extension=%s wt_path=%s",
                design.extension_name,
                runtime.wt_path,
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
            result = CycleResult(
                success=False,
                error="missing result",
                error_log=(
                    "No cycle result recorded for extension task"
                ),
            )
        if result.success:
            task.status = TaskStatus.SUCCESS
        elif task.status == TaskStatus.RUNNING:
            task.status = TaskStatus.FAILED
        return result

    @staticmethod
    def _resolve_build_verify_result(
        orchestrator: "AutoHarnessOrchestrator",
        task: OptimizationTask,
    ) -> CycleResult:
        result = orchestrator.artifacts.get(
            "task_result",
            task_id=task_key(task),
        )
        if isinstance(result, CycleResult):
            return result
        runtime_ext = orchestrator.artifacts.get(
            "runtime_extension",
            task_id=task_key(task),
        )
        if runtime_ext is None:
            return CycleResult(
                success=False,
                error="missing runtime extension",
                error_log=(
                    "No runtime_extension artifact recorded "
                    "after build/verify"
                ),
            )
        return CycleResult(
            success=True,
            summary=f"Extension verified: {task.topic}",
        )


__all__ = [
    "ExtensionTaskPipeline",
    "VerifiedExtensionTask",
    "build_extension_task",
    "prepare_extension_task_runtime",
]


def _remaining_task_timeout(
    orchestrator: "AutoHarnessOrchestrator",
) -> float:
    """Bound one extension task by both task and session budgets."""
    return max(
        1.0,
        min(
            orchestrator.config.task_timeout_secs,
            orchestrator.budget.remaining_secs,
        ),
    )


def _parent_stage(stage: Any) -> str:
    if stage.name in {"implement_ext", "verify_ext"}:
        return "build_verify"
    if stage.name == "activate_ext":
        return "activate"
    return str(stage.slot or stage.name)


def _extension_stage_output(
    ctx: TaskContext,
    stage: Any,
    *,
    status: str,
    error: str = "",
    messages: list[str] | None = None,
) -> OutputSchema:
    design = ctx.require_artifact("extension_target")
    return OutputSchema(
        type="stage_result",
        index=0,
        payload={
            "stage": _parent_stage(stage),
            "scope": "extension",
            "parent_stage": _parent_stage(stage),
            "extension_stage": stage.name,
            "extension_name": design.extension_name,
            "task_id": task_key(ctx.task),
            "status": status,
            "error": error,
            "messages": messages or [],
            "metrics": {},
        },
    )
