# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-level pipeline selection helpers."""

from __future__ import annotations

from typing import Any, AsyncIterator, List

from openjiuwen.auto_harness.infra.parsers import (
    extract_text,
    parse_pipeline_selection,
)
from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
    normalize_pipeline_name,
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
    """Choose the best pipeline for the task."""
    if task.pipeline_name:
        pipeline_name = normalize_pipeline_name(
            task.pipeline_name
        )
        return PipelineSelectionArtifact(
            pipeline_name=pipeline_name,
            reason="task requested explicit pipeline",
            alternatives=[],
            confidence=1.0,
            fallback_pipeline=pipeline_name,
        )
    if config.model is None:
        return PipelineSelectionArtifact(
            pipeline_name=META_EVOLVE_PIPELINE,
            reason=(
                "no model configured, fallback to "
                f"{META_EVOLVE_PIPELINE}"
            ),
            alternatives=[EXTENDED_EVOLVE_PIPELINE],
            confidence=0.0,
            fallback_pipeline=META_EVOLVE_PIPELINE,
        )

    output = ""
    async for chunk in run_select_pipeline_stream(
        config,
        task,
        assessment=assessment,
        available_pipelines=available_pipelines,
    ):
        output += extract_text(chunk)

    parsed = parse_pipeline_selection(output)
    if parsed is not None:
        return parsed
    return PipelineSelectionArtifact(
        pipeline_name=META_EVOLVE_PIPELINE,
        reason="selector fallback to default pipeline",
        alternatives=[EXTENDED_EVOLVE_PIPELINE],
        confidence=0.0,
        fallback_pipeline=META_EVOLVE_PIPELINE,
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
