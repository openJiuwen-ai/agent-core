"""Public result and progress contracts for Symphony orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


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
