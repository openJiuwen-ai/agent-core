# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RASAgents facade — routes skill invocations to AgentAdapter."""
from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.harness.agent_ras.agents.base import (
    AgentAdapter,
    NoOpAgentAdapter,
)


class RASAgents:
    """Facade over ``AgentAdapter`` for detector skill members."""

    def __init__(self, adapter: AgentAdapter | None = None) -> None:
        self._adapter = adapter or NoOpAgentAdapter()

    async def invoke_skill(
        self,
        *,
        role: str,
        skill_name: str,
        payload: str,
        timeout: float,
    ) -> dict[str, Any]:
        try:
            result = await asyncio.wait_for(
                self._adapter.invoke_skill(
                    role=role,
                    skill_name=skill_name,
                    payload=payload,
                    timeout=timeout,
                ),
                timeout=timeout,
            )
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        warmup = getattr(self._adapter, "warmup_members", None)
        if callable(warmup):
            await warmup(roles)
