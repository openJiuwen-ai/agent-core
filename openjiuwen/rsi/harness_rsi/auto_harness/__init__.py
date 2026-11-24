# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto Harness Agent — 自主优化 harness 框架的编码 agent。"""

from openjiuwen.rsi.harness_rsi.auto_harness.orchestrator import (
    AutoHarnessOrchestrator,
    create_auto_harness_orchestrator,
)
from openjiuwen.rsi.harness_rsi.auto_harness.registry import (
    PipelineRegistry,
    StageRegistry,
)
from openjiuwen.rsi.harness_rsi.auto_harness.schema import (
    PIPELINE_PREFERENCE_AUTO,
    AutoHarnessConfig,
    AutoHarnessPaths,
    CycleResult,
    Experience,
    Gap,
    OptimizationTask,
    PipelineSpec,
    ResearchContext,
    StageSpec,
    normalize_pipeline_preference,
)

__all__ = [
    "AutoHarnessConfig",
    "AutoHarnessPaths",
    "AutoHarnessOrchestrator",
    "CycleResult",
    "Experience",
    "Gap",
    "OptimizationTask",
    "PIPELINE_PREFERENCE_AUTO",
    "PipelineRegistry",
    "PipelineSpec",
    "ResearchContext",
    "StageRegistry",
    "StageSpec",
    "create_auto_harness_orchestrator",
    "normalize_pipeline_preference",
]
