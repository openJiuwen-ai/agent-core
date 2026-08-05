# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility exports for AutoHarness after its move into RSI."""

import sys

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

# Keep legacy submodule imports working without duplicating the implementation.
# For example, ``openjiuwen.auto_harness.schema`` resolves from the RSI package.
for _module_name, _module in tuple(sys.modules.items()):
    if _module_name.startswith("openjiuwen.rsi.auto_harness."):
        _legacy_name = _module_name.replace("openjiuwen.rsi.auto_harness", __name__, 1)
        sys.modules.setdefault(_legacy_name, _module)
__path__.extend(_rsi_auto_harness.__path__)

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
