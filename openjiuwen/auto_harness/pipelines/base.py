# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Base pipeline interfaces for auto-harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.schema import (
    StageResult,
)
from openjiuwen.auto_harness.stages.base import (
    BaseStage,
)
from openjiuwen.core.common.logging import (
    logger,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.contexts import (
        SessionContext,
        TaskContext,
    )


@dataclass(frozen=True)
class PipelineStageMap:
    """Slot -> stage class binding for a pipeline."""

    mapping: dict[str, type[BaseStage]] = field(
        default_factory=dict
    )

    def resolve(self, slot: str) -> BaseStage:
        """Instantiate the stage bound to *slot*."""
        cls = self.mapping.get(slot)
        if cls is None:
            raise KeyError(
                f"No stage bound for slot '{slot}'"
            )
        return cls()


class BasePipeline:
    """Base interface for explicit pipeline orchestration."""

    name = ""
    description = ""
    expected_outputs: list[str] = []
    stage_map: PipelineStageMap = PipelineStageMap()
    stage_order: list[tuple[str, str]] = []

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

    def resolve_stage(self, slot: str) -> BaseStage:
        """Instantiate the stage bound to the given slot."""
        return self.stage_map.resolve(slot)

    async def _stream_stage(
        self,
        stage: BaseStage,
        ctx: "SessionContext | TaskContext",
        *,
        result_holder: list[StageResult],
    ) -> AsyncIterator[Any]:
        """Stream one stage and capture its final result."""
        context_label = _describe_context(ctx)
        logger.info(
            "[AutoHarnessPipeline] stage start: pipeline=%s stage=%s slot=%s context=%s",
            self.name or type(self).__name__,
            stage.name,
            stage.slot or "",
            context_label,
        )
        if stage.display_name:
            yield ctx.message(
                stage.display_name,
                stage=stage.slot or stage.name,
            )
        try:
            async for event in stage.stream(ctx):
                if isinstance(event, StageResult):
                    result_holder.append(event)
                    logger.info(
                        "[AutoHarnessPipeline] stage result: pipeline=%s "
                        "stage=%s status=%s error=%s messages=%d "
                        "artifacts=%s metrics=%s context=%s",
                        self.name or type(self).__name__,
                        stage.name,
                        event.status,
                        event.error or "",
                        len(event.messages or []),
                        sorted((event.artifacts or {}).keys()),
                        dict(event.metrics or {}),
                        context_label,
                    )
                    if event.artifacts:
                        ctx.put_artifacts(event.artifacts)
                    for message in event.messages:
                        yield ctx.message(message)
                    yield event
                    continue
                yield event
        except Exception:
            logger.exception(
                "[AutoHarnessPipeline] stage exception: pipeline=%s stage=%s context=%s",
                self.name or type(self).__name__,
                stage.name,
                context_label,
            )
            raise
        finally:
            if result_holder:
                logger.info(
                    "[AutoHarnessPipeline] stage end: pipeline=%s stage=%s final_status=%s context=%s",
                    self.name or type(self).__name__,
                    stage.name,
                    result_holder[-1].status,
                    context_label,
                )
            else:
                logger.warning(
                    "[AutoHarnessPipeline] stage end without StageResult: pipeline=%s stage=%s context=%s",
                    self.name or type(self).__name__,
                    stage.name,
                    context_label,
                )

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


def _describe_context(ctx: "SessionContext | TaskContext") -> str:
    """Return compact context metadata for auto-harness pipeline logs."""
    task = getattr(ctx, "task", None)
    if task is not None:
        return (
            f"task={getattr(task, 'topic', '')} "
            f"status={getattr(task, 'status', '')}"
        )
    orchestrator = getattr(ctx, "orchestrator", None)
    runtime = getattr(orchestrator, "runtime", None)
    return (
        f"session={getattr(runtime, 'session_id', '')} "
        f"pipeline={getattr(runtime, 'selected_pipeline', '')}"
    )
