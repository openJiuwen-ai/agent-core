# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Implement stage for task-scoped code changes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from openjiuwen.auto_harness.contexts import (
    TaskContext,
)
from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
)
from openjiuwen.auto_harness.schema import (
    CodeChangeArtifact,
    Experience,
    OptimizationTask,
    StageResult,
)
from openjiuwen.auto_harness.stages.base import (
    TaskStage,
)
from openjiuwen.auto_harness.stages.verify import (
    _format_ci_status_for_evaluator,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import (
        DeepAgent,
    )

logger = logging.getLogger(__name__)


async def run_implement_stream(
    agent: "DeepAgent | None",
    task: OptimizationTask,
    related: list[Experience],
) -> AsyncIterator[Any]:
    """Stream task implementation through the task agent."""
    if agent is None:
        logger.warning("No agent, skipping implement")
        return
    context_parts: list[str] = []
    for exp in related:
        context_parts.append(
            f"- [{exp.type.value}] {exp.topic}: {exp.summary}"
        )
    context = "\n".join(context_parts) or "无"
    prompt = (
        f"任务: {task.topic}\n"
        f"描述: {task.description}\n"
        f"目标文件: {', '.join(task.files) or '自行判断'}\n"
        f"\n相关经验:\n{context}\n"
        "\n本阶段只允许完成代码修改与局部验证。"
        "\n严禁执行 git add、git commit 或其他提交动作；"
        "提交只允许在后续独立 commit phase 中进行。"
    )
    async for chunk in agent.stream({"query": prompt}):
        yield chunk


class ImplementStage(TaskStage):
    """Execute code changes for the current task."""

    name = "implement"
    description = "Run the implement stage for PR pipeline."
    produces = ["code_change"]

    async def stream(
        self,
        ctx: TaskContext,
    ) -> AsyncIterator[Any]:
        messages = [f"任务准备就绪: {ctx.task.topic}"]
        yield ctx.message("[1/5] 执行代码修改")
        async for chunk in run_implement_stream(
            ctx.runtime.task_agent,
            ctx.task,
            ctx.runtime.related,
        ):
            yield chunk
        yield StageResult(
            artifacts={
                "code_change": CodeChangeArtifact(
                    related=ctx.runtime.related,
                    edited_files=ctx.runtime.edit_safety_rail.edited_files(),
                )
            },
            messages=messages,
        )
