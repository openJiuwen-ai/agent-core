# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Recursive self-improvement for standalone Expert Harnesses."""

from openjiuwen.rsi.harness_rsi.auto_harness import (
    AutoHarnessConfig,
    AutoHarnessOrchestrator,
    create_auto_harness_orchestrator,
)
from openjiuwen.rsi.harness_rsi.auto_harness.contexts import (
    TaskContext,
    TaskRuntime,
)
from openjiuwen.rsi.harness_rsi.auto_harness.infra.git_auth import build_git_auth_env
from openjiuwen.rsi.harness_rsi.auto_harness.pipelines import (
    EXTENDED_EVOLVE_PIPELINE,
    META_EVOLVE_PIPELINE,
)
from openjiuwen.rsi.harness_rsi.auto_harness.pipelines.extended_evolve_pipeline import (
    ExtensionTaskPipeline,
)
from openjiuwen.rsi.harness_rsi.auto_harness.schema import (
    ExtensionDesign,
    OptimizationTask,
    RuntimeExtensionArtifact,
    StageResult,
    load_auto_harness_config,
)
from openjiuwen.rsi.harness_rsi.auto_harness.stages.activate import ExtendActivateStage

from openjiuwen.rsi.harness_rsi.config import (
    AutoCoordinatingHarnessConfig,
    DataLoaderConfig,
    EvaluationResultAnalyzerConfig,
    EvaluatorConfig,
    MemberOptimizerConfig,
    ModelConfigs,
    OrchestratorSchedulingConfig,
)
from openjiuwen.rsi.harness_rsi.data_loader import DataLoader
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import (
    EvaluationResultAnalysisStrategy,
    EvaluationResultAnalyzer,
)
from openjiuwen.rsi.harness_rsi.evaluator import TeamEvaluator
from openjiuwen.rsi.harness_rsi.member_optimizer import MemberOptimizer
from openjiuwen.rsi.harness_rsi.schema import (
    ActionDefinition,
    CaseMapping,
    DatasetArtifact,
    EvaluationCaseTraceRef,
    EvaluationResultAnalysisArtifact,
    EvaluationResultAnalysisInvocation,
    TeamIssue,
)
from openjiuwen.rsi.harness_rsi.single_harness import (
    IterativeSingleHarnessRequest,
    IterativeSingleHarnessResult,
    SingleHarnessIterativeOptimizationOrchestrator,
)

__all__ = [
    "ActionDefinition",
    "AutoCoordinatingHarnessConfig",
    "CaseMapping",
    "DataLoader",
    "DataLoaderConfig",
    "DatasetArtifact",
    "EvaluationCaseTraceRef",
    "EvaluationResultAnalysisArtifact",
    "EvaluationResultAnalysisInvocation",
    "EvaluationResultAnalysisStrategy",
    "EvaluationResultAnalyzer",
    "EvaluationResultAnalyzerConfig",
    "EvaluatorConfig",
    "MemberOptimizer",
    "MemberOptimizerConfig",
    "ModelConfigs",
    "OrchestratorSchedulingConfig",
    "IterativeSingleHarnessRequest",
    "IterativeSingleHarnessResult",
    "SingleHarnessIterativeOptimizationOrchestrator",
    "TeamEvaluator",
    "TeamIssue",
    # Downstream-facing AutoHarness exports (JiuwenSwarm and other integrators).
    "AutoHarnessConfig",
    "AutoHarnessOrchestrator",
    "EXTENDED_EVOLVE_PIPELINE",
    "ExtendActivateStage",
    "ExtensionDesign",
    "ExtensionTaskPipeline",
    "META_EVOLVE_PIPELINE",
    "OptimizationTask",
    "RuntimeExtensionArtifact",
    "StageResult",
    "TaskContext",
    "TaskRuntime",
    "build_git_auth_env",
    "create_auto_harness_orchestrator",
    "load_auto_harness_config",
]
