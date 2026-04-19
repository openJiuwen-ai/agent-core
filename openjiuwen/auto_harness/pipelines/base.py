# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Base pipeline interfaces for auto-harness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.schema import (
    StageResult,
)
from openjiuwen.auto_harness.stages.base import (
    BaseStage,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.contexts import (
        SessionContext,
        TaskContext,
    )


class BasePipeline:
    """Base interface for explicit pipeline orchestration."""

    name = ""
    description = ""
    expected_outputs: list[str] = []

    @classmethod
    def spec(cls):
        """Return the pipeline metadata."""
        from openjiuwen.auto_harness.schema import (
            PipelineSpec,
        )

        return PipelineSpec(
            name=cls.name,
            pipeline_cls=cls,
            description=cls.description,
            expected_outputs=list(cls.expected_outputs),
        )

    async def stream(
        self,
        ctx: "SessionContext | TaskContext",
    ) -> AsyncIterator[Any]:
        """Execute the pipeline."""
        raise NotImplementedError

    async def _stream_stage(
        self,
        stage: BaseStage,
        ctx: "SessionContext | TaskContext",
        *,
        result_holder: list[StageResult],
    ) -> AsyncIterator[Any]:
        """Stream one stage and capture its final result."""
        async for event in stage.stream(ctx):
            if isinstance(event, StageResult):
                result_holder.append(event)
                if event.artifacts:
                    ctx.put_artifacts(event.artifacts)
                for message in event.messages:
                    yield ctx.message(message)
                continue
            yield event

    @staticmethod
    def _require_stage_result(
        stage: BaseStage,
        result_holder: list[StageResult],
    ) -> StageResult:
        """Return the final stage result or fail loudly."""
        if not result_holder:
            raise RuntimeError(
                f"Stage '{stage.name}' did not return a StageResult"
            )
        return result_holder[-1]

    def _did_stage_fail(
        self,
        stage: BaseStage,
        result_holder: list[StageResult],
    ) -> bool:
        """Return whether the stage ended in failure."""
        return (
            self._require_stage_result(stage, result_holder).status
            == "failed"
        )
