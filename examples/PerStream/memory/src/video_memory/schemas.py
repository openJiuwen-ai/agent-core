from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

NodeType = Literal["detail", "summary", "preference"]


@dataclass(frozen=True)
class FrameRecord:
    frame_id: str
    frame_key: str
    global_frame_id: int
    event_id: str
    local_frame_id: int
    time_id: int
    modality: Literal["png", "txt"]
    path: Path

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass(frozen=True)
class QAItem:
    qa_id: str
    question: str
    answer: str
    qa_time_key: str | None
    qa_time_id: int | None
    reference_sets: list[list[str]]
    raw_type: list[str] = field(default_factory=list)
    reasoning: str | None = None
    video_id: str | None = None
    required_facts: list[dict[str, Any]] = field(default_factory=list)
    background_frame_keys: list[str] = field(default_factory=list)
    minimum_evidence_size: int | None = None
    evidence_aggregation: dict[str, Any] | None = None
    evidence_scope: dict[str, Any] | None = None
    evidence_schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrameWindow:
    window_id: str
    frame_keys: list[str]
    start_time_id: int
    end_time_id: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryNode:
    node_id: str
    node_type: NodeType
    description_text: str
    time_ids: list[int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RejectedNode:
    """A generated node dropped because none of its cited frames exist.

    The generator prompt requires related_frame_ids to name frames from the
    current window. When a model cites something else — a global frame id, a
    bare index, a frame from an adjacent window, or an outright hallucination —
    the node cannot be bound to real evidence and is discarded rather than
    attached to every frame in the window.
    """

    window_id: str
    node_index: int
    node_type: str
    description_text: str
    cited_frame_keys: list[str]
    known_frame_keys: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Entity:
    entity_id: str
    canonical_name: str
    entity_type: str | None = None
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NodeFrameEdge:
    node_id: str
    frame_key: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NodeEntityEdge:
    node_id: str
    entity_id: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntityMention:
    text: str
    label: str | None = None
    start_char: int | None = None
    end_char: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QAParseResult:
    qa_types: list[NodeType]
    entities: list[str]
    time_range: tuple[int, int] | None
    temporal_hint: str = "none"
    time_order: Literal["none", "recent", "earliest", "latest"] = "none"
    intent: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["time_range"] = list(self.time_range) if self.time_range else None
        return data


@dataclass(frozen=True)
class RankedNode:
    node_id: str
    score: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PropagationStep:
    hop: int
    source_node_id: str
    entity_id: str
    target_node_id: str
    source_score: float
    entity_score: float
    propagated_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PropagationResult:
    node_scores: dict[str, float]
    steps: list[PropagationStep]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_scores": self.node_scores,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class RetrievedContext:
    selected_node_ids: list[str]
    retrieved_frame_keys: list[str]
    node_scores: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnswerResult:
    qa_id: str
    answer: str
    selected_node_ids: list[str]
    retrieved_frame_keys: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationResult:
    qa_id: str
    qa_accuracy: float
    reference_recall: float
    redundant_ratio: float
    evidence_precision: float
    evidence_f1: float
    retrieved_frame_count: int
    reference_frame_count: int
    extra_frame_count: int
    best_reference_set: list[str]
    evidence_unit_coverage: float
    fact_completeness: float
    evidence_sufficiency: float
    valid_evidence_precision: float
    background_ratio: float
    off_target_ratio: float
    conditional_redundant_ratio: float | None
    evidence_unit_count: int
    covered_evidence_unit_count: int
    required_fact_count: int
    complete_fact_count: int
    valid_evidence_frame_count: int
    background_frame_count: int
    matched_sufficient_frames: list[str]
    metric_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
