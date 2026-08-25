"""Public contracts for Symphony graph observations and merged snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from openjiuwen.symphony.models._base import NonEmptyString, SymphonyModel

GRAPH_EVOLUTION_INPUT_SCHEMA = "symphony.graph_evolution_input.v1"


class EvidenceStrength(str, Enum):
    """How strongly an upstream component has verified an outcome."""

    STRONG = "strong"
    WEAK = "weak"
    NONE = "none"


class TaskOutcomeLabel(str, Enum):
    """Canonical task outcomes produced by an evolution rail or evaluator."""

    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class FailureDomain(str, Enum):
    """Failure domains used to decide whether graph weights may be updated."""

    ORCHESTRATION = "orchestration"
    SKILL_INPUT = "skill_input"
    SKILL_LOGIC = "skill_logic"
    PERMISSION = "permission"
    NETWORK = "network"
    EXTERNAL_SERVICE = "external_service"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class GraphSnapshotRef(SymphonyModel):
    """Static and optional observation revisions fixed for one task."""

    static_revision: NonEmptyString
    observation_revision: NonEmptyString
    merged_revision: NonEmptyString | None = None


class CapabilityEvidence(SymphonyModel):
    """Capability identity observed while the task was running."""

    capability_type: NonEmptyString = Field(default="skill", alias="type")
    version: str | None = None
    content_hash: NonEmptyString


class PortMapping(SymphonyModel):
    """One observed source-output to target-input mapping."""

    source_output: NonEmptyString
    target_input: NonEmptyString
    source_type: str | None = None
    target_type: str | None = None


class EvolutionGraphNode(SymphonyModel):
    """A JGF node in a planned or observed execution graph."""

    label: NonEmptyString = "skill"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvolutionEdgeMetadata(SymphonyModel):
    """Local edge outcome and trace references produced by the Rail."""

    success: bool | None = None
    failure_domain: FailureDomain | None = None
    port_mappings: tuple[PortMapping, ...] = ()
    port_mapping_hash: str | None = None
    evidence_refs: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def _validate_failure_evidence(self) -> EvolutionEdgeMetadata:
        if self.success is False and self.failure_domain is None:
            raise ValueError("failed execution edges require failure_domain")
        return self


class EvolutionGraphEdge(SymphonyModel):
    """An observed transition plus optional edge-level outcome evidence."""

    source_id: NonEmptyString = Field(alias="source")
    target_id: NonEmptyString = Field(alias="target")
    relation_type: NonEmptyString = Field(default="can_feed", alias="relation")
    metadata: EvolutionEdgeMetadata = Field(default_factory=EvolutionEdgeMetadata)


class EvolutionGraph(SymphonyModel):
    """JGF-compatible graph exchanged between Rail, evaluator, and GraphEngine."""

    id: str | None = None
    type: NonEmptyString
    directed: bool = True
    nodes: dict[NonEmptyString, EvolutionGraphNode] = Field(default_factory=dict)
    edges: tuple[EvolutionGraphEdge, ...] = ()


class TraceEvidence(SymphonyModel):
    """Trace identity and quality metadata without raw Session JSON coupling."""

    trajectory_id: NonEmptyString
    session_id: NonEmptyString
    capture_mode: NonEmptyString
    request_id: str | None = None
    truncated: bool = False
    quality_flags: tuple[NonEmptyString, ...] = ()
    span_refs: tuple[NonEmptyString, ...] = ()
    member_trajectory_refs: tuple[NonEmptyString, ...] = ()


class TaskOutcome(SymphonyModel):
    """Task-level verdict resolved outside GraphEngine."""

    label: TaskOutcomeLabel
    evidence_strength: EvidenceStrength
    failure_domain: FailureDomain | None = None
    evidence_refs: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def _validate_failure_domain(self) -> TaskOutcome:
        if self.label == TaskOutcomeLabel.VERIFIED_FAILURE and self.failure_domain is None:
            raise ValueError("verified_failure requires failure_domain")
        return self


class TaskEvidence(SymphonyModel):
    """Task-level verdict used to qualify edge observations."""

    query: str | None = None
    task_cluster_id: str | None = None
    outcome: TaskOutcome


class GraphEvolutionInput(SymphonyModel):
    """Canonical input accepted by the Symphony observation layer."""

    schema_version: Literal["symphony.graph_evolution_input.v1"] = "symphony.graph_evolution_input.v1"
    evidence_id: NonEmptyString
    graph_scope_id: NonEmptyString = "default"
    observed_at: datetime
    graph_snapshot: GraphSnapshotRef
    trace: TraceEvidence
    task: TaskEvidence
    capabilities: dict[NonEmptyString, CapabilityEvidence]
    planned_graph: EvolutionGraph | None = None
    execution_graph: EvolutionGraph

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("capabilities")
    @classmethod
    def _normalize_capability_ids(
        cls,
        value: dict[str, CapabilityEvidence],
    ) -> dict[str, CapabilityEvidence]:
        normalized = {str(capability_id).strip(): evidence for capability_id, evidence in value.items()}
        if any(not capability_id for capability_id in normalized):
            raise ValueError("capability IDs must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _validate_graph_contract(self) -> GraphEvolutionInput:
        if self.planned_graph is not None and self.planned_graph.type != "planned_graph":
            raise ValueError("planned_graph.type must be planned_graph")
        if self.execution_graph.type != "execution_graph":
            raise ValueError("execution_graph.type must be execution_graph")
        capability_ids = set(self.capabilities)
        referenced_ids = {
            endpoint for edge in self.execution_graph.edges for endpoint in (edge.source_id, edge.target_id)
        }
        missing_ids = sorted(referenced_ids - capability_ids)
        if missing_ids:
            raise ValueError(f"execution_graph references capabilities without identity evidence: {missing_ids}")
        return self


class ObservationReceipt(SymphonyModel):
    """Append receipt returned before asynchronous aggregation completes."""

    evidence_id: NonEmptyString
    graph_scope_id: NonEmptyString
    sequence: int = Field(ge=1)
    status: Literal["accepted", "audit_only", "duplicate"]
    reason: str = ""


class GraphSnapshot(SymphonyModel):
    """One immutable static-plus-observation snapshot used by a planner."""

    graph_scope_id: NonEmptyString
    static_revision: NonEmptyString
    observation_revision: NonEmptyString
    merged_revision: NonEmptyString
    high_water_sequence: int = Field(ge=0)
    overlay: dict[str, Any] = Field(default_factory=dict)
