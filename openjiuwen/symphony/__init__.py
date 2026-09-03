# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public Symphony fingerprint, evaluation, graph, and orchestration APIs."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

from openjiuwen.symphony.evaluation import EvaluationContext, EvaluationSuite, EvaluationWindow, Evaluator
from openjiuwen.symphony.graph_engine import SymphonyGraphEngine
from openjiuwen.symphony.interfaces import (
    AtomicCapabilityProvider,
    CapabilityProvider,
    SkillGraphUpdater,
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
from openjiuwen.symphony.observation import (
    GRAPH_EVOLUTION_INPUT_SCHEMA,
    CapabilityEvidence,
    EvidenceStrength,
    EvolutionGraph,
    EvolutionGraphEdge,
    EvolutionGraphNode,
    EvolutionEdgeMetadata,
    FailureDomain,
    GraphEvolutionInput,
    GraphSnapshot,
    GraphSnapshotRef,
    ObservationReceipt,
    PortMapping,
    TaskEvidence,
    TaskOutcome,
    TaskOutcomeLabel,
    TraceEvidence,
)
from openjiuwen.symphony.orchestration import (
    CapabilityGraph,
    GraphArtifactStatus,
    GraphBuildResult,
    GraphMutationDelta,
    GraphMutationResult,
    OrchestrationConfig,
    OrchestrationPlan,
    OrchestrationProgress,
    OrchestrationService,
    PrepareArtifactHook,
)
from openjiuwen.symphony.orchestration.artifacts import GraphArtifactStore
from openjiuwen.symphony.runtime import SymphonyRuntime
from openjiuwen.symphony.shared import ArtifactSpec, Fingerprint, ParameterSpec, normalize_name_key
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
    from openjiuwen.symphony import discovery as discovery
    from openjiuwen.symphony import retrieval as retrieval
    from openjiuwen.symphony import shared as shared

CapabilityInput = ParameterSpec
CapabilityOutput = ArtifactSpec
_LAZY_MODULES = frozenset({"agent", "discovery", "retrieval", "shared"})

__all__ = [
    "FINGERPRINT_ARTIFACT_FILENAME",
    "FINGERPRINT_SCHEMA_VERSION",
    "GRAPH_EVOLUTION_INPUT_SCHEMA",
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
    "CapabilityEvidence",
    "EvidenceStrength",
    "EvaluationCase",
    "EvaluationContext",
    "EvaluationSuite",
    "EvaluationWindow",
    "EvolutionGraph",
    "EvolutionGraphEdge",
    "EvolutionGraphNode",
    "EvolutionEdgeMetadata",
    "Evaluator",
    "EvidenceRef",
    "FailureReason",
    "FailureSeverity",
    "FailureDomain",
    "Fingerprint",
    "FingerprintArtifact",
    "FingerprintService",
    "FingerprintSettings",
    "GraphArtifactStatus",
    "GraphArtifactStore",
    "GraphBuildResult",
    "GraphEvolutionInput",
    "GraphMutationDelta",
    "GraphMutationResult",
    "GraphSnapshot",
    "GraphSnapshotRef",
    "IONameVocabulary",
    "ImprovementSuggestion",
    "MetricResult",
    "MetricStatus",
    "OrchestrationConfig",
    "OrchestrationPlan",
    "OrchestrationProgress",
    "OrchestrationService",
    "ObservationReceipt",
    "ParameterSpec",
    "PrepareArtifactHook",
    "PortMapping",
    "QualityConfidence",
    "QualityResult",
    "ScanDiagnostic",
    "ScanResult",
    "SemanticProfile",
    "SkillFolderScanner",
    "SkillGraphUpdater",
    "SkillManifestParser",
    "SourceSnapshot",
    "SuggestionPriority",
    "SymphonyLLM",
    "SymphonyGraphEngine",
    "SymphonyRuntime",
    "TaskEvidence",
    "TaskOutcome",
    "TaskOutcomeLabel",
    "TraceEvidence",
    "agent",
    "discovery",
    "normalize_name_key",
    "retrieval",
    "shared",
]


def __getattr__(name: str) -> ModuleType:
    if name in _LAZY_MODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
