# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility exports for AutoHarness after its move into RSI."""

from openjiuwen.rsi import auto_harness as _rsi_auto_harness
from openjiuwen.rsi.auto_harness import (
    PIPELINE_PREFERENCE_AUTO,
    AutoHarnessConfig,
    AutoHarnessOrchestrator,
    AutoHarnessPaths,
    CycleResult,
    Experience,
    Gap,
    OptimizationTask,
    PipelineRegistry,
    PipelineSpec,
    ResearchContext,
    StageRegistry,
    StageSpec,
    create_auto_harness_orchestrator,
    normalize_pipeline_preference,
)

# Keep old submodule imports working, for example
# ``openjiuwen.auto_harness.infra.git_auth`` used by older JiuwenSwarm builds.
__path__ = list(_rsi_auto_harness.__path__)

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
