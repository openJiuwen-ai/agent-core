"""Runtime configuration for capability orchestration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrchestrationConfig:
    mode: str = "fast"
    top_k: int = 3
    max_depth: int = 4
    min_edge_confidence: float = 0.7
    dynamic_graph_enabled: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"fast", "beam"}:
            raise ValueError(f"Unsupported orchestration mode: {self.mode}")
        if self.top_k < 1 or self.max_depth < 1:
            raise ValueError("top_k and max_depth must be positive.")
        if not 0 <= self.min_edge_confidence <= 1:
            raise ValueError("min_edge_confidence must be between 0 and 1.")

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "top_k": self.top_k,
            "max_depth": self.max_depth,
            "min_edge_confidence": self.min_edge_confidence,
            "dynamic_graph_enabled": self.dynamic_graph_enabled,
        }
