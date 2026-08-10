# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public Symphony fingerprint, evaluation, graph, and orchestration APIs."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

from openjiuwen.symphony.evaluation import EvaluationContext, EvaluationSuite, EvaluationWindow, Evaluator
from openjiuwen.symphony.interfaces import (
    AtomicCapabilityProvider,
    CapabilityProvider,
    SymphonyLLM,
)
from openjiuwen.symphony.models import (
    CapabilityCall,
    CapabilityDescriptor,
    CapabilityFingerprint,
    CapabilityIO,
    EvaluationCase,
    EvidenceRef,
    FailureReason,
    FailureSeverity,
    FingerprintArtifact,
    ImprovementSuggestion,
    MetricResult,
    MetricStatus,
    QualityConfidence,
    QualityResult,
    SemanticProfile,
    SourceSnapshot,
    SuggestionPriority,
)
from openjiuwen.symphony.orchestration import (
    CapabilityGraph,
    GraphArtifactStatus,
    GraphBuildResult,
    OrchestrationConfig,
    OrchestrationPlan,
    OrchestrationProgress,
    OrchestrationService,
    PrepareArtifactHook,
)
from openjiuwen.symphony.runtime import SymphonyRuntime
from openjiuwen.symphony.shared import ArtifactSpec, Fingerprint, ParameterSpec
from openjiuwen.symphony.shared.fingerprint import (
    FINGERPRINT_ARTIFACT_FILENAME,
    FINGERPRINT_SCHEMA_VERSION,
    FingerprintService,
    FingerprintSettings,
    IONameVocabulary,
    ScanDiagnostic,
    ScanResult,
    SkillFolderScanner,
    SkillManifestParser,
)

if TYPE_CHECKING:
    from openjiuwen.symphony import agent as agent
    from openjiuwen.symphony import retrieval as retrieval
    from openjiuwen.symphony import shared as shared

CapabilityInput = ParameterSpec
CapabilityOutput = ArtifactSpec
_LAZY_MODULES = frozenset({"agent", "retrieval", "shared"})

__all__ = [
    "FINGERPRINT_ARTIFACT_FILENAME",
    "FINGERPRINT_SCHEMA_VERSION",
    "ArtifactSpec",
    "AtomicCapabilityProvider",
    "CapabilityCall",
    "CapabilityDescriptor",
    "CapabilityFingerprint",
    "CapabilityGraph",
    "CapabilityIO",
    "CapabilityInput",
    "CapabilityOutput",
    "CapabilityProvider",
    "EvaluationCase",
    "EvaluationContext",
    "EvaluationSuite",
    "EvaluationWindow",
    "Evaluator",
    "EvidenceRef",
    "FailureReason",
    "FailureSeverity",
    "Fingerprint",
    "FingerprintArtifact",
    "FingerprintService",
    "FingerprintSettings",
    "GraphArtifactStatus",
    "GraphBuildResult",
    "IONameVocabulary",
    "ImprovementSuggestion",
    "MetricResult",
    "MetricStatus",
    "OrchestrationConfig",
    "OrchestrationPlan",
    "OrchestrationProgress",
    "OrchestrationService",
    "ParameterSpec",
    "PrepareArtifactHook",
    "QualityConfidence",
    "QualityResult",
    "ScanDiagnostic",
    "ScanResult",
    "SemanticProfile",
    "SkillFolderScanner",
    "SkillManifestParser",
    "SourceSnapshot",
    "SuggestionPriority",
    "SymphonyLLM",
    "SymphonyRuntime",
    "agent",
    "retrieval",
    "shared",
]


def __getattr__(name: str) -> ModuleType:
    if name in _LAZY_MODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
