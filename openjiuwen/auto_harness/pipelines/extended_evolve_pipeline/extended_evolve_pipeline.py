# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-level extended evolve pipeline."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from openjiuwen.auto_harness.contexts import (
    SessionContext,
    TaskContext,
    TaskRuntime,
)
from openjiuwen.auto_harness.infra.runtime_extension_merger import (
    MergedExtensionError,
)
from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.pipelines.base import (
    BasePipeline,
    PipelineStageMap,
)
from openjiuwen.auto_harness.pipelines.extended_evolve_pipeline.extension_task_pipeline import (
    ExtensionTaskPipeline,
    VerifiedExtensionTask,
)
from openjiuwen.auto_harness.schema import (
    CycleResult,
    ExtensionDesign,
    ExtensionDesignArtifact,
    OptimizationTask,
    RuntimeExtensionArtifact,
    SessionResultsArtifact,
    StageSlot,
)
from openjiuwen.auto_harness.stages.assess import (
    ExtendAssessStage,
)
from openjiuwen.auto_harness.stages.merge import (
    MergeActivationBlock,
    MergeSuccessResult,
)
from openjiuwen.auto_harness.stages.plan import (
    ExtendPlanStage,
)
from openjiuwen.core.common.logging import (
    logger,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)


class ExtendedEvolvePipeline(BasePipeline):
    """Session pipeline for isolated extension evolution."""

    name = EXTENDED_EVOLVE_PIPELINE
    description = "Extended evolve generation pipeline."
    expected_outputs = [
        "extension_design",
        "session_results",
    ]
    stage_order = [
        ("assess", "评估扩展缺口"),
        ("plan", "设计扩展方案"),
        ("build_verify", "实现/验证扩展"),
        ("activate", "激活扩展"),
    ]
    stage_map = PipelineStageMap(mapping={
        StageSlot.ASSESS: ExtendAssessStage,
        StageSlot.PLAN: ExtendPlanStage,
    })

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        logger.info(
            "[AutoHarnessExtendedPipeline] session pipeline start: max_tasks=%s budget_remaining=%.1fs",
            ctx.orchestrator.config.max_tasks_per_session,
            ctx.orchestrator.budget.remaining_secs,
        )
        gap_result_holder = []
        async for chunk in self._stream_stage(
            ExtendAssessStage(),
            ctx,
            result_holder=gap_result_holder,
        ):
            yield chunk
        if self._did_stage_fail(
            ExtendAssessStage(),
            gap_result_holder,
        ):
            logger.warning(
                "[AutoHarnessExtendedPipeline] assess failed, stop session pipeline"
            )
            return

        design_result_holder = []
        async for chunk in self._stream_stage(
            ExtendPlanStage(),
            ctx,
            result_holder=design_result_holder,
        ):
            yield chunk
        if self._did_stage_fail(
            ExtendPlanStage(),
            design_result_holder,
        ):
            logger.warning(
                "[AutoHarnessExtendedPipeline] plan failed, stop session pipeline"
            )
            return
        design_artifact = ctx.require_artifact(
            "extension_design"
        )
        if not isinstance(
            design_artifact, ExtensionDesignArtifact
        ):
            raise TypeError(
                "extension_design artifact must be "
                "ExtensionDesignArtifact"
            )

        constraints = [
            design
            for design in design_artifact.designs
            if design.kind == "constraint"
        ]
        capabilities = [
            design
            for design in design_artifact.designs
            if design.kind != "constraint"
        ]
        designs_to_run = (constraints + capabilities)[
            : ctx.orchestrator.config.max_tasks_per_session
        ]
        logger.info(
            "[AutoHarnessExtendedPipeline] design selection: total=%d "
            "constraints=%d capabilities=%d selected=%d "
            "selected_names=%s",
            len(design_artifact.designs),
            len(constraints),
            len(capabilities),
            len(designs_to_run),
            [design.extension_name for design in designs_to_run],
        )
        verified_tasks: list[VerifiedExtensionTask] = []
        failed_extensions: set[str] = set()
        yield _top_stage_result(
            "build_verify",
            "running",
        )
        async for chunk in _run_dependency_waves(
            ctx,
            designs_to_run,
            verified_tasks,
            failed_extensions,
        ):
            yield chunk
        yield _top_stage_result(
            "build_verify",
            "failed" if failed_extensions else "success",
        )

        if verified_tasks:
            yield _top_stage_result(
                "activate",
                "running",
            )

        activate_started_at = len(ctx.orchestrator.results)
        if len(verified_tasks) == 1:
            logger.info("[AutoHarnessExtendedPipeline] only get 1 verified tasks")
            async for chunk in ExtensionTaskPipeline.run_activate_stream(
                ctx.orchestrator,
                verified_tasks[0],
            ):
                yield chunk
        elif len(verified_tasks) > 1:
            logger.info("[AutoHarnessExtendedPipeline] get multiple verified tasks")
            merge_before_activate = MergeActivationBlock()
            merged_artifact: RuntimeExtensionArtifact | None = None
            try:
                async for chunk in merge_before_activate.stream(
                    ctx.orchestrator,
                    verified_tasks,
                ):
                    if isinstance(chunk, MergeSuccessResult):
                        merged_artifact = chunk.artifact
                    else:
                        yield chunk
            except MergedExtensionError as exc:
                ctx.orchestrator.record_cycle_result(
                    CycleResult(
                        success=False,
                        error=f"merge multiple extensions failed: {exc}",
                    )
                )
            else:
                merged_verified = _build_merged_verified_task(
                    ctx.orchestrator,
                    merged_artifact,
                )
                async for chunk in ExtensionTaskPipeline.run_activate_stream(
                    ctx.orchestrator,
                    merged_verified,
                ):
                    yield chunk

        if verified_tasks:
            activate_results = ctx.orchestrator.results[
                activate_started_at:
            ]
            activate_failed = any(
                not result.success
                for result in activate_results
            )
            yield _top_stage_result(
                "activate",
                "failed" if activate_failed else "success",
            )

        ctx.put_artifact(
            "session_results",
            SessionResultsArtifact(
                results=list(ctx.orchestrator.results)
            ),
        )
        logger.info(
            "[AutoHarnessExtendedPipeline] session pipeline end: results=%d failed_extensions=%s",
            len(ctx.orchestrator.results),
            sorted(failed_extensions),
        )


