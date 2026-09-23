"""Runtime configuration for capability orchestration."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_FLOW_ENABLED = True
DEFAULT_FLOW_MIN_SUCCESSES = 1
DEFAULT_FLOW_MIN_PACK_SUCCESS_RATE = 0.8
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
    """Symphony flow（经验沉淀与能力包）运行参数。

    分组与判级只看 pack 级统计（min_successes / min_pack_success_rate）：
    分组签名 = 轨迹自身的全部成功边，确定性且不随全局统计漂移，不再需要
    边级资格阈值（min_edge_support / min_edge_success_rate）与 candidate
    中间态（candidate 判级没有独立消费方）。
    """

    enabled: bool = DEFAULT_FLOW_ENABLED
    min_successes: int = DEFAULT_FLOW_MIN_SUCCESSES
    min_pack_success_rate: float = DEFAULT_FLOW_MIN_PACK_SUCCESS_RATE
    max_narrative_examples: int = DEFAULT_FLOW_MAX_NARRATIVE_EXAMPLES

    def __post_init__(self) -> None:
        if self.min_successes < 1:
            raise ValueError("min_successes must be positive.")
        if self.max_narrative_examples < 1:
            raise ValueError("max_narrative_examples must be positive.")
        if not 0 <= self.min_pack_success_rate <= 1:
            raise ValueError("min_pack_success_rate must be between 0 and 1.")

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "min_successes": self.min_successes,
            "min_pack_success_rate": self.min_pack_success_rate,
            "max_narrative_examples": self.max_narrative_examples,
        }
