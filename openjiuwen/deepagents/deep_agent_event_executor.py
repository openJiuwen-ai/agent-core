# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent task-executor skeleton."""
from __future__ import annotations

from typing import AsyncIterator, Tuple, cast

from openjiuwen.core.controller.modules import TaskExecutor
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutputChunk,
)
from openjiuwen.core.session.agent import Session


class DeepAgentEventExecutor(TaskExecutor):
    """Placeholder TaskExecutor for DeepAgent controller integration."""

    async def execute_ability(
        self,
        task_id: str,
        session: Session,
    ) -> AsyncIterator[ControllerOutputChunk]:
        _ = task_id
        _ = session
        if False:
            yield cast(ControllerOutputChunk, None)

    async def can_pause(
        self,
        task_id: str,
        session: Session,
    ) -> Tuple[bool, str]:
        _ = task_id
        _ = session
        return False, "not implemented"

    async def pause(
        self,
        task_id: str,
        session: Session,
    ) -> bool:
        _ = task_id
        _ = session
        return False

    async def can_cancel(
        self,
        task_id: str,
        session: Session,
    ) -> Tuple[bool, str]:
        _ = task_id
        _ = session
        return False, "not implemented"

    async def cancel(
        self,
        task_id: str,
        session: Session,
    ) -> bool:
        _ = task_id
        _ = session
        return False


__all__ = [
    "DeepAgentEventExecutor",
]
