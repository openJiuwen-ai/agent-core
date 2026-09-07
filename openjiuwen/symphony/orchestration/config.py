"""Runtime configuration for capability orchestration."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_FLOW_ENABLED = True
DEFAULT_FLOW_MIN_EDGE_SUPPORT = 1
DEFAULT_FLOW_MIN_EDGE_SUCCESS_RATE = 0.8
DEFAULT_FLOW_MIN_SUCCESSES_CANDIDATE = 1
DEFAULT_FLOW_MIN_SUCCESSES_VERIFIED = 1
DEFAULT_FLOW_MIN_PACK_SUCCESS_RATE_VERIFIED = 0.8
DEFAULT_FLOW_MAX_NARRATIVE_EXAMPLES = 5


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


@dataclass(frozen=True)
class SymphonyFlowConfig:
    """Symphony flow（经验沉淀与能力包）运行参数。"""

    enabled: bool = DEFAULT_FLOW_ENABLED
    min_edge_support: int = DEFAULT_FLOW_MIN_EDGE_SUPPORT
    min_edge_success_rate: float = DEFAULT_FLOW_MIN_EDGE_SUCCESS_RATE
    min_successes_candidate: int = DEFAULT_FLOW_MIN_SUCCESSES_CANDIDATE
    min_successes_verified: int = DEFAULT_FLOW_MIN_SUCCESSES_VERIFIED
    min_pack_success_rate_verified: float = DEFAULT_FLOW_MIN_PACK_SUCCESS_RATE_VERIFIED
    max_narrative_examples: int = DEFAULT_FLOW_MAX_NARRATIVE_EXAMPLES

    def __post_init__(self) -> None:
        if self.min_edge_support < 1:
            raise ValueError("min_edge_support must be positive.")
        if self.min_successes_candidate < 1 or self.min_successes_verified < 1:
            raise ValueError("min_successes_candidate and min_successes_verified must be positive.")
        if self.max_narrative_examples < 1:
            raise ValueError("max_narrative_examples must be positive.")
        for name in ("min_edge_success_rate", "min_pack_success_rate_verified"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1.")

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "min_edge_support": self.min_edge_support,
            "min_edge_success_rate": self.min_edge_success_rate,
            "min_successes_candidate": self.min_successes_candidate,
            "min_successes_verified": self.min_successes_verified,
            "min_pack_success_rate_verified": self.min_pack_success_rate_verified,
            "max_narrative_examples": self.max_narrative_examples,
        }
