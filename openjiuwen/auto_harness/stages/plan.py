# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Plan 阶段 — 用 DeepAgent 生成任务列表。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    List,
)

from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
    parse_tasks,
)
from openjiuwen.auto_harness.stages.base import (
    SessionStage,
)
from openjiuwen.auto_harness.contexts import (
    SessionContext,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    OptimizationTask,
    StageResult,
    TaskPlanArtifact,
)

if TYPE_CHECKING:
    from openjiuwen.auto_harness.experience.experience_store import (
        ExperienceStore,
    )

logger = logging.getLogger(__name__)


class PlanStage(SessionStage):
    """Generate the task plan for the current session."""

    name = "plan"
    description = "Plan optimization tasks."
    consumes = ["assessment"]
    produces = ["task_plan"]

    async def stream(
        self,
        ctx: SessionContext,
    ) -> AsyncIterator[Any]:
        assessment_artifact = ctx.get_artifact("assessment")
        assessment = getattr(
            assessment_artifact,
            "report",
            "",
        )
        messages: list[str] = []
        yield ctx.message("[Phase A2] 制定优化计划...")
        plan_text = ""
        async for chunk in run_plan_stream(
            ctx.orchestrator.config,
            assessment,
            ctx.orchestrator.experience_store,
        ):
            text = extract_text(chunk)
            if text:
                plan_text += text
            yield chunk
        if plan_text.strip():
            path = (
                Path(ctx.orchestrator.paths.runs_dir)
                / "latest_plan.md"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(plan_text, encoding="utf-8")
            messages.append(
                f"规划原始输出已保存: {path}"
            )
        tasks = parse_tasks(plan_text)
        if not tasks:
            messages.append("规划阶段未生成任务，session 结束")
        yield StageResult(
            artifacts={
                "task_plan": TaskPlanArtifact(
                    tasks=list(tasks),
                    raw_plan=plan_text,
                )
            },
            messages=messages,
        )


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
    from openjiuwen.auto_harness.agents import (
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
