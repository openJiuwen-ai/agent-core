# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RASAgents — platform-agnostic orchestration for semantic skill invokes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.agent_ras.agents.base import (
    AgentAdapter,
    NoOpAgentAdapter,
    build_inline_skill_query,
)
from openjiuwen.harness.agent_ras.detectors.skill_verdicts import (
    parse_skill_verdict,
    verdict_to_dict,
)


class RASAgents:
    """Orchestrate skill invoke across any ``AgentAdapter`` platform.

    Owns: task framing, SKILL pathlib inline, timeout, fail-open, verdict parse.
    Does not own: how a platform runs the LLM/agent (that is ``AgentAdapter.run``).
    """

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
        if role == "recovery":
            task_block = f"恢复材料:\n{payload}"
        else:
            task_block = f"待判定 excerpt:\n{payload}"

        if timeout <= 0:
            logger.warning(
                "Agent RAS semantic %s skill=%s fail_open=True reason=non_positive_timeout timeout=%.1f",
                role,
                skill_name,
                timeout,
            )
            return {}

        query = build_inline_skill_query(
            role=role,
            skill_name=skill_name,
            task_block=task_block,
        )
        t0 = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                self._adapter.run(
                    role=role,
                    skill_name=skill_name,
                    query=query,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            logger.warning(
                "Agent RAS semantic %s skill=%s timed out fail_open=True elapsed_sec=%.1f limit_sec=%.1f",
                role,
                skill_name,
                elapsed,
                timeout,
            )
            return {}
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.warning(
                "Agent RAS semantic %s skill=%s failed fail_open=True elapsed_sec=%.1f error_type=%s",
                role,
                skill_name,
                elapsed,
                type(exc).__name__,
                exc_info=True,
            )
            return {}

        elapsed = time.monotonic() - t0
        logger.info(
            "Agent RAS semantic skill completed role=%s skill=%s elapsed_ms=%d",
            role,
            skill_name,
            int(elapsed * 1000),
        )

        if raw in ("{}", "", None) or raw == {}:
            logger.warning(
                "Agent RAS semantic %s skill=%s fail_open=True reason=empty_result elapsed_ms=%d",
                role,
                skill_name,
                int(elapsed * 1000),
            )
            return {}

        verdict = parse_skill_verdict(skill_name, raw)
        if verdict.fail_open_reason:
            logger.warning(
                "Agent RAS semantic %s skill=%s fail_open=True reason=%s elapsed_ms=%d",
                role,
                skill_name,
                verdict.fail_open_reason,
                int(elapsed * 1000),
            )
        if role in ("detection", "recovery"):
            return verdict_to_dict(verdict)
        return verdict.raw or {}

    async def warmup_members(self, roles: tuple[str, ...]) -> None:
        await self._adapter.warmup_members(roles)


__all__ = ["RASAgents"]
