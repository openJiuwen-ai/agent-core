# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Drain responder-owned Jiuwen memory before closing a Team session."""
from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.schema.events import TaskAcceptedEvent
from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.harness.rails.memory.jiuwen_team_memory_rail import JiuwenTeamMemoryRail


async def submit_jiuwen_task_acceptance(harness: Any, event: TaskAcceptedEvent) -> None:
    """Dispatch the committed delivery only to its responder's registered Rails."""
    find_rails = getattr(harness, "find_rails", None)
    if callable(find_rails):
        for rail in find_rails(JiuwenTeamMemoryRail) or ():
            try:
                await rail.on_task_accepted(event)
            except Exception as exc:
                logger.warning("[TeamMemory] acceptance failed: %s", type(exc).__name__)


def record_jiuwen_task_delivery(harness: Any, task_id: str, version: str) -> None:
    """Link a task mutation to the native execution that delivered it."""
    find_rails = getattr(harness, "find_rails", None)
    if callable(find_rails):
        for rail in find_rails(JiuwenTeamMemoryRail) or ():
            try:
                rail.record_task_delivery(task_id, version)
            except Exception as exc:
                logger.warning("[TeamMemory] delivery failed: %s", type(exc).__name__)


async def drain_jiuwen_team_memory(host: Any) -> None:
    """Wait for submitted writes while the Team session is still available."""
    harness = host.resources.harness
    find_rails = getattr(harness, "find_rails", None)
    if callable(find_rails):
        for rail in find_rails(JiuwenTeamMemoryRail):
            try:
                await rail.drain()
            except Exception as exc:
                logger.warning("[TeamMemory] drain failed: %s", type(exc).__name__)
