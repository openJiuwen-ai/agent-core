# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Base stage interfaces for auto-harness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, TypeAlias

from openjiuwen.auto_harness.schema import (
    StageResult,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.contexts import (
        SessionContext,
        TaskContext,
    )

StageEvent: TypeAlias = OutputSchema | StageResult


def scope_output_event_stage(
    event: Any,
    stage: str,
) -> Any:
    """Scope nested agent progress events to the outer stage."""
    if not stage:
        return event
    if not isinstance(event, OutputSchema):
        return event
    if event.type not in {"message", "stage_result"}:
        return event
    if not isinstance(event.payload, dict):
        return event

    payload = dict(event.payload)
    if payload.get("stage") == stage:
        return event
    payload["stage"] = stage
    return event.model_copy(update={"payload": payload})


class BaseStage:
    """Base interface for all stages."""

    name = ""
    display_name = ""
    description = ""
    slot: str = ""
    consumes: list[str] = []
    produces: list[str] = []
    scope = "session"

    @classmethod
    def spec(cls):
        """Return the stage metadata."""
        from openjiuwen.auto_harness.schema import (
            StageSpec,
        )

        return StageSpec(
            name=cls.name,
            stage_cls=cls,
            description=cls.description,
            consumes=list(cls.consumes),
            produces=list(cls.produces),
            scope=cls.scope,
            slot=cls.slot,
        )

    async def stream(
        self,
        ctx: "SessionContext | TaskContext",
    ) -> AsyncIterator[StageEvent]:
        """Execute the stage as a stream."""
        raise NotImplementedError


class SessionStage(BaseStage):
    """Base class for session-scoped stages."""

    scope = "session"

    async def stream(
        self,
        ctx: "SessionContext",
    ) -> AsyncIterator[StageEvent]:
        raise NotImplementedError


class TaskStage(BaseStage):
    """Base class for task-scoped stages."""

    scope = "task"

    async def stream(
        self,
        ctx: "TaskContext",
    ) -> AsyncIterator[StageEvent]:
        raise NotImplementedError
