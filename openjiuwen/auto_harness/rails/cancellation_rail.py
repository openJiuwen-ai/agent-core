# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancellation rail — check orchestrator.should_cancel and request force finish."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.auto_harness.orchestrator import (
        AutoHarnessOrchestrator,
    )

logger = logging.getLogger(__name__)


class CancellationRail(DeepAgentRail):
    """Check orchestrator.should_cancel and request force finish.

    Registered on stream_rails, triggered at agent callbacks.
    When orchestrator.cancel() is called, this rail will detect it
    at the next agent checkpoint (before/after tool/model call).

    Args:
        orchestrator: The orchestrator to monitor (bound after creation).
    """

    priority = 100  # Run early to catch cancellation quickly

    def __init__(self) -> None:
        super().__init__()
        self._orchestrator: "AutoHarnessOrchestrator | None" = None

    def bind(self, orchestrator: "AutoHarnessOrchestrator") -> None:
        """Bind the orchestrator reference after creation."""
        self._orchestrator = orchestrator

    async def before_tool_call(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        """Check cancellation before each tool call."""
        if self._orchestrator and self._orchestrator.should_cancel:
            logger.info(
                "[CancellationRail] cancellation detected, requesting force_finish"
            )
            ctx.request_force_finish(
                {"reason": "user_cancelled", "cancelled": True}
            )

    async def after_model_call(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        """Check cancellation after each model call."""
        if self._orchestrator and self._orchestrator.should_cancel:
            logger.info(
                "[CancellationRail] cancellation detected, requesting force_finish"
            )
            ctx.request_force_finish(
                {"reason": "user_cancelled", "cancelled": True}
            )


__all__ = ["CancellationRail"]