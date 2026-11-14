"""Recursive improvement for Harnesses and artifact providers."""

from importlib import import_module

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
    load_cases,
)

__all__ = [
    "AutoCoordinatingHarnessConfig",
    "DataLoaderConfig",
    "EvaluationResultAnalyzerConfig",
    "EvaluatorConfig",
    "MemberOptimizerConfig",
    "ModelConfigs",
    "OrchestratorSchedulingConfig",
    "DataLoader",
    "EvaluationResultAnalysisStrategy",
    "EvaluationResultAnalyzer",
    "TeamEvaluator",
    "MemberOptimizer",
    "ActionDefinition",
    "CaseMapping",
    "DatasetArtifact",
    "EvaluationCaseTraceRef",
    "EvaluationResultAnalysisArtifact",
    "EvaluationResultAnalysisInvocation",
    "TeamIssue",
    "IterativeSingleHarnessRequest",
    "IterativeSingleHarnessResult",
    "SingleHarnessIterativeOptimizationOrchestrator",
    "load_cases",
]

_ARTIFACT_EXPORTS = {
    "ArtifactProvider",
    "PaperArtifactProvider",
    "ProgramArtifactProvider",
    "build_request",
    "validate_artifact_task_request",
}
__all__ += sorted(_ARTIFACT_EXPORTS | {"PuctProgramArtifactProvider"})


def __getattr__(name: str):
    if name == "PuctProgramArtifactProvider":
        module = "openjiuwen.rsi.artifact_rsi.program_opt"
    elif name in _ARTIFACT_EXPORTS:
        module = "openjiuwen.rsi.artifact_rsi"
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)
