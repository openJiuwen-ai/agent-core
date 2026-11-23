# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bridge Team round closure to the responder-owned Jiuwen Rail."""
from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.memory.config import TeamMemoryConfig
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.harness.rails.memory.jiuwen_team_memory_rail import JiuwenTeamMemoryRail


async def extract_jiuwen_team_round(host: Any) -> None:
    """Run before releasing the Team session; native memory remains independent."""
    if host.role != TeamRole.LEADER:
        return
    harness = host.resources.harness
    if harness is None:
        return
    find_rails = getattr(harness, "find_rails", None)
    if not callable(find_rails):
        return
    try:
        rails = find_rails(JiuwenTeamMemoryRail)
        if not rails:
            return
        from openjiuwen.agent_teams.memory.extractor import _build_extraction_context

        tasks = await host.infra.task_manager.list_tasks() if host.infra.task_manager else []
        backend = host.infra.team_backend
        messages = await backend.db.message.get_team_messages(host.team_name) if backend else []
        if not tasks and not messages:
            return
        memory_config = getattr(getattr(host, "spec", None), "memory", None)
        if memory_config is None:
            memory_config = TeamMemoryConfig()
        material = _build_extraction_context(tasks, messages, memory_config.timezone_offset_hours)
        for rail in rails:
            await rail.drain()
            await rail.conclude_round(material, model=harness.model)
    except Exception as exc:
        logger.warning("[TeamMemory] round extraction failed: %s", type(exc).__name__)
