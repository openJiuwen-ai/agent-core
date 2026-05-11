# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-level pipeline selection helpers."""

from __future__ import annotations

from typing import Any, AsyncIterator, List

from openjiuwen.auto_harness.infra.pipeline_selector import (
    choose_session_pipeline,
)
from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    PipelineSelectionArtifact,
    OptimizationTask,
)


async def run_select_pipeline_stream(
    config: AutoHarnessConfig,
    task: OptimizationTask,
    *,
    assessment: str = "",
    available_pipelines: List[str] | None = None,
) -> AsyncIterator[Any]:
    """Run the selector agent in stream mode."""
    from openjiuwen.auto_harness.agents import (
        create_select_pipeline_agent,
    )

    agent = create_select_pipeline_agent(config)
    query = _build_query(
        task,
        assessment=assessment,
        available_pipelines=available_pipelines
        or [META_EVOLVE_PIPELINE],
    )
    async for chunk in agent.stream(
        {"query": query}
    ):
        yield chunk


async def run_select_pipeline(
    config: AutoHarnessConfig,
    task: OptimizationTask,
    *,
    assessment: str = "",
    available_pipelines: List[str] | None = None,
) -> PipelineSelectionArtifact:
    """Choose the configured or auto-detected pipeline for the task."""
    _ = assessment
    return choose_session_pipeline(
        tasks=[task],
        config=config,
        available_pipelines=available_pipelines
        or [
            META_EVOLVE_PIPELINE,
            EXTENDED_EVOLVE_PIPELINE,
        ],
    )


def _build_query(
    task: OptimizationTask,
    *,
    assessment: str,
    available_pipelines: List[str],
) -> str:
    """Build selector prompt context."""
    summary = assessment.strip()
    if len(summary) > 4000:
        summary = f"{summary[:3997].rstrip()}..."
    return (
        f"任务主题: {task.topic}\n"
        f"任务描述: {task.description or '无'}\n"
        f"目标文件: {', '.join(task.files) or '未指定'}\n"
        f"评估摘要:\n{summary or '无'}\n\n"
        f"可选 pipeline:\n"
        + "\n".join(
            f"- {name}" for name in available_pipelines
        )
    )
