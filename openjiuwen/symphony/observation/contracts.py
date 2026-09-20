"""Public contracts for Symphony graph observations and merged snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from unicodedata import category as unicode_category

from pydantic import ConfigDict, Field, field_validator, model_validator

from openjiuwen.symphony.models._base import NonEmptyString, SymphonyModel

GRAPH_EVOLUTION_INPUT_SCHEMA = "symphony.graph_evolution_input.v2"

_EXECUTION_NODE_METADATA_FIELDS = frozenset({"capability_type", "version", "description", "inputs", "outputs"})
_CAPABILITY_PORT_FIELDS = frozenset({"name", "type", "required", "description"})


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


class EvolutionGraphNode(SymphonyModel):
    """A JGF node in a planned or observed execution graph."""

    label: NonEmptyString = "skill"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvolutionEdgeMetadata(SymphonyModel):
    """Minimal local edge outcome produced by the Rail."""

    model_config = ConfigDict(extra="forbid")

    success: bool | None = None
    failure_domain: FailureDomain | None = None


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

    model_config = ConfigDict(extra="forbid")

    label: TaskOutcomeLabel
    evidence_strength: EvidenceStrength
    failure_domain: FailureDomain | None = None

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

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["symphony.graph_evolution_input.v2"] = "symphony.graph_evolution_input.v2"
    evidence_id: NonEmptyString
    graph_scope_id: NonEmptyString = "default"
    observed_at: datetime
    graph_snapshot: GraphSnapshotRef
    trace: TraceEvidence
    task: TaskEvidence
    planned_graph: EvolutionGraph | None = None
    execution_graph: EvolutionGraph

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_graph_contract(self) -> GraphEvolutionInput:
        if self.planned_graph is not None and self.planned_graph.type != "planned_graph":
            raise ValueError("planned_graph.type must be planned_graph")
        if self.execution_graph.type != "execution_graph":
            raise ValueError("execution_graph.type must be execution_graph")
        if any(node.label != "skill" for node in self.execution_graph.nodes.values()):
            raise ValueError("execution_graph nodes must be skills")
        for node in self.execution_graph.nodes.values():
            _validate_execution_node_metadata(node.metadata, label=node.label)
        capability_ids = set(self.execution_graph.nodes)
        referenced_ids = {
            endpoint for edge in self.execution_graph.edges for endpoint in (edge.source_id, edge.target_id)
        }
        missing_ids = sorted(referenced_ids - capability_ids)
        if missing_ids:
            raise ValueError(f"execution_graph references missing nodes: {missing_ids}")
        return self


def _validate_execution_node_metadata(value: object, *, label: str) -> None:
    """Validate the allow-listed capability snapshot attached by the Rail."""

    if not isinstance(value, Mapping):
        raise ValueError("execution_graph node metadata must be an object")
    if not set(value).issubset(_EXECUTION_NODE_METADATA_FIELDS):
        raise ValueError("execution_graph node metadata contains unsupported fields")
    if value.get("capability_type") != label or not _valid_contract_text(value.get("version")):
        raise ValueError("execution_graph node identity metadata is invalid")
    description = value.get("description", "")
    if description != "" and not _valid_contract_text(description):
        raise ValueError("execution_graph node description is invalid")
    for field_name in ("inputs", "outputs"):
        ports = value.get(field_name, [])
        if not isinstance(ports, list):
            raise ValueError("execution_graph node ports must be arrays")
        for port in ports:
            _validate_capability_port(port)


def _validate_capability_port(value: object) -> None:
    if not isinstance(value, Mapping) or not set(value).issubset(_CAPABILITY_PORT_FIELDS):
        raise ValueError("execution_graph node port is invalid")
    if not _valid_contract_text(value.get("name")) or not _valid_contract_text(value.get("type")):
        raise ValueError("execution_graph node port identity is invalid")
    if "required" in value and not isinstance(value.get("required"), bool):
        raise ValueError("execution_graph node port required must be boolean")
    description = value.get("description", "")
    if description != "" and not _valid_contract_text(description):
        raise ValueError("execution_graph node port description is invalid")


def _valid_contract_text(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(unicode_category(character) in {"Cc", "Cf"} for character in value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


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
