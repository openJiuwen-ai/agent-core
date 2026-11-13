"""Trainable routing (alpha) and reliability-weighting (W) models.

The models share only fixed query/history encoder outputs. Alpha separately
owns the trainable session-start answer-state encoder. All ``torch`` parameters
belong to disjoint module instances, making the alpha/W gradient boundary
explicit rather than enforcing it by naming convention.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from ..config import NetworkConfig
from .encoders import FixedBaseEncoders, HistoryEncoding, TEXT_DIMENSION
from ..infrastructure.randomness import SEED_DERIVATION_ID, derive_seed
from ..schemas import RoutingAction, RoutingMode, SampledDecision, Scheme

Edge = tuple[int, int]
Capacity = int | Sequence[int]
ANSWER_STATE_ARCHITECTURE_ID = "canonical_byte_persistent_answer_blocks_v2"
ROUTING_ARCHITECTURE_ID = "raw_mixed_q_semantic_blocks_v3"
WEIGHTING_ARCHITECTURE_ID = "weighting_semantic_projection_blocks_v3"
ROUTING_SEED_DERIVATION_ID = SEED_DERIVATION_ID


def derive_routing_seed(run_seed: int, trajectory_id: int | str, update_id: int, session_id: int) -> int:
    """One route substream for a trajectory/update/session, with no rollout axis."""

    return derive_seed(run_seed, "route", trajectory_id, update_id, session_id)


@dataclass(frozen=True)
class RoutingRolloutIdentity:
    run_seed: int
    trajectory_id: int | str
    update_id: int

    def for_session(self, session_id: int) -> RoutingSubstream:
        return RoutingSubstream(self.run_seed, self.trajectory_id, self.update_id, session_id)


@dataclass(frozen=True)
class RoutingSubstream:
    run_seed: int
    trajectory_id: int | str
    update_id: int
    session_id: int
    derivation_id: str = ROUTING_SEED_DERIVATION_ID

    def __post_init__(self) -> None:
        if self.derivation_id != ROUTING_SEED_DERIVATION_ID:
            raise ValueError("unsupported routing seed derivation")
        derive_routing_seed(self.run_seed, self.trajectory_id, self.update_id, self.session_id)

    @property
    def seed(self) -> int:
        return derive_routing_seed(self.run_seed, self.trajectory_id, self.update_id, self.session_id)

    def generator(self, *, device: str = "cpu") -> torch.Generator:
        return torch.Generator(device=device).manual_seed(self.seed)

    @property
    def rollout_identity(self) -> RoutingRolloutIdentity:
        return RoutingRolloutIdentity(self.run_seed, self.trajectory_id, self.update_id)

    def to_dict(self) -> dict[str, int | str]:
        return {
            "derivation_id": self.derivation_id,
            "run_seed": self.run_seed,
            "trajectory_id": self.trajectory_id,
            "update_id": self.update_id,
            "session_id": self.session_id,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RoutingSubstream:
        descriptor = cls(
            **{key: value[key] for key in ("run_seed", "trajectory_id", "update_id", "session_id", "derivation_id")}
        )
        if value.get("seed") != descriptor.seed:
            raise ValueError("routing substream seed does not match its identifiers")
        return descriptor


def routing_generator(
    identity: RoutingRolloutIdentity, session_id: int, *, device: str = "cpu"
) -> tuple[int, torch.Generator]:
    substream = identity.for_session(session_id)
    return substream.seed, substream.generator(device=device)


def _default_scheme_order() -> tuple[Scheme, ...]:
    return (
        Scheme.ZERO,
        Scheme.SELF,
        Scheme.DEBATE,
        Scheme.JUDGE,
        Scheme.TEACHER,
        Scheme.REVIEWER,
    )


def legal_schemes(
    initiator_id: int, receiver_id: int, scheme_order: Sequence[Scheme] | None = None,
    legality: Mapping[str, Sequence[Edge]] | None = None,
) -> tuple[Scheme, ...]:
    """Built-in structural sets plus explicitly registered non-judge edges."""
    base = (Scheme.ZERO, Scheme.SELF) if initiator_id == receiver_id else (
        Scheme.ZERO, Scheme.DEBATE, Scheme.JUDGE, Scheme.TEACHER, Scheme.REVIEWER
    )
    order = tuple(scheme_order or _default_scheme_order())
    extra = set(order) - set(_default_scheme_order())
    if any(label.value not in (legality or {}) for label in extra):
        raise ValueError("each added routing scheme needs an explicit legal-edge registry")
    return tuple(label for label in order if label in base or (
        label in extra and (initiator_id, receiver_id) in (legality or {})[label.value]
    ))


def legal_label_mask(
    n_agents: int,
    scheme_order: Sequence[Scheme] | None = None,
    *,
    device: torch.device | str | None = None,
    legality: Mapping[str, Sequence[Edge]] | None = None,
) -> torch.Tensor:
    """Build the ``[N,N,|Z_route|]`` structural legality mask."""

    if n_agents <= 0:
        raise ValueError("n_agents must be positive")
    order = tuple(scheme_order or _default_scheme_order())
    if len(set(order)) != len(order) or Scheme.ZERO not in order:
        raise ValueError("scheme_order must contain unique labels including zero")
    mask = torch.zeros((n_agents, n_agents, len(order)), dtype=torch.bool, device=device)
    for initiator in range(n_agents):
        for receiver in range(n_agents):
            legal = set(legal_schemes(initiator, receiver, order, legality))
            mask[initiator, receiver] = torch.tensor(
                [scheme in legal for scheme in order], dtype=torch.bool, device=device
            )
    return mask


def masked_softmax(logits: torch.Tensor, mask: torch.Tensor, *, dim: int = -1) -> torch.Tensor:
    """Softmax over legal labels, assigning exactly zero to illegal labels."""

    if logits.shape != mask.shape:
        raise ValueError(f"logits/mask shape mismatch: {tuple(logits.shape)} != {tuple(mask.shape)}")
    if not bool(mask.any(dim=dim).all()):
        raise ValueError("every categorical distribution needs at least one legal label")
    probabilities = torch.softmax(logits.masked_fill(~mask, -torch.inf), dim=dim)
    return probabilities.masked_fill(~mask, 0.0)


@dataclass(frozen=True)
class RoutingPolicyOutput:
    values: torch.Tensor
    legal_mask: torch.Tensor
    scheme_order: tuple[Scheme, ...]

    @property
    def probabilities(self) -> torch.Tensor:
        """Unit-temperature collection distribution, never the predicted value."""
        return masked_softmax(self.values.detach(), self.legal_mask)

    def probability(self, edge: Edge, scheme: Scheme) -> torch.Tensor:
        return self.probabilities[edge[0], edge[1], self.scheme_order.index(scheme)]

    def value(self, edge: Edge, scheme: Scheme) -> torch.Tensor:
        return self.values[edge[0], edge[1], self.scheme_order.index(scheme)]


def _capacity_vector(value: Capacity, n_agents: int, name: str) -> tuple[int, ...]:
    if isinstance(value, int):
        result = (value,) * n_agents
    else:
        result = tuple(int(item) for item in value)
    if len(result) != n_agents:
        raise ValueError(f"{name} must contain one value per agent")
    if any(item < 0 or item > n_agents for item in result):
        raise ValueError(f"{name} values must be in [0, n_agents]")
    return result


class ProjectedResidual(nn.Module):
    """Projected skip plus GELU branch, with affine output normalization."""

    def __init__(
        self, input_dimension: int, hidden_dimension: int, output_dimension: int, *, normalize_input: bool
    ) -> None:
        super().__init__()
        self.input_norm = nn.LayerNorm(input_dimension, elementwise_affine=False) if normalize_input else nn.Identity()
        self.first = nn.Linear(input_dimension, hidden_dimension)
        self.second = nn.Linear(hidden_dimension, output_dimension)
        self.skip = nn.Linear(input_dimension, output_dimension, bias=False)
        self.output_norm = nn.LayerNorm(output_dimension)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        branch = self.second(torch.nn.functional.gelu(self.first(self.input_norm(value))))
        return self.output_norm(self.skip(value) + branch)


def _query_mapping(configuration: NetworkConfig) -> nn.Sequential:
    return nn.Sequential(
        nn.LayerNorm(TEXT_DIMENSION, elementwise_affine=False),
        nn.Linear(TEXT_DIMENSION, configuration.feedforward_dim),
        nn.GELU(),
        nn.Linear(configuration.feedforward_dim, configuration.representation_dim),
        nn.LayerNorm(configuration.representation_dim),
    )


class AnswerStateEncoder(nn.Module):
    """One trainable canonical-byte encoder with exact ordered agreement features."""

    def __init__(self, n_agents: int, *, configuration: NetworkConfig | None = None) -> None:
        super().__init__()
        self.configuration = configuration or NetworkConfig()
        self.n_agents = n_agents
        self.agent_ids = tuple(range(n_agents))
        self.initial_agent_ids = self.agent_ids
        self.added_agent_ids: list[int] = []
        self.added_projections = nn.ModuleList()
        self.output_dimension = self.configuration.representation_dim
        self.token_embedding = nn.Embedding(257, self.configuration.answer_byte_dim)
        self.answer_norm = nn.LayerNorm(self.configuration.answer_byte_dim, elementwise_affine=False)
        self.output_projection = ProjectedResidual(
            n_agents * (self.configuration.answer_byte_dim + n_agents),
            self.configuration.feedforward_dim,
            self.output_dimension,
            normalize_input=True,
        )

    def _embeddings(self, answers: Sequence[str], answer_views: Sequence[str] | None) -> list[torch.Tensor]:
        if any(not isinstance(answer, str) for answer in answers):
            raise ValueError("one canonical string answer is required per agent")
        views = tuple(answers if answer_views is None else answer_views)
        if len(views) != len(answers) or any(not isinstance(view, str) for view in views):
            raise ValueError("one deterministic string answer view is required per agent")
        device = self.token_embedding.weight.device
        result = []
        for view in views:
            payload = [byte + 1 for byte in view.encode("utf-8")] or [0]
            tokens = self.token_embedding(torch.tensor(payload, dtype=torch.long, device=device))
            positions = torch.arange(1, len(payload) + 1, dtype=tokens.dtype, device=device).unsqueeze(-1)
            result.append(self.answer_norm((tokens * positions).sum(dim=0) / positions.sum()))
        return result

    def ordered_features(self, answers: Sequence[str], answer_views: Sequence[str] | None = None) -> torch.Tensor:
        if len(answers) != self.n_agents:
            raise ValueError("one canonical string answer is required per agent")
        embeddings = self._embeddings(answers, answer_views)
        return torch.cat([part for index, embedding in enumerate(embeddings) for part in (
            embedding, embedding.new_tensor([float(answers[index] == other) for other in answers])
        )])

    def append_agent(self, agent_id: int) -> None:
        if agent_id in self.agent_ids:
            raise ValueError("agent identity already registered")
        width = self.configuration.answer_byte_dim + 2 * self.n_agents + 1
        projection = AnswerBlock(width, self.configuration).to(self.token_embedding.weight)
        self.added_projections.append(projection)
        self.added_agent_ids.append(agent_id)
        self.agent_ids = (*self.agent_ids, agent_id)
        self.n_agents += 1

    def forward(
        self, answers: Sequence[str], answer_views: Sequence[str] | None = None, *,
        active_agent_ids: Sequence[int] | None = None,
    ) -> torch.Tensor:
        active = tuple(self.agent_ids if active_agent_ids is None else active_agent_ids)
        if len(answers) != len(active) or active != self.agent_ids[:len(active)] or len(active) < len(self.initial_agent_ids):
            raise ValueError("answer-state parent mode requires an intact registered agent prefix")
        embeddings = self._embeddings(answers, answer_views)
        initial_count = len(self.initial_agent_ids)
        base = torch.cat([part for index in range(initial_count) for part in (
            embeddings[index], embeddings[index].new_tensor([
                float(answers[index] == other) for other in answers[:initial_count]
            ])
        )])
        projection = self.output_projection
        first = projection.first(projection.input_norm(base))
        skip = projection.skip(base)
        for offset, block in enumerate(self.added_projections, initial_count):
            if offset >= len(active):
                break
            agreement = embeddings[offset].new_tensor([
                *[float(answers[index] == answers[offset]) for index in range(offset)],
                *[float(answers[offset] == answers[index]) for index in range(offset)], 1.0,
            ])
            value = torch.cat((embeddings[offset], agreement))
            first = first + block.first(block.norm(value))
            skip = skip + block.skip(value)
        return projection.output_norm(projection.second(torch.nn.functional.gelu(first)) + skip)


class AnswerBlock(nn.Module):
    def __init__(self, width: int, configuration: NetworkConfig) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width, elementwise_affine=False)
        self.first = nn.Linear(width, configuration.feedforward_dim, bias=False)
        self.skip = nn.Linear(width, configuration.representation_dim, bias=False)
        nn.init.zeros_(self.first.weight)
        nn.init.zeros_(self.skip.weight)


class ColumnProjection(nn.Linear):
    """Inherited columns keep their own parameters and immutable coordinate order."""
    def __init__(self, width: int, output: int, coordinates: Sequence[str] | None = None) -> None:
        super().__init__(width, output, bias=False)
        self.coordinates = tuple(coordinates or (f"position:{index}" for index in range(width)))
        self.blocks: list[tuple[str, ...]] = [self.coordinates]
        self.added = nn.ParameterList()

    def append(self, coordinates: Sequence[str], *, mean_initialize: bool = False) -> None:
        coordinates = tuple(coordinates)
        if not set(self.coordinates).issubset(coordinates) or len(set(coordinates)) != len(coordinates):
            raise ValueError("projection coordinates must retain every registered identity")
        additional = tuple(item for item in coordinates if item not in self.coordinates)
        if additional:
            value = self.weight.new_zeros((self.out_features, len(additional)))
            if mean_initialize:
                value.copy_(torch.cat([self.weight, *self.added], dim=1).mean(dim=1, keepdim=True))
            self.added.append(nn.Parameter(value))
            self.blocks.append(additional)
        self.coordinates = coordinates
        self.in_features = len(coordinates)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        if value.shape[-1] != len(self.coordinates):
            raise ValueError("projection input does not match the registered coordinates")
        if not self.added and self.coordinates == self.blocks[0]:
            return torch.nn.functional.linear(value, self.weight)
        result = None
        for identities, weight in zip(self.blocks, (self.weight, *self.added), strict=True):
            indices = torch.tensor([self.coordinates.index(item) for item in identities], device=value.device)
            projected = torch.nn.functional.linear(value.index_select(-1, indices), weight)
            result = projected if result is None else result + projected
        return result


class RowProjection(nn.Linear):
    """Append Q rows without replacing inherited Parameters or their Adam state."""
    def __init__(self, width: int, labels: int) -> None:
        super().__init__(width, labels)
        self.added_weights = nn.ParameterList()
        self.added_biases = nn.ParameterList()

    def append(self, count: int) -> None:
        if count <= 0:
            raise ValueError("at least one new output label is required")
        self.added_weights.append(nn.Parameter(self.weight.new_zeros((count, self.in_features))))
        self.added_biases.append(nn.Parameter(self.bias.new_zeros(count)))
        self.out_features += count

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return torch.cat([torch.nn.functional.linear(value, weight, bias) for weight, bias in zip(
            (self.weight, *self.added_weights), (self.bias, *self.added_biases), strict=True
        )], dim=-1)


class ConditionedHistory(nn.Module):
    """One unmasked Transformer per decision, followed by relation-biased readouts."""

    def __init__(self, n_agents: int, event_dimension: int, configuration: NetworkConfig, *, routing: bool) -> None:
        super().__init__()
        self.n_agents = n_agents
        self.event_dimension = event_dimension
        self.configuration = configuration
        self.routing = routing
        dimension = configuration.representation_dim
        self.summary_token = nn.Parameter(torch.empty(dimension))
        nn.init.normal_(self.summary_token, std=0.02)
        self.input_projection = ColumnProjection(event_dimension, dimension)

        def new_layer() -> nn.TransformerEncoderLayer:
            return nn.TransformerEncoderLayer(
                d_model=dimension,
                nhead=configuration.transformer_heads,
                dim_feedforward=configuration.feedforward_dim,
                dropout=configuration.dropout,
                activation="gelu",
                batch_first=True,
                norm_first=configuration.transformer_norm_first,
            )

        # TransformerEncoder clones its prototype's initialized matrices. Keep
        # the public stack implementation, but instantiate every additional layer
        # independently with successive draws from the initialization stream.
        self.transformer = nn.TransformerEncoder(new_layer(), 1, enable_nested_tensor=False)
        self.transformer.layers.extend(new_layer() for _ in range(configuration.transformer_layers - 1))
        self.transformer.num_layers = configuration.transformer_layers
        if routing:
            self.sender_query = ColumnProjection(n_agents, dimension)
            self.receiver_query = ColumnProjection(n_agents, dimension)
        else:
            self.agent_query = ColumnProjection(n_agents, dimension)
        self.attention_query = nn.Linear(dimension, dimension, bias=False)
        self.attention_key = nn.Linear(dimension, dimension, bias=False)
        self.attention_value = nn.Linear(dimension, dimension, bias=False)
        self.relation_bias = nn.Parameter(torch.zeros(5 if routing else 4))
        # Projections may be identity because all three components already have dimension d.
        self.query_norm = nn.LayerNorm(dimension, elementwise_affine=False)
        self.global_norm = nn.LayerNorm(dimension, elementwise_affine=False)
        self.context_norm = nn.LayerNorm(dimension, elementwise_affine=False)
        self.readout = ProjectedResidual(3 * dimension, configuration.feedforward_dim, dimension, normalize_input=False)

    def _positions(self, indices: Sequence[int], prototype: torch.Tensor) -> torch.Tensor:
        dimension = self.configuration.representation_dim
        positions = prototype.new_tensor(indices).unsqueeze(-1)
        frequencies = torch.exp(
            torch.arange(0, dimension, 2, dtype=prototype.dtype, device=prototype.device)
            * (-math.log(10000.0) / dimension)
        )
        encoded = prototype.new_zeros((len(indices), dimension))
        encoded[:, 0::2] = torch.sin(positions * frequencies)
        encoded[:, 1::2] = torch.cos(positions * frequencies[: dimension // 2])
        return encoded

    def contextualize(self, history: HistoryEncoding) -> tuple[torch.Tensor, torch.Tensor]:
        if not isinstance(history, HistoryEncoding):
            raise TypeError("history must be a fixed HistoryEncoding event sequence")
        if history.vectors.shape[1] != self.event_dimension:
            raise ValueError("history event dimension does not match this network")
        events = self.input_projection(history.vectors) + self._positions(history.history_indices, history.vectors)
        inputs = torch.cat((self.summary_token.unsqueeze(0), events), dim=0).unsqueeze(0)
        contextual = self.transformer(inputs).squeeze(0)
        return contextual[0], contextual[1:]

    def conditioned(
        self,
        history: HistoryEncoding,
        contextual: tuple[torch.Tensor, torch.Tensor],
        first: int,
        second: int | None = None,
    ) -> torch.Tensor:
        if not 0 <= first < self.n_agents or (second is not None and not 0 <= second < self.n_agents):
            raise IndexError("agent identity outside configured pool")
        one_hot = self.summary_token.new_zeros(self.n_agents)
        one_hot[first] = 1.0
        if self.routing:
            if second is None:
                raise ValueError("routing history needs both ordered endpoints")
            receiver = torch.nn.functional.one_hot(torch.tensor(second, device=one_hot.device), self.n_agents).to(
                one_hot.dtype
            )
            query = self.sender_query(one_hot) + self.receiver_query(receiver)
            relations = [event.routing_relation(first, second) for event in history.identities]
        else:
            query = self.agent_query(one_hot)
            relations = [event.weighting_relation(first) for event in history.identities]
        global_state, event_states = contextual
        if len(relations):
            relation_indices = torch.tensor(relations, dtype=torch.long, device=query.device)
            logits = self.attention_key(event_states) @ self.attention_query(query) / math.sqrt(query.numel())
            weights = torch.softmax(logits + self.relation_bias[relation_indices], dim=0)
            context = self.context_norm(weights @ self.attention_value(event_states))
        else:
            # This remains exactly zero, even if learned output norms later have nonzero bias.
            context = torch.zeros_like(query)
        return self.readout(torch.cat((self.query_norm(query), self.global_norm(global_state), context)))


class RoutingHead(nn.Module):
    def __init__(self, configuration: NetworkConfig, labels: int) -> None:
        super().__init__()
        dimension = configuration.representation_dim
        self.comparisons = nn.ModuleList(
            [
                nn.Sequential(
                    nn.LayerNorm(dimension, elementwise_affine=False), nn.Linear(dimension, dimension, bias=False)
                )
                for _ in range(3)
            ]
        )
        self.input_norm = nn.LayerNorm(6 * dimension, elementwise_affine=False)
        self.hidden = nn.Linear(6 * dimension, configuration.feedforward_dim)
        self.output = RowProjection(configuration.feedforward_dim, labels)
        nn.init.zeros_(self.output.weight)
        nn.init.zeros_(self.output.bias)

    def interaction_features(self, query: torch.Tensor, answers: torch.Tensor, history: torch.Tensor) -> torch.Tensor:
        x, y, h = [mapping(value) for mapping, value in zip(self.comparisons, (query, answers, history), strict=True)]
        return torch.cat((x, y, h, x * y, x * h, y * h))

    def forward(self, query: torch.Tensor, answers: torch.Tensor, history: torch.Tensor) -> torch.Tensor:
        feature = self.interaction_features(query, answers, history)
        return self.output(torch.nn.functional.gelu(self.hidden(self.input_norm(feature))))


class RoutingPolicy(nn.Module):
    """Concrete alpha: query, canonical answer, Transformer history, interaction head."""

    def __init__(
        self,
        n_agents: int,
        event_dimension: int,
        *,
        configuration: NetworkConfig | None = None,
        scheme_order: Sequence[Scheme] | None = None,
        legality: Mapping[str, Sequence[Edge]] | None = None,
    ) -> None:
        super().__init__()
        if min(n_agents, event_dimension) <= 0:
            raise ValueError("agent count and event dimension must be positive")
        self.configuration = configuration or NetworkConfig()
        self.n_agents = n_agents
        self.scheme_order = tuple(scheme_order or _default_scheme_order())
        if len(set(self.scheme_order)) != len(self.scheme_order) or not set(_default_scheme_order()).issubset(self.scheme_order):
            raise ValueError("scheme_order must retain each built-in label exactly once")
        self.legality = {str(label): tuple(tuple(edge) for edge in edges) for label, edges in (legality or {}).items()}
        legal_label_mask(n_agents, self.scheme_order, legality=self.legality)
        self.agent_ids = tuple(range(n_agents))
        self.query_map = _query_mapping(self.configuration)
        self.answer_state_encoder = AnswerStateEncoder(n_agents, configuration=self.configuration)
        self.history_map = ConditionedHistory(n_agents, event_dimension, self.configuration, routing=True)
        self.routing_head = RoutingHead(self.configuration, len(self.scheme_order))

    def forward(
        self,
        base_query: torch.Tensor,
        base_history: HistoryEncoding,
        answers: Sequence[str],
        *,
        answer_views: Sequence[str] | None = None,
        active_agent_ids: Sequence[int] | None = None,
        active_scheme_order: Sequence[Scheme] | None = None,
    ) -> RoutingPolicyOutput:
        if base_query.shape != (TEXT_DIMENSION,):
            raise ValueError("routing requires one 384-dimensional fixed query representation")
        query = self.query_map(base_query)
        active = tuple(self.agent_ids if active_agent_ids is None else active_agent_ids)
        order = tuple(self.scheme_order if active_scheme_order is None else active_scheme_order)
        if active != self.agent_ids[:len(active)] or not set(order).issubset(self.scheme_order):
            raise ValueError("parent mode must preserve semantic identities")
        answer_state = self.answer_state_encoder(answers, answer_views, active_agent_ids=active)
        contextual = self.history_map.contextualize(base_history)
        logits = torch.stack(
            [
                self.routing_head(
                    query, answer_state, self.history_map.conditioned(base_history, contextual, initiator, receiver)
                )
                for initiator in active
                for receiver in active
            ]
        ).reshape(len(active), len(active), -1)
        indices = torch.tensor([self.scheme_order.index(label) for label in order], device=logits.device)
        values = logits.index_select(-1, indices)
        mask = legal_label_mask(len(active), order, device=values.device, legality=self.legality)
        return RoutingPolicyOutput(values, mask, order)

    def route_training(
        self,
        base_query: torch.Tensor,
        base_history: HistoryEncoding,
        answers: Sequence[str],
        outgoing_capacity: Capacity,
        incoming_capacity: Capacity,
        *,
        session: int,
        generator: torch.Generator | None = None,
        permutation: Sequence[Edge] | None = None,
        answer_views: Sequence[str] | None = None,
    ) -> RoutingAction:
        output = self(base_query, base_history, answers, answer_views=answer_views)
        return sample_capacity_feasible(
            output.probabilities,
            output.scheme_order,
            outgoing_capacity,
            incoming_capacity,
            session=session,
            generator=generator,
            permutation=permutation,
            legality=self.legality,
            q_values=output.values,
        )

    @torch.no_grad()
    def route_inference(
        self,
        base_query: torch.Tensor,
        base_history: HistoryEncoding,
        answers: Sequence[str],
        outgoing_capacity: Capacity,
        incoming_capacity: Capacity,
        *,
        session: int,
        answer_views: Sequence[str] | None = None,
    ) -> RoutingAction:
        output = self(base_query, base_history, answers, answer_views=answer_views)
        return decode_capacity_feasible(
            output.values, output.scheme_order, outgoing_capacity, incoming_capacity, session=session,
            legality=self.legality,
        )


def _validate_distribution(probabilities: torch.Tensor, scheme_order: Sequence[Scheme]) -> int:
    if probabilities.ndim != 3 or probabilities.shape[0] != probabilities.shape[1]:
        raise ValueError("routing probabilities must have shape [N,N,labels]")
    if probabilities.shape[-1] != len(scheme_order):
        raise ValueError("routing probability label dimension does not match scheme_order")
    if not bool(torch.isfinite(probabilities).all()) or bool((probabilities < 0).any()):
        raise ValueError("routing probabilities must be finite and non-negative")
    if not bool((probabilities.sum(dim=-1) > 0).all()):
        raise ValueError("every edge distribution must have positive mass")
    return probabilities.shape[0]


def sample_capacity_feasible(
    probabilities: torch.Tensor,
    scheme_order: Sequence[Scheme],
    outgoing_capacity: Capacity,
    incoming_capacity: Capacity,
    *,
    session: int,
    generator: torch.Generator | None = None,
    permutation: Sequence[Edge] | None = None,
    legality: Mapping[str, Sequence[Edge]] | None = None,
    q_values: torch.Tensor | None = None,
) -> RoutingAction:
    """Sample Step 4's random-permutation, masked categorical action.

    Probabilities and sampled labels are frozen collection metadata. Raw Q
    predictions are recorded separately; only those predictions can participate
    in the pooled regression. Capacity-forced zero records are excluded.
    """

    order = tuple(scheme_order)
    probabilities = probabilities.detach()
    n_agents = _validate_distribution(probabilities, order)
    zero_index = order.index(Scheme.ZERO)
    remaining_out = list(_capacity_vector(outgoing_capacity, n_agents, "outgoing_capacity"))
    remaining_in = list(_capacity_vector(incoming_capacity, n_agents, "incoming_capacity"))
    all_edges = tuple((j, i) for j in range(n_agents) for i in range(n_agents))
    if permutation is None:
        positions = torch.randperm(len(all_edges), generator=generator).tolist()
        edge_order = tuple(all_edges[position] for position in positions)
    else:
        edge_order = tuple((int(edge[0]), int(edge[1])) for edge in permutation)
        if len(edge_order) != len(all_edges) or set(edge_order) != set(all_edges):
            raise ValueError("permutation must contain every ordered edge exactly once")

    matrix: dict[Edge, Scheme] = {}
    decisions: list[SampledDecision] = []
    for position, (initiator, receiver) in enumerate(edge_order):
        free = remaining_out[initiator] > 0 and remaining_in[receiver] > 0
        structural = legal_schemes(initiator, receiver, order, legality)
        feasible = structural if free else (Scheme.ZERO,)
        if free:
            indices = torch.tensor([order.index(scheme) for scheme in feasible], device=probabilities.device)
            feasible_mass = probabilities[initiator, receiver].index_select(0, indices)
            feasible_mass = feasible_mass / feasible_mass.sum()
            sampled_local = int(torch.multinomial(feasible_mass.detach().cpu(), 1, generator=generator).item())
            scheme = feasible[sampled_local]
            selected_probability = feasible_mass[sampled_local]
            log_probability = torch.log(selected_probability)
            probability = float(selected_probability.detach().cpu())
            masked = probabilities.new_zeros(len(order))
            masked.index_copy_(0, indices, feasible_mass)
        else:
            scheme = Scheme.ZERO
            probability = 1.0
            log_probability = probabilities.new_zeros(())
            masked = probabilities.new_zeros(len(order))
            masked[zero_index] = 1.0

        if scheme is not Scheme.ZERO:
            remaining_out[initiator] -= 1
            remaining_in[receiver] -= 1
        matrix[(initiator, receiver)] = scheme
        decisions.append(
            SampledDecision(
                session=session,
                position=position,
                initiator_id=initiator,
                receiver_id=receiver,
                scheme=scheme,
                legal_schemes=feasible,
                probability=probability,
                log_probability=log_probability,
                free=free,
                forced_zero=not free,
                remaining_out=tuple(remaining_out),
                remaining_in=tuple(remaining_in),
                legal_mask=tuple(scheme in feasible for scheme in order),
                base_probabilities=tuple(float(value) for value in probabilities[initiator, receiver].detach().cpu()),
                masked_probabilities=tuple(float(value) for value in masked.detach().cpu()),
                q_value=None if q_values is None else q_values[initiator, receiver, order.index(scheme)],
            )
        )
    return RoutingAction(
        session=session,
        matrix=matrix,
        decisions=tuple(decisions),
        permutation=edge_order,
        mode=RoutingMode.TRAIN,
        routing_generator_state=bytes(
            (generator.get_state() if generator is not None else torch.random.get_rng_state()).tolist()
        ),
    )


@dataclass
class _FlowArc:
    destination: int
    reverse: int
    capacity: int
    cost: tuple[float, int]


def _add_flow_arc(
    graph: list[list[_FlowArc]],
    source: int,
    destination: int,
    capacity: int,
    cost: tuple[float, int],
) -> int:
    forward_index = len(graph[source])
    reverse_index = len(graph[destination])
    graph[source].append(_FlowArc(destination, reverse_index, capacity, cost))
    graph[destination].append(_FlowArc(source, forward_index, 0, (-cost[0], -cost[1])))
    return forward_index


def exact_b_matching(
    beta: torch.Tensor | Sequence[Sequence[float]],
    outgoing_capacity: Capacity,
    incoming_capacity: Capacity,
) -> tuple[Edge, ...]:
    """Solve the specification's maximum-score bipartite b-matching exactly.

    This is a deterministic successive-shortest-path min-cost-flow solver
    with an exact two-component objective. The first component minimizes
    negative beta, and only among equal primary optima does the second
    component minimize the row-major binary activation vector. No epsilon
    perturbation is applied to beta.
    Only strictly positive-beta edges are inserted, so the optional-flow
    optimum never activates an edge whose best nonzero label does not beat
    zero.
    """

    score = torch.as_tensor(beta, dtype=torch.float64).detach().cpu()
    if score.ndim != 2 or score.shape[0] != score.shape[1]:
        raise ValueError("beta must be a square [N,N] matrix")
    if not bool(torch.isfinite(score).all()):
        raise ValueError("beta must contain finite values")
    n_agents = score.shape[0]
    out_capacity = _capacity_vector(outgoing_capacity, n_agents, "outgoing_capacity")
    in_capacity = _capacity_vector(incoming_capacity, n_agents, "incoming_capacity")

    source = 0
    sender_offset = 1
    receiver_offset = sender_offset + n_agents
    sink = receiver_offset + n_agents
    graph: list[list[_FlowArc]] = [[] for _ in range(sink + 1)]
    for initiator, capacity in enumerate(out_capacity):
        _add_flow_arc(graph, source, sender_offset + initiator, capacity, (0.0, 0))
    edge_arcs: dict[Edge, tuple[int, int]] = {}
    for initiator in range(n_agents):
        for receiver in range(n_agents):
            value = float(score[initiator, receiver])
            if value > 0.0:
                node = sender_offset + initiator
                row_major_position = initiator * n_agents + receiver
                lexicographic_weight = 1 << (n_agents * n_agents - 1 - row_major_position)
                arc_index = _add_flow_arc(
                    graph,
                    node,
                    receiver_offset + receiver,
                    1,
                    (-value, lexicographic_weight),
                )
                edge_arcs[(initiator, receiver)] = (node, arc_index)
    for receiver, capacity in enumerate(in_capacity):
        _add_flow_arc(graph, receiver_offset + receiver, sink, capacity, (0.0, 0))

    # Each negative-cost augmenting path increases the original beta objective.
    while True:
        distance: list[tuple[float, int] | None] = [None] * len(graph)
        predecessor: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = (0.0, 0)
        for _ in range(len(graph) - 1):
            changed = False
            for node, arcs in enumerate(graph):
                if distance[node] is None:
                    continue
                for arc_index, arc in enumerate(arcs):
                    candidate = (
                        distance[node][0] + arc.cost[0],
                        distance[node][1] + arc.cost[1],
                    )
                    if arc.capacity > 0 and (
                        distance[arc.destination] is None or candidate < distance[arc.destination]
                    ):
                        distance[arc.destination] = candidate
                        predecessor[arc.destination] = (node, arc_index)
                        changed = True
            if not changed:
                break
        if predecessor[sink] is None or distance[sink] >= (0.0, 0):
            break
        node = sink
        while node != source:
            step = predecessor[node]
            if step is None:  # pragma: no cover - protected by path construction
                raise RuntimeError("incomplete augmenting path")
            previous, arc_index = step
            arc = graph[previous][arc_index]
            arc.capacity -= 1
            graph[node][arc.reverse].capacity += 1
            node = previous

    selected = [edge for edge, (node, arc_index) in edge_arcs.items() if graph[node][arc_index].capacity == 0]
    return tuple(sorted(selected))


def routing_beta(
    values: torch.Tensor, scheme_order: Sequence[Scheme], *,
    legality: Mapping[str, Sequence[Edge]] | None = None,
) -> tuple[torch.Tensor, dict[Edge, Scheme]]:
    """Raw mixed-Q improvement against zero, with deterministic scheme tie order."""
    order = tuple(scheme_order)
    if values.ndim != 3 or values.shape[0] != values.shape[1] or values.shape[-1] != len(order):
        raise ValueError("routing values must have shape [N,N,labels]")
    if not bool(torch.isfinite(values).all()):
        raise ValueError("routing values must be finite")
    n_agents = values.shape[0]
    zero_index = order.index(Scheme.ZERO)
    beta = values.new_empty((n_agents, n_agents))
    best_scheme: dict[Edge, Scheme] = {}
    for initiator in range(n_agents):
        for receiver in range(n_agents):
            nonzero = [scheme for scheme in legal_schemes(initiator, receiver, order, legality) if scheme is not Scheme.ZERO]
            indices = torch.tensor([order.index(scheme) for scheme in nonzero], device=values.device)
            selected = values[initiator, receiver].index_select(0, indices)
            index = int(torch.argmax(selected).item())
            best_scheme[(initiator, receiver)] = nonzero[index]
            beta[initiator, receiver] = selected[index] - values[initiator, receiver, zero_index]
    return beta, best_scheme


def decode_capacity_feasible(
    values: torch.Tensor, scheme_order: Sequence[Scheme], outgoing_capacity: Capacity, incoming_capacity: Capacity,
    *, session: int, legality: Mapping[str, Sequence[Edge]] | None = None,
) -> RoutingAction:
    """Exact b-matching of raw mixed-Q differences; no evaluation softmax."""
    beta, best_scheme = routing_beta(values, scheme_order, legality=legality)
    selected = set(exact_b_matching(beta, outgoing_capacity, incoming_capacity))
    matrix = {
        (initiator, receiver): best_scheme[(initiator, receiver)] if (initiator, receiver) in selected else Scheme.ZERO
        for initiator in range(values.shape[0]) for receiver in range(values.shape[0])
    }
    return RoutingAction(
        session=session, matrix=matrix, decisions=(), permutation=(), mode=RoutingMode.INFERENCE,
        objective_score=float(sum(beta[edge].detach().cpu() for edge in selected)),
    )


@dataclass(frozen=True)
class WeightingOutput:
    logits: torch.Tensor
    weights: torch.Tensor


def _direct_candidate_mass_nll(
    reliability_logits: torch.Tensor,
    resolved_answers: Sequence[Any],
    gold_answer: Any,
) -> torch.Tensor:
    """Evaluate ``-log(s_gold)`` stably from the agent reliability logits."""

    if reliability_logits.ndim != 1 or len(resolved_answers) != reliability_logits.shape[0]:
        raise ValueError("logits and resolved_answers must contain one item per agent")
    correct_mask = torch.tensor(
        [answer == gold_answer for answer in resolved_answers],
        dtype=torch.bool,
        device=reliability_logits.device,
    )
    if not bool(correct_mask.any()):
        raise ValueError("gold_answer must be represented by a resolved answer")
    log_agent_weights = torch.log_softmax(reliability_logits, dim=0)
    return -torch.logsumexp(log_agent_weights[correct_mask], dim=0)


@dataclass(frozen=True)
class WeightedVote:
    candidates: tuple[Any, ...]
    scores: torch.Tensor
    prediction: Any

    @property
    def probabilities(self) -> torch.Tensor:
        """Read-only alias: candidate scores already are probabilities."""

        return self.scores

    def index(self, answer: Any) -> int:
        return self.candidates.index(answer)

    def score(self, answer: Any) -> torch.Tensor:
        return self.scores[self.index(answer)]

    def probability(self, answer: Any) -> torch.Tensor:
        return self.score(answer)


class ReliabilityHead(nn.Module):
    def __init__(self, configuration: NetworkConfig) -> None:
        super().__init__()
        dimension = configuration.representation_dim
        self.comparisons = nn.ModuleList(
            [
                nn.Sequential(
                    nn.LayerNorm(dimension, elementwise_affine=False), nn.Linear(dimension, dimension, bias=False)
                )
                for _ in range(2)
            ]
        )
        self.input_norm = nn.LayerNorm(4 * dimension, elementwise_affine=False)
        self.hidden = nn.Linear(4 * dimension, dimension)
        self.output = nn.Linear(dimension, 1)
        nn.init.zeros_(self.output.weight)
        nn.init.zeros_(self.output.bias)

    def interaction_features(self, query: torch.Tensor, history: torch.Tensor) -> torch.Tensor:
        x, h = [mapping(value) for mapping, value in zip(self.comparisons, (query, history), strict=True)]
        return torch.cat((x, h, x * h, torch.abs(x - h)))

    def forward(self, query: torch.Tensor, history: torch.Tensor) -> torch.Tensor:
        feature = self.interaction_features(query, history)
        return self.output(torch.nn.functional.gelu(self.hidden(self.input_norm(feature)))).squeeze(-1)


class WeightingModel(nn.Module):
    """Concrete disjoint W with agent-conditioned event history and direct vote mass."""

    def __init__(self, n_agents: int, event_dimension: int, *, configuration: NetworkConfig | None = None) -> None:
        super().__init__()
        if min(n_agents, event_dimension) <= 0:
            raise ValueError("agent count and event dimension must be positive")
        self.configuration = configuration or NetworkConfig()
        self.n_agents = n_agents
        self.query_map = _query_mapping(self.configuration)
        self.history_map = ConditionedHistory(n_agents, event_dimension, self.configuration, routing=False)
        self.reliability_head = ReliabilityHead(self.configuration)

    def forward(
        self, base_query: torch.Tensor, base_history: HistoryEncoding, *, active_agent_ids: Sequence[int] | None = None,
    ) -> WeightingOutput:
        if base_query.shape != (TEXT_DIMENSION,):
            raise ValueError("weighting requires one 384-dimensional fixed query representation")
        query = self.query_map(base_query)
        contextual = self.history_map.contextualize(base_history)
        logits = torch.stack(
            [
                self.reliability_head(query, self.history_map.conditioned(base_history, contextual, agent))
                for agent in (range(self.n_agents) if active_agent_ids is None else active_agent_ids)
            ]
        )
        return WeightingOutput(logits, torch.softmax(logits, dim=0))

    def vote(
        self,
        weights: torch.Tensor,
        resolved_answers: Sequence[Any],
        *,
        candidate_order: Sequence[Any] | None = None,
    ) -> WeightedVote:
        """Aggregate agent weights into realized-candidate probability masses."""

        if weights.shape != (self.n_agents,) or len(resolved_answers) != self.n_agents:
            raise ValueError("weights and resolved_answers must contain one item per agent")
        realized = set(resolved_answers)
        if candidate_order is None:
            candidates = tuple(sorted(realized, key=lambda answer: (type(answer).__name__, str(answer))))
        else:
            ordered = tuple(candidate_order)
            if len(set(ordered)) != len(ordered) or not realized.issubset(ordered):
                raise ValueError("candidate_order must uniquely contain every realized answer")
            candidates = tuple(candidate for candidate in ordered if candidate in realized)
        scores = torch.stack(
            [
                weights[
                    torch.tensor(
                        [answer == candidate for answer in resolved_answers],
                        dtype=torch.bool,
                        device=weights.device,
                    )
                ].sum()
                for candidate in candidates
            ]
        )
        prediction = candidates[int(torch.argmax(scores).item())]
        return WeightedVote(candidates, scores, prediction)

    @staticmethod
    def is_informative(resolved_answers: Sequence[Any], gold_answer: Any) -> bool:
        candidates = set(resolved_answers)
        return gold_answer in candidates and len(candidates) > 1

    def session_loss(
        self,
        base_query: torch.Tensor,
        base_history: HistoryEncoding,
        resolved_answers: Sequence[Any],
        gold_answer: Any,
        *,
        candidate_order: Sequence[Any] | None = None,
    ) -> torch.Tensor | None:
        """Return Step 23 loss, or ``None`` for a non-informative session."""

        if not self.is_informative(resolved_answers, gold_answer):
            return None
        output = self(base_query, base_history)
        # Retain candidate-order validation and grouping behavior while using
        # logits privately for a stable evaluation of -log(s_gold).
        self.vote(output.weights, resolved_answers, candidate_order=candidate_order)
        return _direct_candidate_mass_nll(output.logits, resolved_answers, gold_answer)


@dataclass(frozen=True)
class ArchitectureExpansion:
    """A copied extension and its precisely isolated, newly introduced parameters."""

    alpha: RoutingPolicy
    weighting: WeightingModel
    encoders: FixedBaseEncoders
    alpha_parameters: tuple[str, ...]
    weighting_parameters: tuple[str, ...]
    metadata: dict[str, Any]


def expand_models(
    alpha: RoutingPolicy,
    weighting: WeightingModel,
    parent_encoders: FixedBaseEncoders,
    *,
    agent_ids: Sequence[int],
    scheme_order: Sequence[Scheme],
    legality: Mapping[str, Sequence[Edge]] | None = None,
    categories: Mapping[str, Sequence[str | None]] | None = None,
) -> ArchitectureExpansion:
    """Copy a parent and append independent blocks under semantic identities.

    The returned parameter names contain only blocks introduced at this boundary.
    Optimizers must preserve inherited states by name and use a separate optimizer
    for these blocks; resizing a shared Adam tensor would violate that contract.
    """
    ids, labels = tuple(agent_ids), tuple(scheme_order)
    if ids != tuple(range(len(ids))) or ids[:alpha.n_agents] != tuple(parent_encoders.agent_ids):
        raise ValueError("agents must retain their registered append-only integer identities")
    if labels[:len(alpha.scheme_order)] != alpha.scheme_order:
        raise ValueError("scheme labels must retain their registered append-only order")
    if weighting.n_agents != alpha.n_agents or parent_encoders.n_agents != alpha.n_agents:
        raise ValueError("parent model/encoder agent registries differ")
    encoders = parent_encoders.extended(agent_ids=ids, scheme_order=labels, categories=categories)
    routing, weights = copy.deepcopy(alpha), copy.deepcopy(weighting)
    old_alpha = set(dict(routing.named_parameters()))
    old_weighting = set(dict(weights.named_parameters()))
    legal = dict(routing.legality)
    legal.update({str(label): tuple(tuple(edge) for edge in edges) for label, edges in (legality or {}).items()})
    for label, edges in routing.legality.items():
        if legal.get(label) != edges:
            raise ValueError("an inherited scheme legal-edge registration cannot be replaced")
    legal_label_mask(len(ids), labels, legality=legal)
    for model in (routing, weights):
        history = model.history_map
        projection = history.input_projection
        if not projection.added:
            projection.coordinates = parent_encoders.semantic_coordinates
            projection.blocks = [parent_encoders.semantic_coordinates]
        if projection.coordinates != parent_encoders.semantic_coordinates:
            raise ValueError("parent history projection semantics do not match the encoder registry")
        projection.append(encoders.semantic_coordinates)
        history.event_dimension = encoders.history_dimension
        names = ("sender_query", "receiver_query") if history.routing else ("agent_query",)
        for name in names:
            identity = getattr(history, name)
            if not identity.added:
                identity.coordinates = tuple(f"agent:{agent}" for agent in parent_encoders.agent_ids)
                identity.blocks = [identity.coordinates]
            for agent in ids[len(parent_encoders.agent_ids):]:
                identity.append((*identity.coordinates, f"agent:{agent}"), mean_initialize=True)
        history.n_agents = len(ids)
        model.n_agents = len(ids)
    for agent in ids[len(parent_encoders.agent_ids):]:
        routing.answer_state_encoder.append_agent(agent)
    routing.agent_ids = ids
    added_labels = len(labels) - len(routing.scheme_order)
    if added_labels:
        routing.routing_head.output.append(added_labels)
    routing.scheme_order = labels
    routing.legality = legal
    alpha_names = tuple(name for name, _ in routing.named_parameters() if name not in old_alpha)
    weight_names = tuple(name for name, _ in weights.named_parameters() if name not in old_weighting)
    if not alpha_names and not weight_names:
        raise ValueError("extension must introduce at least one registered parameter block")
    boundary = {
        "agent_ids": list(ids), "scheme_order": [label.value for label in labels],
        "legality": {label: [list(edge) for edge in edges] for label, edges in legal.items()},
        "categories": {name: list(values) for name, values in encoders.categories.items()},
        "alpha_parameters": list(alpha_names), "weighting_parameters": list(weight_names),
    }
    parent_metadata = getattr(alpha, "extension_metadata", {
        "protocol": "semantic_parameter_blocks_v1",
        "initial_agents": list(parent_encoders.agent_ids),
        "initial_scheme_order": [label.value for label in alpha.scheme_order],
        "initial_categories": {name: list(values) for name, values in parent_encoders.categories.items()},
        "initial_legality": {label: [list(edge) for edge in edges] for label, edges in alpha.legality.items()},
        "boundaries": [],
    })
    metadata = copy.deepcopy(parent_metadata)
    metadata["boundaries"].append(boundary)
    routing.extension_metadata = copy.deepcopy(metadata)
    weights.extension_metadata = copy.deepcopy(metadata)
    return ArchitectureExpansion(routing, weights, encoders, alpha_names, weight_names, metadata)


def models_from_expansion_metadata(
    metadata: Mapping[str, Any], *, configuration: NetworkConfig,
    encoder_configuration: Any, text_encoder: Any = None,
) -> ArchitectureExpansion:
    """Rebuild exact append boundaries before strict state/optimizer restoration."""
    if metadata.get("protocol") != "semantic_parameter_blocks_v1" or not metadata.get("boundaries"):
        raise ValueError("unsupported or empty semantic expansion metadata")
    ids = tuple(metadata["initial_agents"])
    labels = tuple(Scheme(label) for label in metadata["initial_scheme_order"])
    encoders = FixedBaseEncoders(
        len(ids), configuration=encoder_configuration, text_encoder=text_encoder, agent_ids=ids,
        scheme_order=labels, categories=metadata["initial_categories"],
    )
    alpha = RoutingPolicy(
        len(ids), encoders.history_dimension, configuration=configuration, scheme_order=labels,
        legality=metadata["initial_legality"],
    )
    weighting = WeightingModel(len(ids), encoders.history_dimension, configuration=configuration)
    result = None
    for boundary in metadata["boundaries"]:
        result = expand_models(
            alpha, weighting, encoders, agent_ids=boundary["agent_ids"],
            scheme_order=tuple(Scheme(label) for label in boundary["scheme_order"]),
            legality=boundary["legality"], categories=boundary["categories"],
        )
        if result.metadata["boundaries"][-1] != dict(boundary):
            raise ValueError("expanded parameter registry differs from checkpoint metadata")
        alpha, weighting, encoders = result.alpha, result.weighting, result.encoders
    assert result is not None
    return result
