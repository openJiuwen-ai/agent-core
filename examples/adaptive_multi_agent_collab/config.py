"""Validated configuration for the executable collaboration example.

Primary architecture and training defaults follow Appendices A and B.
Run-specific scientific choices remain explicitly unconfigured.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

from .infrastructure.failure_policy import FailurePolicyConfig
from .workflows.recovery import WorkflowRecoveryConfig


CapacityValue = int | tuple[int, ...]


def _positive(name: str, value: int | float, *, allow_zero: bool = False) -> None:
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier}")


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """Primary neural dimensions; Transformer depth/head count are recorded choices."""

    representation_dim: int = 128
    feedforward_dim: int = 256
    answer_byte_dim: int = 64
    transformer_layers: int = 2
    transformer_heads: int = 4
    dropout: float = 0.0
    transformer_norm_first: bool = True
    positional_encoding: str = "sinusoidal"
    initialization: str = "pytorch_default_zero_output"

    def __post_init__(self) -> None:
        for name in (
            "representation_dim",
            "feedforward_dim",
            "answer_byte_dim",
            "transformer_layers",
            "transformer_heads",
        ):
            _positive(name, getattr(self, name))
        if self.representation_dim % self.transformer_heads:
            raise ValueError("representation_dim must be divisible by transformer_heads")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        if self.positional_encoding != "sinusoidal":
            raise ValueError("only the recorded sinusoidal positional encoding is supported")
        if self.initialization != "pytorch_default_zero_output":
            raise ValueError("unsupported neural initialization")


@dataclass(frozen=True, slots=True)
class EncoderConfig:
    """Frozen MiniLM and observable-event preprocessing, including fixture identity."""

    encoder_type: str = "minilm"
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    model_revision: str = "unconfigured"
    cache_directory: str | None = None
    max_tokens: int = 256
    chunking_rule: str = "contiguous_token_ids_no_overlap"
    serialization_version: str = "observable_query_event_fields_v1"
    structural_feature_version: str = "one_hot_log1p_observable_events_v1"
    answer_view_version: str = "canonical_answer_with_option_text_v1"
    normalization: str = "chunk_l2_mean_l2"
    numerical_precision: str = "float32"
    character_encoding: str = "utf-8"
    empty_history_representation: str = "empty_event_sequence"
    supervision_filter_version: str = "explicit_observable_fields_v1"

    def __post_init__(self) -> None:
        if self.encoder_type not in {"minilm", "offline_fixture"}:
            raise ValueError("encoder_type must be minilm or offline_fixture")
        if self.model_name != "sentence-transformers/all-MiniLM-L6-v2":
            raise ValueError("the primary frozen text model is all-MiniLM-L6-v2")
        if not self.model_revision.strip():
            raise ValueError("model_revision must not be empty")
        _positive("max_tokens", self.max_tokens)
        if self.max_tokens < 3 or self.max_tokens > 256:
            raise ValueError("max_tokens must be in [3, 256]")
        if self.chunking_rule != "contiguous_token_ids_no_overlap":
            raise ValueError("unsupported tokenizer-aligned chunking rule")
        if self.normalization != "chunk_l2_mean_l2":
            raise ValueError("unsupported text normalization")
        if self.numerical_precision not in {"float32", "float64"}:
            raise ValueError("numerical_precision must be float32 or float64")
        if self.character_encoding != "utf-8":
            raise ValueError("the fixed encoder character encoding must be UTF-8")
        if self.empty_history_representation != "empty_event_sequence":
            raise ValueError("empty history must be an empty event sequence")
        for name in (
            "serialization_version",
            "structural_feature_version",
            "answer_view_version",
            "supervision_filter_version",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class LogicalAgentConfig:
    """Frozen logical-agent/backend assignment used by one experiment."""

    agent_id: int
    backend_key: str
    model_name: str = "unspecified"
    model_revision: str = "unspecified"
    system_configuration: str = ""
    role_configuration: str = ""
    decoding_configuration: tuple[tuple[str, str], ...] = ()
    prompt_collection_version: str = "seed_prompt_collection_v4"

    def __post_init__(self) -> None:
        if self.agent_id < 0:
            raise ValueError("logical agent_id must be non-negative")
        for name in (
            "backend_key",
            "model_name",
            "model_revision",
            "prompt_collection_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        normalized = tuple((str(key), str(value)) for key, value in self.decoding_configuration)
        if len({key for key, _ in normalized}) != len(normalized):
            raise ValueError("decoding_configuration keys must be unique")
        object.__setattr__(self, "decoding_configuration", normalized)


@dataclass(frozen=True, slots=True)
class CapacityConfig:
    """Per-session outgoing and incoming conversation capacities (Step 4)."""

    outgoing: CapacityValue = 2
    incoming: CapacityValue = 2

    @staticmethod
    def _resolve_one(value: CapacityValue, n_agents: int, name: str) -> tuple[int, ...]:
        resolved = (value,) * n_agents if isinstance(value, int) else tuple(value)
        if len(resolved) != n_agents:
            raise ValueError(f"{name} must contain exactly n_agents={n_agents} entries")
        if any(not isinstance(item, int) or not 0 <= item <= n_agents for item in resolved):
            raise ValueError(f"every {name} capacity must be an integer in [0, n_agents]")
        return resolved

    def resolve(self, n_agents: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Return explicit ``(K_out, K_in)`` tuples for ``n_agents``."""

        _positive("n_agents", n_agents)
        return (
            self._resolve_one(self.outgoing, n_agents, "outgoing"),
            self._resolve_one(self.incoming, n_agents, "incoming"),
        )


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    """Engineering-only guards, separate from Step 33 termination.

    ``max_internal_rounds`` is ``R_conv^max``: the largest permitted
    internal-round index of one non-judge conversation.
    """

    max_internal_rounds: int = 30
    call_timeout_seconds: float = 120.0
    session_timeout_seconds: float = 600.0
    parse_retries: int = 1
    answer_stability_patience: int = 3
    max_concurrent_model_calls: int = 3

    def __post_init__(self) -> None:
        _positive("answer_stability_patience", self.answer_stability_patience)
        _positive("max_internal_rounds", self.max_internal_rounds)
        _positive("call_timeout_seconds", self.call_timeout_seconds)
        _positive("session_timeout_seconds", self.session_timeout_seconds)
        _positive("max_concurrent_model_calls", self.max_concurrent_model_calls)
        _positive("parse_retries", self.parse_retries, allow_zero=True)


