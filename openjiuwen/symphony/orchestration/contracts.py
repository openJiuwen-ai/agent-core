"""Public result and progress contracts for Symphony orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


@dataclass(frozen=True)
class GraphArtifactStatus:
    """Current state of the versioned capability-graph artifact."""

    exists: bool
    fresh: bool
    version: str | None = None
    generated_at: str | None = None
    schema_version: str | None = None
    building: bool = False


@dataclass(frozen=True)
class GraphBuildResult:
    """Result of publishing a capability-graph artifact."""

    version: str
    graph_path: Path
    generated_at: str


@dataclass(frozen=True)
class GraphMutationDelta:
    """Materialized graph changes produced by one atomic mutation batch."""

    added_node_count: int = 0
    updated_node_count: int = 0
    removed_node_count: int = 0
    added_edge_count: int = 0
    removed_edge_count: int = 0


@dataclass(frozen=True)
class GraphMutationResult:
    """Result of atomically applying one homogeneous Skill mutation batch."""

    request_id: str
    operation: Literal["add", "update", "delete"]
    status: Literal["published", "noop"]
    previous_version: str
    version: str
    source_snapshot_id: str
    changed_capability_ids: tuple[str, ...]
    delta: GraphMutationDelta
    diagnostics: tuple[Mapping[str, Any], ...] = ()


class CapabilityGraph(dict[str, Any]):
    """Public, mapping-compatible view of a capability graph artifact."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        super().__init__(payload)

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


class OrchestrationPlan(dict[str, Any]):
    """Public, mapping-compatible online orchestration result."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        super().__init__(payload)

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


class OrchestrationProgress(dict[str, Any]):
    """A typed progress event that remains compatible with dict callbacks."""

    def __init__(self, event: str, **details: Any) -> None:
        super().__init__(event=event, **details)

    @property
    def event(self) -> str:
        return str(self["event"])

    @property
    def details(self) -> dict[str, Any]:
        return {key: value for key, value in self.items() if key != "event"}
