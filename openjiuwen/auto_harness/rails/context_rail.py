# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto-harness context rail.

Keeps the useful context-processor setup from harness
``ContextEngineeringRail`` while disabling prompt-section
injection that would otherwise read workspace-local
context files and conflict with auto-harness identity.
"""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.harness.rails.context_engineering_rail import (
    ContextEngineeringRail,
)


class AutoHarnessContextRail(
    ContextEngineeringRail
):
    """Context processor rail without workspace/context prompt injection."""

    async def before_model_call(
        self, ctx: AgentCallbackContext,
    ) -> None:
        """Do not inject workspace/tools/context prompt sections."""
        return

    def uninit(self, agent) -> None:
        """Do not mutate system prompt sections on teardown."""
        return
