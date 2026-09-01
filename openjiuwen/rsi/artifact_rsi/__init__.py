"""Public contracts for program and paper artifact optimization providers."""

from openjiuwen.rsi.artifact_rsi.events import (
    EngineEvent,
    EventNode,
    EventProgress,
    EventStatus,
    OnEvent,
    emit,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.provider import PaperArtifactProvider
from openjiuwen.rsi.artifact_rsi.program_opt.provider import ProgramArtifactProvider
from openjiuwen.rsi.artifact_rsi.provider import ArtifactProvider
from openjiuwen.rsi.artifact_rsi.request import (
    build_request,
    validate_artifact_task_request,
)
from openjiuwen.rsi.artifact_rsi.schema import (
    ArtifactEngineRequest,
    ArtifactRef,
    ArtifactType,
    ArtifactValidationResult,
    EngineReport,
    EngineResult,
    EngineState,
    RsiChange,
    RsiScenario,
    RsiStatus,
    RsiTaskCreateRequest,
    RsiTaskEnvelope,
    RsiTreeNode,
    RsiUsage,
    RsiUsageTokens,
    TreeResponse,
)

__all__ = [
    "ArtifactEngineRequest",
    "ArtifactProvider",
    "ArtifactRef",
    "ArtifactType",
    "ArtifactValidationResult",
    "EngineEvent",
    "EngineReport",
    "EngineResult",
    "EngineState",
    "EventNode",
    "EventProgress",
    "EventStatus",
    "OnEvent",
    "PaperArtifactProvider",
    "ProgramArtifactProvider",
    "RsiChange",
    "RsiScenario",
    "RsiStatus",
    "RsiTaskCreateRequest",
    "RsiTaskEnvelope",
    "RsiTreeNode",
    "RsiUsage",
    "RsiUsageTokens",
    "TreeResponse",
    "build_request",
    "emit",
    "validate_artifact_task_request",
]