async def _run_dependency_waves(
    ctx: SessionContext,
    designs: list[ExtensionDesign],
    verified_tasks: list[VerifiedExtensionTask],
    failed_extensions: set[str],
) -> AsyncIterator[Any]:
    """Run implement+verify in dependency waves."""
    pending = {
        design.extension_name: design
        for design in designs
    }
    completed: set[str] = set()
    selected_names = set(pending)
    wave_index = 0

    while pending:
        skipped = []
        for design in pending.values():
            if _has_unmet_selected_dependency(
                design,
                failed_extensions,
                selected_names,
            ):
                skipped.append(design)
        for design in skipped:
            unmet = _collect_unmet_selected_dependencies(
                design,
                failed_extensions,
                selected_names,
            )
            _record_skipped_dependency(ctx, design, unmet)
            pending.pop(design.extension_name, None)
            failed_extensions.add(design.extension_name)
            yield ctx.orchestrator.message_output(
                "Skipped extension "
                f"{design.extension_name}: failed "
                f"dependency {', '.join(unmet)}"
            )

        ready = []
        for design in pending.values():
            if _dependencies_completed(design, completed):
                ready.append(design)
        if not ready:
            for design in list(pending.values()):
                unmet = _collect_incomplete_dependencies(
                    design,
                    completed,
                )
                _record_skipped_dependency(ctx, design, unmet)
                failed_extensions.add(design.extension_name)
                yield ctx.orchestrator.message_output(
                    "Skipped extension "
                    f"{design.extension_name}: unresolved "
                    f"dependency {', '.join(unmet)}"
                )
            pending.clear()
            break

        wave_index += 1
        for design in ready:
            pending.pop(design.extension_name, None)
        logger.info(
            "[AutoHarnessExtendedPipeline] extension wave dispatch: wave=%d extensions=%s",
            wave_index,
            [design.extension_name for design in ready],
        )
        wave_results: dict[str, bool] = {}
        async for chunk in _run_build_verify_wave(
            ctx,
            ready,
            verified_tasks,
            wave_results,
        ):
            yield chunk
        for design in ready:
            if wave_results.get(design.extension_name):
                completed.add(design.extension_name)
            else:
                failed_extensions.add(design.extension_name)


