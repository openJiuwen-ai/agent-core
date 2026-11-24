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
    "EXTENDED_EVOLVE_PIPELINE",
    "META_EVOLVE_PIPELINE",
    "AutoCoordinatingHarnessConfig",
    # Downstream-facing AutoHarness exports (lazy; see __getattr__).
    "AutoHarnessConfig",
    "AutoHarnessOrchestrator",
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
    "ExtendActivateStage",
    "ExtensionDesign",
    "ExtensionTaskPipeline",
    "IterativeSingleHarnessRequest",
    "IterativeSingleHarnessResult",
    "OptimizationTask",
    "RuntimeExtensionArtifact",
    "SingleHarnessIterativeOptimizationOrchestrator",
    "StageResult",
    "TaskContext",
    "TaskRuntime",
    "build_git_auth_env",
    "create_auto_harness_orchestrator",
    "load_auto_harness_config",
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

# Map public AutoHarness symbols to their defining modules so
# `import openjiuwen.rsi` does not eagerly pull DeepAgent/security deps.
_AUTO_HARNESS_EXPORTS = {
    "EXTENDED_EVOLVE_PIPELINE": "openjiuwen.rsi.harness_rsi.auto_harness.pipelines",
    "META_EVOLVE_PIPELINE": "openjiuwen.rsi.harness_rsi.auto_harness.pipelines",
    "AutoHarnessConfig": "openjiuwen.rsi.harness_rsi.auto_harness",
    "AutoHarnessOrchestrator": "openjiuwen.rsi.harness_rsi.auto_harness",
    "ExtendActivateStage": "openjiuwen.rsi.harness_rsi.auto_harness.stages.activate",
    "ExtensionDesign": "openjiuwen.rsi.harness_rsi.auto_harness.schema",
    "ExtensionTaskPipeline": (
        "openjiuwen.rsi.harness_rsi.auto_harness.pipelines.extended_evolve_pipeline"
    ),
    "OptimizationTask": "openjiuwen.rsi.harness_rsi.auto_harness.schema",
    "RuntimeExtensionArtifact": "openjiuwen.rsi.harness_rsi.auto_harness.schema",
    "StageResult": "openjiuwen.rsi.harness_rsi.auto_harness.schema",
    "TaskContext": "openjiuwen.rsi.harness_rsi.auto_harness.contexts",
    "TaskRuntime": "openjiuwen.rsi.harness_rsi.auto_harness.contexts",
    "build_git_auth_env": "openjiuwen.rsi.harness_rsi.auto_harness.infra.git_auth",
    "create_auto_harness_orchestrator": "openjiuwen.rsi.harness_rsi.auto_harness",
    "load_auto_harness_config": "openjiuwen.rsi.harness_rsi.auto_harness.schema",
}


def __getattr__(name: str):
    if name == "PuctProgramArtifactProvider":
        module = "openjiuwen.rsi.artifact_rsi.program_opt"
    elif name in _ARTIFACT_EXPORTS:
        module = "openjiuwen.rsi.artifact_rsi"
    elif name in _AUTO_HARNESS_EXPORTS:
        module = _AUTO_HARNESS_EXPORTS[name]
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)
