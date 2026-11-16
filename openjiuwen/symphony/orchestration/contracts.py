"""Public result and progress contracts for Symphony orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field


class PlanningInput(BaseModel):
    """Named task data. The host resolves file references for its own workspace."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, str_strip_whitespace=True, hide_input_in_errors=True,
    )

    name: str = Field(min_length=1)
    kind: Literal["text", "file"]
    value: str = Field(min_length=1)
    description: str | None = None

    @classmethod
    def validate_many(
        cls, inputs: Sequence[PlanningInput | Mapping[str, Any]],
    ) -> tuple[PlanningInput, ...]:
        """Validate a planning input list and require unique names."""
        if not isinstance(inputs, Sequence) or isinstance(inputs, (str, bytes)):
            raise ValueError("inputs must be an array of named planning inputs")
        items = tuple(cls.model_validate(item) for item in inputs)
        if len({item.name for item in items}) != len(items):
            raise ValueError("Planning input names must be unique")
        return items


@dataclass(frozen=True)
class GraphArtifactStatus:
    """Current state of the versioned capability-graph artifact."""

    exists: bool
    fresh: bool
    version: str | None = None
    generated_at: str | None = None
    schema_version: str | None = None
    building: bool = False
    enabled: bool = True
    build_id: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    diagnostics: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class GraphBuildResult:
    """Result of publishing a capability-graph artifact."""

    version: str
    graph_path: Path
    generated_at: str
    build_id: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    diagnostics: tuple[Mapping[str, Any], ...] = ()


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