async def _run_build_verify_wave(
    ctx: SessionContext,
    designs: list[ExtensionDesign],
    verified_tasks: list[VerifiedExtensionTask],
    wave_results: dict[str, bool],
) -> AsyncIterator[Any]:
    """Run one dependency wave with bounded concurrency."""
    concurrency = max(
        1,
        min(
            ctx.orchestrator.config.extension_verify_concurrency,
            ctx.orchestrator.config.max_tasks_per_session,
            len(designs),
        ),
    )
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()
    semaphore = asyncio.Semaphore(concurrency)

    async def _worker(design: ExtensionDesign) -> None:
        if ctx.orchestrator.budget.should_stop:
            wave_results[design.extension_name] = False
            return
        if not ctx.orchestrator.budget.check_task_budget(
            task_timeout_secs=1.0
        ):
            wave_results[design.extension_name] = False
            return
        local_verified: list[VerifiedExtensionTask] = []
        logger.info(
            "[AutoHarnessExtendedPipeline] extension build/verify dispatch: extension=%s kind=%s depends_on=%s",
            design.extension_name,
            design.kind,
            list(design.depends_on or []),
        )
        result_count = len(ctx.orchestrator.results)
        async with semaphore:
            try:
                stream = (
                    ExtensionTaskPipeline.run_build_verify_isolated_stream(
                        ctx.orchestrator,
                        design,
                        verified_tasks=local_verified,
                    )
                )
            except TypeError:
                stream = (
                    ExtensionTaskPipeline.run_build_verify_isolated_stream(
                        ctx.orchestrator,
                        design,
                    )
                )
            async for chunk in stream:
                await queue.put(chunk)
        if local_verified:
            verified_tasks.extend(local_verified)
            wave_results[design.extension_name] = True
            return
        if len(ctx.orchestrator.results) > result_count:
            wave_results[design.extension_name] = (
                ctx.orchestrator.results[-1].success
            )
            return
        wave_results[design.extension_name] = False

    async def _run_all() -> None:
        try:
            await asyncio.gather(
                *[
                    asyncio.create_task(_worker(design))
                    for design in designs
                ]
            )
        finally:
            await queue.put(sentinel)

    runner = asyncio.create_task(_run_all())
    while True:
        item = await queue.get()
        if item is sentinel:
            break
        yield item
    await runner


def _has_unmet_selected_dependency(
    design: ExtensionDesign,
    failed_extensions: set[str],
    selected_names: set[str],
) -> bool:
    """Return whether design has a failed or unselected dependency."""
    for dependency in design.depends_on or []:
        if dependency in failed_extensions:
            return True
        if dependency not in selected_names:
            return True
    return False


def _collect_unmet_selected_dependencies(
    design: ExtensionDesign,
    failed_extensions: set[str],
    selected_names: set[str],
) -> list[str]:
    """Return dependencies that cannot be satisfied by selected designs."""
    unmet = []
    for dependency in design.depends_on or []:
        if dependency in failed_extensions:
            unmet.append(dependency)
            continue
        if dependency not in selected_names:
            unmet.append(dependency)
    return unmet


def _dependencies_completed(
    design: ExtensionDesign,
    completed: set[str],
) -> bool:
    """Return whether all design dependencies are completed."""
    for dependency in design.depends_on or []:
        if dependency not in completed:
            return False
    return True


def _collect_incomplete_dependencies(
    design: ExtensionDesign,
    completed: set[str],
) -> list[str]:
    """Return dependencies that have not completed."""
    unmet = []
    for dependency in design.depends_on or []:
        if dependency not in completed:
            unmet.append(dependency)
    return unmet


def _record_skipped_dependency(
    ctx: SessionContext,
    design: ExtensionDesign,
    unmet: list[str],
) -> None:
    ctx.orchestrator.record_cycle_result(
        CycleResult(
            success=False,
            summary=(
                "skipped extension "
                f"{design.extension_name}"
            ),
            error="skipped dependency",
            error_log=(
                "Skipped because dependency "
                f"failed or was unavailable: {', '.join(unmet)}"
            ),
        )
    )


def _top_stage_result(
    stage: str,
    status: str,
) -> OutputSchema:
    return OutputSchema(
        type="stage_result",
        index=0,
        payload={
            "stage": stage,
            "status": status,
            "messages": [],
            "metrics": {},
        },
    )


def _build_merged_verified_task(
    orchestrator: Any,
    merged: RuntimeExtensionArtifact,
) -> VerifiedExtensionTask:
    """Construct a self-consistent VerifiedExtensionTask for the merged package.

    The merged extension is a brand-new entity (not a child of any source
    extension), so it owns its own ``OptimizationTask``, ``ExtensionDesign``
    and ``TaskContext``.  This keeps ``extension_target`` /
    ``runtime_extension`` / ``task_id`` aligned for downstream activation.
    """
    design = ExtensionDesign(
        gap_id="merged",
        extension_name=merged.extension_name,
        kind="merged",
    )
    task = OptimizationTask(
        topic=f"runtime-extension:{merged.extension_name}",
    )
    session_root = str(orchestrator.ensure_session_runtime_dir())
    runtime = TaskRuntime(
        related=[],
        wt_path=session_root,
        edit_safety_rail=None,
        preexisting_dirty_files=[],
        task_agent=None,
        commit_agent=None,
    )
    ctx = TaskContext(
        orchestrator=orchestrator,
        task=task,
        runtime=runtime,
    )
    ctx.put_artifact("extension_target", design)
    ctx.put_artifact("runtime_extension", merged)
    return VerifiedExtensionTask(
        design=design,
        task=task,
        ctx=ctx,
    )
