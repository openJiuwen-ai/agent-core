# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Plan 阶段 — 用 DeepAgent 生成任务列表。"""

from __future__ import annotations

import logging
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    List,
)

from openjiuwen.auto_harness.infra.parsers import (
    parse_tasks,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    OptimizationTask,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )

logger = logging.getLogger(__name__)


async def run_plan_stream(
    config: AutoHarnessConfig,
    assessment: str,
    experience_store: "ExperienceStore",
) -> AsyncIterator[Any]:
    """用 DeepAgent 生成任务列表（流式）。

    Args:
        config: Auto Harness 配置。
        assessment: 评估报告文本。
        experience_store: ExperienceStore 实例。

    Yields:
        OutputSchema chunks from plan agent.
    """
    from openjiuwen.auto_harness.agent import (
        create_plan_agent,
    )

    agent = create_plan_agent(config)
    query = await _build_plan_query(
        config, assessment, experience_store,
    )

    async for chunk in agent.stream(
        {"query": query},
    ):
        yield chunk


async def run_plan(
    config: AutoHarnessConfig,
    assessment: str,
    experience_store: "ExperienceStore",
) -> List[OptimizationTask]:
    """用 DeepAgent 生成任务列表（非流式）。

    Args:
        config: Auto Harness 配置。
        assessment: 评估报告文本。
        experience_store: ExperienceStore 实例。

    Returns:
        解析后的 OptimizationTask 列表。
    """
    from openjiuwen.auto_harness.agent import (
        create_plan_agent,
    )

    agent = create_plan_agent(config)
    query = await _build_plan_query(
        config, assessment, experience_store,
    )

    result = await agent.invoke({"query": query})
    output = result.get("output", "")
    return parse_tasks(output)


async def _build_plan_query(
    config: AutoHarnessConfig,
    assessment: str,
    experience_store: "ExperienceStore",
) -> str:
    """组装 plan agent 的 query。"""
    recent = await experience_store.list_recent(limit=5)
    experiences_text = "\n".join(
        f"- [{e.type.value}] {e.topic}: "
        f"{e.summary}"
        for e in recent
    ) or "无"

    return (
        f"本轮目标:\n"
        f"{config.optimization_goal or '无'}\n\n"
        f"重点竞品:\n"
        f"{config.competitor or '无'}\n\n"
        f"评估报告:\n{assessment}\n\n"
        f"近期经验:\n{experiences_text}\n\n"
        f"最大任务数: "
        f"{config.max_tasks_per_session}\n"
        f"自驱动槽位: "
        f"{config.self_driven_slots}\n"
    )
