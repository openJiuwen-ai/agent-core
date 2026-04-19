# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-level meta evolve pipeline."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from openjiuwen.auto_harness.contexts import (
    SessionContext,
)
from openjiuwen.auto_harness.pipelines import (
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.pipelines.base import (
    BasePipeline,
)
from openjiuwen.auto_harness.pipelines.meta_evolve_pipeline.meta_evolve_task_pipeline import (
    PRTaskPipeline,
)
from openjiuwen.auto_harness.schema import (
    SessionResultsArtifact,
    TaskPlanArtifact,
)
from openjiuwen.auto_harness.stages.assess import (
    AssessStage,
)
from openjiuwen.auto_harness.stages.learnings import (
    LearningsStage,
)
from openjiuwen.auto_harness.stages.plan import (
    PlanStage,
)


class _StopMetaEvolvePipeline(Exception):
    """Stop the pipeline after a failed stage."""


class MetaEvolvePipeline(BasePipeline):
    """Built-in meta evolve pipeline."""

    name = META_EVOLVE_PIPELINE
    description = "Default meta evolve pipeline."
    expected_outputs = ["session_results"]

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        tasks = ctx.get_artifact("input_tasks")
        if isinstance(tasks, list):
            self._populate_task_plan_from_input_tasks(
                ctx, list(tasks)
            )
        else:
            try:
                async for chunk in self.run_assess_and_plan_stream(
                    ctx
                ):
                    yield chunk
            except _StopMetaEvolvePipeline:
                return

        async for chunk in self.run_task_pipeline_stream(ctx):
            yield chunk

        self._store_session_results(ctx)

        async for chunk in self.run_learnings_stage_stream(
            ctx
        ):
            yield chunk

    def _populate_task_plan_from_input_tasks(
        self,
        ctx: SessionContext,
        tasks: list[Any],
    ) -> None:
        ctx.put_artifact(
            "task_plan",
            TaskPlanArtifact(tasks=tasks, raw_plan=""),
        )

    def _store_session_results(
        self,
        ctx: SessionContext,
    ) -> None:
        ctx.put_artifact(
            "session_results",
            SessionResultsArtifact(
                results=list(ctx.orchestrator.results)
            ),
        )

    async def run_assess_and_plan_stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        async with self._readonly_assess_workspace(ctx):
            assess_stage = AssessStage()
            assess_result_holder = []
            async for chunk in self.run_assess_stage_stream(
                ctx,
                result_holder=assess_result_holder,
            ):
                yield chunk
            if self._did_stage_fail(
                assess_stage, assess_result_holder
            ):
                raise _StopMetaEvolvePipeline

            plan_stage = PlanStage()
            plan_result_holder = []
            async for chunk in self.run_plan_stage_stream(
                ctx,
                result_holder=plan_result_holder,
            ):
                yield chunk
            if self._did_stage_fail(
                plan_stage, plan_result_holder
            ):
                raise _StopMetaEvolvePipeline

    async def run_task_pipeline_stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        task_plan = ctx.require_artifact("task_plan")
        capped_tasks = task_plan.tasks[
            : ctx.orchestrator.config.max_tasks_per_session
        ]
        for task in capped_tasks:
            if ctx.orchestrator.budget.should_stop:
                break
            if not ctx.orchestrator.budget.check_task_budget():
                break
            async for chunk in PRTaskPipeline.run_isolated_stream(
                ctx.orchestrator,
                task,
            ):
                yield chunk

    async def run_learnings_stage_stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        result_holder = []
        async for chunk in self._stream_stage(
            LearningsStage(),
            ctx,
            result_holder=result_holder,
        ):
            yield chunk
        self._require_stage_result(
            LearningsStage(), result_holder
        )

    async def run_assess_stage_stream(
        self,
        ctx: SessionContext,
        *,
        result_holder: list,
    ) -> AsyncIterator[Any]:
        async for chunk in self._stream_stage(
            AssessStage(),
            ctx,
            result_holder=result_holder,
        ):
            yield chunk

    async def run_plan_stage_stream(
        self,
        ctx: SessionContext,
        *,
        result_holder: list,
    ) -> AsyncIterator[Any]:
        async for chunk in self._stream_stage(
            PlanStage(),
            ctx,
            result_holder=result_holder,
        ):
            yield chunk

    @asynccontextmanager
    async def _readonly_assess_workspace(
        self,
        ctx: SessionContext,
    ):
        original_workspace = (
            ctx.orchestrator.config.workspace
        )
        assess_workspace = await ctx.orchestrator.worktree_mgr.prepare_readonly_snapshot(
            label="assess"
        )
        ctx.orchestrator.config.workspace = assess_workspace
        try:
            yield
        finally:
            ctx.orchestrator.config.workspace = (
                original_workspace
            )
            await ctx.orchestrator.worktree_mgr.cleanup(
                assess_workspace
            )
