# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-level pipeline selection helpers."""

from __future__ import annotations

import re
from typing import Iterable

from openjiuwen.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
)
from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    OptimizationTask,
    PIPELINE_PREFERENCE_AUTO,
    PipelineSelectionArtifact,
    normalize_pipeline_preference,
)

_COMPETITOR_SIGNAL_KEYWORDS = (
    "竞品",
    "competitor",
    "吸收",
    "absorb",
    "hermes",
    "evolver",
    "devin",
    "cursor",
    "扩展能力",
    "extend capability",
)
_COMPETITOR_SIGNAL_PATTERNS = (
    re.compile(r"把.+的.+能力.+加入"),
    re.compile(r"学习.+的做法"),
)


def detect_pipeline_signal(
    tasks: Iterable[OptimizationTask] | None,
    config: AutoHarnessConfig,
) -> str | None:
    """Detect whether the session should route to extended evolve."""
    text_parts = []
    for task in tasks or []:
        text_parts.append(task.topic or "")
        text_parts.append(task.description or "")
        text_parts.append(task.expected_effect or "")
    if config.optimization_goal:
        text_parts.append(config.optimization_goal)
    text = " ".join(part for part in text_parts if part).lower()
    if not text:
        return None
    if any(keyword in text for keyword in _COMPETITOR_SIGNAL_KEYWORDS):
        return EXTENDED_EVOLVE_PIPELINE
    if any(pattern.search(text) for pattern in _COMPETITOR_SIGNAL_PATTERNS):
        return EXTENDED_EVOLVE_PIPELINE
    return None


def choose_session_pipeline(
    *,
    tasks: list[OptimizationTask] | None,
    config: AutoHarnessConfig,
    available_pipelines: list[str],
) -> PipelineSelectionArtifact:
    """Choose the session pipeline from config preference and signals."""
    preference = normalize_pipeline_preference(
        config.pipeline_preference
    )
    if (
        preference != PIPELINE_PREFERENCE_AUTO
        and preference in available_pipelines
    ):
        alternatives = [
            name
            for name in available_pipelines
            if name != preference
        ]
        return PipelineSelectionArtifact(
            pipeline_name=preference,
            reason="config pipeline preference",
            alternatives=alternatives,
            confidence=1.0,
            fallback_pipeline=preference,
        )

    signal = detect_pipeline_signal(tasks, config)
    if signal and signal in available_pipelines:
        alternatives = [
            name for name in available_pipelines if name != signal
        ]
        return PipelineSelectionArtifact(
            pipeline_name=signal,
            reason="auto signal matched extended evolve pipeline",
            alternatives=alternatives,
            confidence=0.85,
            fallback_pipeline=META_EVOLVE_PIPELINE,
        )

    pipeline_name = (
        META_EVOLVE_PIPELINE
        if META_EVOLVE_PIPELINE in available_pipelines
        else available_pipelines[0]
    )
    alternatives = [
        name
        for name in available_pipelines
        if name != pipeline_name
    ]
    return PipelineSelectionArtifact(
        pipeline_name=pipeline_name,
        reason="auto default meta evolve pipeline",
        alternatives=alternatives,
        confidence=0.7,
        fallback_pipeline=pipeline_name,
    )


__all__ = [
    "choose_session_pipeline",
    "detect_pipeline_signal",
]