@dataclass(frozen=True, slots=True)
class CostConfig:
    """Configurable normalization and Step 11/12 cost penalties."""

    normalization: float = 1_000.0
    input_token_weight: float = 1.0
    output_token_weight: float = 1.0
    model_call_weight: float = 0.0
    monetary_weight: float = 0.0
    lambda_session: float = 0.01
    lambda_conversation: float = 0.01

    def __post_init__(self) -> None:
        _positive("normalization", self.normalization)
        for name in (
            "input_token_weight",
            "output_token_weight",
            "model_call_weight",
            "monetary_weight",
            "lambda_session",
            "lambda_conversation",
        ):
            _positive(name, getattr(self, name), allow_zero=True)


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    """One prescribed trade-off for all end-to-end model selection."""

    lambda_select: float | None = None

    def __post_init__(self) -> None:
        if self.lambda_select is not None:
            _positive("lambda_select", self.lambda_select, allow_zero=True)


@dataclass(frozen=True, slots=True)
class ResourceConfig:
    """Declared request ceilings; monetary choices require explicit registration."""

    initial_input: int = 1024
    role_input: int = 2560
    revision_input: int = 2048
    judge_input: int = 4096
    tie_input: int = 8192
    textgrad_input: int = 8192
    generation_output: int = 256
    textgrad_output: int = 1024
    context_reserve: int = 64
    transport_retries: int = 1
    compatibility_budget: float | None = None
    compatibility_max_attempts: int = 24
    pilot_budget: float | None = None
    development_budget: float | None = None
    test_budget: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "initial_input",
            "role_input",
            "revision_input",
            "judge_input",
            "tie_input",
            "textgrad_input",
            "generation_output",
            "textgrad_output",
            "context_reserve",
        ):
            _positive(name, getattr(self, name))
        if self.transport_retries not in {0, 1}:
            raise ValueError("at most one transport retry is permitted")
        if not 1 <= self.compatibility_max_attempts <= 48:
            raise ValueError("compatibility permits at most 48 explicitly authorized physical generation attempts")
        for name in ("compatibility_budget", "pilot_budget", "development_budget", "test_budget"):
            if getattr(self, name) is not None:
                _positive(name, getattr(self, name), allow_zero=True)
        if self.compatibility_budget is not None and self.compatibility_budget > 3.0:
            raise ValueError("compatibility monetary ceiling cannot exceed USD 3")

    def caps(self, kind: str) -> tuple[int, int]:
        if kind.startswith("textgrad"):
            return self.textgrad_input, self.textgrad_output
        inputs = {
            "initial": self.initial_input,
            "role": self.role_input,
            "revision": self.revision_input,
            "judge": self.judge_input,
            "tie": self.tie_input,
        }
        return inputs[kind], self.generation_output


@dataclass(frozen=True, slots=True)
class TerminationConfig:
    """Adaptive completed-session termination hyperparameters (Step 33)."""

    min_sessions: int = 1
    max_sessions: int = 3
    persistence_length: int = 2
    winner_support_threshold: float = 0.80
    winner_margin_threshold: float = 0.40
    distribution_change_threshold: float = 0.05
    routing_opportunity_threshold: float = 0.05

    def __post_init__(self) -> None:
        _positive("min_sessions", self.min_sessions)
        _positive("max_sessions", self.max_sessions)
        _positive("persistence_length", self.persistence_length)
        if self.min_sessions > self.max_sessions:
            raise ValueError("min_sessions must not exceed max_sessions")
        for name in (
            "winner_support_threshold",
            "winner_margin_threshold",
            "distribution_change_threshold",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        _positive("routing_opportunity_threshold", self.routing_opportunity_threshold, allow_zero=True)


@dataclass(frozen=True, slots=True)
class PhaseConfig:
    """One phase's update bound and validation-plateau rule (Step 32)."""

    max_updates: int = 20
    max_attempts: int = 30
    validation_interval: int = 10
    min_improvement: float = 1e-4
    patience: int = 2

    def __post_init__(self) -> None:
        _positive("max_updates", self.max_updates, allow_zero=True)
        _positive("max_attempts", self.max_attempts)
        _positive("validation_interval", self.validation_interval)
        _positive("min_improvement", self.min_improvement, allow_zero=True)
        _positive("patience", self.patience)
        if self.max_attempts < self.max_updates:
            raise ValueError("max_attempts must be greater than or equal to max_updates")


@dataclass(frozen=True, slots=True)
class PromptPhaseConfig:
    """Single-proposal iterations; acceptance requires improvement strictly above zero."""

    max_iterations: int = 5
    patience: int = 3

    def __post_init__(self) -> None:
        _positive("max_iterations", self.max_iterations, allow_zero=True)
        _positive("patience", self.patience)


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    """Interleaved W -> alpha -> gamma schedule and optimizer settings."""

    weighting_learning_rate: float = 1e-3
    routing_learning_rate: float = 1e-3
    weight_decay: float = 0.0
    gradient_clip_norm: float | None = 1.0
    adam_beta1: float = 0.9
    adam_beta2: float = 0.999
    adam_epsilon: float = 1e-8
    adam_amsgrad: bool = False
    adam_maximize: bool = False
    gradient_norm_type: float = 2.0
    gradient_error_if_nonfinite: bool = True
    bootstrap_weighting_updates: int = 10
    bootstrap_weighting_attempts: int = 50
    bootstrap_routing_updates: int = 10
    max_outer_cycles: int = 5
    weighting_phase: PhaseConfig = field(default_factory=PhaseConfig)
    routing_phase: PhaseConfig = field(default_factory=lambda: PhaseConfig(max_attempts=20))
    prompt_phase: PromptPhaseConfig = field(default_factory=PromptPhaseConfig)
    prompt_train_batch_size: int = 2
    weighting_validation_batch_size: int = 20
    routing_validation_batch_size: int = 20
    prompt_validation_batch_size: int = 20
    system_validation_batch_size: int = 20
    outer_min_improvement: float = 1e-4
    outer_patience: int = 2
    routing_objective: str = "mixed_q"
    p_mix: float = 0.75

    def __post_init__(self) -> None:
        for name in ("weighting_learning_rate", "routing_learning_rate"):
            _positive(name, getattr(self, name))
        for name in ("weight_decay", "outer_min_improvement"):
            _positive(name, getattr(self, name), allow_zero=True)
        for name in ("adam_beta1", "adam_beta2"):
            if not 0 <= getattr(self, name) < 1:
                raise ValueError(f"{name} must be in [0, 1)")
        _positive("adam_epsilon", self.adam_epsilon)
        if self.gradient_norm_type != 2.0 or not self.gradient_error_if_nonfinite:
            raise ValueError("global clipping requires L2 norm and nonfinite-gradient rejection")
        if self.gradient_clip_norm is not None:
            _positive("gradient_clip_norm", self.gradient_clip_norm)
        for name in ("bootstrap_weighting_updates", "bootstrap_routing_updates", "max_outer_cycles"):
            _positive(name, getattr(self, name), allow_zero=True)
        _positive("bootstrap_weighting_attempts", self.bootstrap_weighting_attempts)
        if self.bootstrap_weighting_attempts < self.bootstrap_weighting_updates:
            raise ValueError(
                "bootstrap_weighting_attempts must be greater than or equal to " "bootstrap_weighting_updates"
            )
        for name in (
            "prompt_train_batch_size",
            "weighting_validation_batch_size",
            "routing_validation_batch_size",
            "prompt_validation_batch_size",
            "system_validation_batch_size",
            "outer_patience",
        ):
            _positive(name, getattr(self, name))
        if self.routing_objective != "mixed_q":
            raise ValueError("routing_objective must be mixed_q; older objectives require a new condition")
        if not math.isfinite(self.p_mix) or not 0 <= self.p_mix <= 1:
            raise ValueError("p_mix must be in [0, 1]")

    @property
    def weighting_bootstrap(self) -> PhaseConfig:
        return PhaseConfig(
            max_updates=self.bootstrap_weighting_updates,
            max_attempts=self.bootstrap_weighting_attempts,
            validation_interval=self.weighting_phase.validation_interval,
            min_improvement=self.weighting_phase.min_improvement,
            patience=self.weighting_phase.patience,
        )

    @property
    def routing_bootstrap(self) -> PhaseConfig:
        return PhaseConfig(
            max_updates=self.bootstrap_routing_updates,
            max_attempts=max(1, self.bootstrap_routing_updates),
            validation_interval=self.routing_phase.validation_interval,
            min_improvement=self.routing_phase.min_improvement,
            patience=self.routing_phase.patience,
        )


@dataclass(frozen=True, slots=True)
class ReproducibilityConfig:
    """Values serialized with checkpoints and restored on resume."""

    seed: int = 42
    deterministic: bool = True
    deterministic_algorithms: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class SchemeRegistration:
    """Append-only extension contract for the supported role/revision interface."""

    label: str
    legal_edges: tuple[tuple[int, int], ...]
    init_prompt: str
    role_prompt: str
    revision_prompt: str
    purpose: str
    execution: str = "feedback_revision"
    fallback: str = "latest_valid"
    cost_attribution: str = "conversation"

    def __post_init__(self) -> None:
        from .schemas import SCHEME_ORDER, Scheme
        label = Scheme(self.label)
        if label in SCHEME_ORDER:
            raise ValueError("extension labels cannot redefine an inherited scheme")
        if (self.execution, self.fallback, self.cost_attribution) != (
            "feedback_revision", "latest_valid", "conversation"
        ):
            raise ValueError("unsupported extension execution/fallback/cost contract")
        edges = tuple(tuple(edge) for edge in self.legal_edges)
        if not edges or len(set(edges)) != len(edges) or any(len(edge) != 2 or min(edge) < 0 for edge in edges):
            raise ValueError("new scheme requires explicit unique legal directed edges")
        object.__setattr__(self, "legal_edges", edges)
        for name in ("init_prompt", "role_prompt", "revision_prompt", "purpose"):
            if not getattr(self, name).strip():
                raise ValueError(f"scheme contract requires {name}")


@dataclass(frozen=True, slots=True)
class Config:
    """Complete validated configuration shared by training and inference."""

    n_agents: int = 3
    scheme_registry: tuple[SchemeRegistration, ...] = ()
    history_categories: tuple[tuple[str, tuple[str, ...]], ...] = ()
    network: NetworkConfig = field(default_factory=NetworkConfig)
    encoders: EncoderConfig = field(default_factory=EncoderConfig)
    agent_pool: tuple[LogicalAgentConfig, ...] = ()
    capacity: CapacityConfig = field(default_factory=CapacityConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    failure_policy: FailurePolicyConfig = field(default_factory=FailurePolicyConfig)
    workflow_recovery: WorkflowRecoveryConfig = field(default_factory=WorkflowRecoveryConfig)
    termination: TerminationConfig = field(default_factory=TerminationConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    reproducibility: ReproducibilityConfig = field(default_factory=ReproducibilityConfig)

    def __post_init__(self) -> None:
        _positive("n_agents", self.n_agents)
        self.capacity.resolve(self.n_agents)
        labels = [item.label for item in self.scheme_registry]
        if len(set(labels)) != len(labels) or any(max(edge) >= self.n_agents for item in self.scheme_registry for edge in item.legal_edges):
            raise ValueError("extension scheme labels/edges are invalid")
        pool = self.agent_pool or tuple(
            LogicalAgentConfig(agent_id=index, backend_key=f"agent-{index}") for index in range(self.n_agents)
        )
        if tuple(agent.agent_id for agent in pool) != tuple(range(self.n_agents)):
            raise ValueError("agent_pool must contain exactly one entry per logical agent in index order")
        object.__setattr__(self, "agent_pool", tuple(pool))

    @property
    def scheme_order(self):
        from .schemas import SCHEME_ORDER, Scheme
        return (*SCHEME_ORDER, *(Scheme(item.label) for item in self.scheme_registry))

    @property
    def scheme_legality(self):
        return {item.label: item.legal_edges for item in self.scheme_registry}

    @property
    def agent_ids(self) -> tuple[int, ...]:
        return tuple(range(self.n_agents))

    @property
    def seed(self) -> int:
        return self.reproducibility.seed

    @property
    def deterministic(self) -> bool:
        return self.reproducibility.deterministic

    def to_dict(self) -> dict[str, Any]:
        """Return the exact reproducibility/checkpoint payload."""

        payload = asdict(self)
        # Existing disabled conditions retain their exact checkpoint identity.
        # Nondefault candidates and enabled policies have distinct fingerprints.
        if self.failure_policy == FailurePolicyConfig():
            payload.pop("failure_policy")
        if self.workflow_recovery == WorkflowRecoveryConfig():
            payload.pop("workflow_recovery")
        groups: dict[str, list[int]] = {}
        for agent in self.agent_pool:
            groups.setdefault(agent.backend_key, []).append(agent.agent_id)
        payload["backend_sharing_relation"] = [
            {"backend_key": key, "logical_agent_ids": ids} for key, ids in sorted(groups.items())
        ]
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Config:
        """Read the same explicit configuration that checkpoints and local runs record."""

        data = dict(value)
        sharing = data.pop("backend_sharing_relation", None)
        constructors = {
            "network": NetworkConfig,
            "encoders": EncoderConfig,
            "capacity": CapacityConfig,
            "safety": SafetyConfig,
            "costs": CostConfig,
            "selection": SelectionConfig,
            "resources": ResourceConfig,
            "failure_policy": FailurePolicyConfig,
            "workflow_recovery": WorkflowRecoveryConfig,
            "termination": TerminationConfig,
            "reproducibility": ReproducibilityConfig,
        }
        for name, constructor in constructors.items():
            if name in data:
                nested = dict(data[name])
                if name == "capacity":
                    nested = {key: tuple(item) if isinstance(item, list) else item for key, item in nested.items()}
                data[name] = constructor(**nested)
        if "training" in data:
            training = dict(data["training"])
            for name in ("weighting_phase", "routing_phase"):
                if name in training:
                    training[name] = PhaseConfig(**training[name])
            if "prompt_phase" in training:
                training["prompt_phase"] = PromptPhaseConfig(**training["prompt_phase"])
            data["training"] = TrainingConfig(**training)
        if "history_categories" in data:
            data["history_categories"] = tuple((key, tuple(labels)) for key, labels in data["history_categories"])
        if "scheme_registry" in data:
            data["scheme_registry"] = tuple(SchemeRegistration(**item) for item in data["scheme_registry"])
        if "agent_pool" in data:
            data["agent_pool"] = tuple(LogicalAgentConfig(**item) for item in data["agent_pool"])
        result = cls(**data)
        if sharing is not None and sharing != result.to_dict()["backend_sharing_relation"]:
            raise ValueError("backend sharing metadata disagrees with agent_pool")
        return result
