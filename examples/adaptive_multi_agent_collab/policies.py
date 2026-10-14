"""Trainable routing (alpha) and reliability-weighting (W) models.

The models share only fixed query/history encoder outputs. Alpha separately
owns the trainable session-start answer-state encoder. All ``torch`` parameters
belong to disjoint module instances, making the alpha/W gradient boundary
explicit rather than enforcing it by naming convention.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

from .config import NetworkConfig
from .encoders import HistoryEncoding, TEXT_DIMENSION
from .randomness import SEED_DERIVATION_ID, derive_seed
from .schemas import RoutingAction, RoutingMode, SampledDecision, Scheme

Edge = tuple[int, int]
Capacity = int | Sequence[int]
ANSWER_STATE_ARCHITECTURE_ID = "canonical_byte_agreement_residual_v1"
ROUTING_ARCHITECTURE_ID = "independent_layer_event_transformer_directional_interaction_v2"
WEIGHTING_ARCHITECTURE_ID = "independent_layer_event_transformer_agent_interaction_v2"
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


def legal_schemes(initiator_id: int, receiver_id: int) -> tuple[Scheme, ...]:
    """Return Specification Step 3's structural label set for one edge."""

    if initiator_id == receiver_id:
        return (Scheme.ZERO, Scheme.SELF)
    return (Scheme.ZERO, Scheme.DEBATE, Scheme.JUDGE, Scheme.TEACHER, Scheme.REVIEWER)


def legal_label_mask(
    n_agents: int,
    scheme_order: Sequence[Scheme] | None = None,
    *,
    device: torch.device | str | None = None,
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
            legal = set(legal_schemes(initiator, receiver))
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
    logits: torch.Tensor
    probabilities: torch.Tensor
    scheme_order: tuple[Scheme, ...]

    def probability(self, edge: Edge, scheme: Scheme) -> torch.Tensor:
        return self.probabilities[edge[0], edge[1], self.scheme_order.index(scheme)]


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
        self.output_dimension = self.configuration.representation_dim
        self.token_embedding = nn.Embedding(257, self.configuration.answer_byte_dim)
        self.answer_norm = nn.LayerNorm(self.configuration.answer_byte_dim, elementwise_affine=False)
        self.output_projection = ProjectedResidual(
            n_agents * (self.configuration.answer_byte_dim + n_agents),
            self.configuration.feedforward_dim,
            self.output_dimension,
            normalize_input=True,
        )

    def ordered_features(self, answers: Sequence[str], answer_views: Sequence[str] | None = None) -> torch.Tensor:
        if len(answers) != self.n_agents or any(not isinstance(answer, str) for answer in answers):
            raise ValueError("one canonical string answer is required per agent")
        views = tuple(answers if answer_views is None else answer_views)
        if len(views) != self.n_agents or any(not isinstance(view, str) for view in views):
            raise ValueError("one deterministic string answer view is required per agent")
        device = self.token_embedding.weight.device
        blocks: list[torch.Tensor] = []
        for answer, view in zip(answers, views, strict=True):
            payload = [byte + 1 for byte in view.encode("utf-8")] or [0]
            tokens = self.token_embedding(torch.tensor(payload, dtype=torch.long, device=device))
            positions = torch.arange(1, len(payload) + 1, dtype=tokens.dtype, device=device).unsqueeze(-1)
            embedding = (tokens * positions).sum(dim=0) / positions.sum()
            agreement = tokens.new_tensor([float(answer == other) for other in answers])
            blocks.extend((self.answer_norm(embedding), agreement))
        return torch.cat(blocks)

    def forward(self, answers: Sequence[str], answer_views: Sequence[str] | None = None) -> torch.Tensor:
        return self.output_projection(self.ordered_features(answers, answer_views))


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
        self.input_projection = nn.Linear(event_dimension, dimension, bias=False)

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
            self.sender_query = nn.Linear(n_agents, dimension, bias=False)
            self.receiver_query = nn.Linear(n_agents, dimension, bias=False)
        else:
            self.agent_query = nn.Linear(n_agents, dimension, bias=False)
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
        self.output = nn.Linear(configuration.feedforward_dim, labels)
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
    ) -> None:
        super().__init__()
        if min(n_agents, event_dimension) <= 0:
            raise ValueError("agent count and event dimension must be positive")
        self.configuration = configuration or NetworkConfig()
        self.n_agents = n_agents
        self.scheme_order = tuple(scheme_order or _default_scheme_order())
        if len(set(self.scheme_order)) != len(self.scheme_order) or set(self.scheme_order) != set(
            _default_scheme_order()
        ):
            raise ValueError("scheme_order must contain each implemented routing label exactly once")
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
    ) -> RoutingPolicyOutput:
        if base_query.shape != (TEXT_DIMENSION,):
            raise ValueError("routing requires one 384-dimensional fixed query representation")
        query = self.query_map(base_query)
        answer_state = self.answer_state_encoder(answers, answer_views)
        contextual = self.history_map.contextualize(base_history)
        logits = torch.stack(
            [
                self.routing_head(
                    query, answer_state, self.history_map.conditioned(base_history, contextual, initiator, receiver)
                )
                for initiator in range(self.n_agents)
                for receiver in range(self.n_agents)
            ]
        ).reshape(self.n_agents, self.n_agents, -1)
        mask = legal_label_mask(self.n_agents, self.scheme_order, device=logits.device)
        return RoutingPolicyOutput(logits, masked_softmax(logits, mask), self.scheme_order)

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
            output.probabilities, output.scheme_order, outgoing_capacity, incoming_capacity, session=session
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
) -> RoutingAction:
    """Sample Step 4's random-permutation, masked categorical action.

    Every free decision retains the exact differentiable masked log
    probability.  A capacity-forced zero stores a detached log probability of
    zero and is explicitly marked so alpha losses can exclude it.
    """

    order = tuple(scheme_order)
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
        structural = legal_schemes(initiator, receiver)
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
    probabilities: torch.Tensor, scheme_order: Sequence[Scheme]
) -> tuple[torch.Tensor, dict[Edge, Scheme]]:
    """Return log-odds beta and the deterministic best nonzero scheme."""

    order = tuple(scheme_order)
    n_agents = _validate_distribution(probabilities, order)
    zero_index = order.index(Scheme.ZERO)
    beta = probabilities.new_empty((n_agents, n_agents))
    best_scheme: dict[Edge, Scheme] = {}
    for initiator in range(n_agents):
        for receiver in range(n_agents):
            legal_nonzero = [scheme for scheme in legal_schemes(initiator, receiver) if scheme is not Scheme.ZERO]
            indices = torch.tensor([order.index(scheme) for scheme in legal_nonzero], device=probabilities.device)
            nonzero_probabilities = probabilities[initiator, receiver].index_select(0, indices)
            local_index = int(torch.argmax(nonzero_probabilities).item())
            scheme = legal_nonzero[local_index]
            best_scheme[(initiator, receiver)] = scheme
            zero_probability = probabilities[initiator, receiver, zero_index]
            beta[initiator, receiver] = torch.log(nonzero_probabilities[local_index]) - torch.log(zero_probability)
    return beta, best_scheme


def decode_capacity_feasible(
    probabilities: torch.Tensor,
    scheme_order: Sequence[Scheme],
    outgoing_capacity: Capacity,
    incoming_capacity: Capacity,
    *,
    session: int,
) -> RoutingAction:
    """Run the exact deterministic specification evaluation decoder."""

    order = tuple(scheme_order)
    beta, best_scheme = routing_beta(probabilities, order)
    selected = set(exact_b_matching(beta, outgoing_capacity, incoming_capacity))
    n_agents = probabilities.shape[0]
    matrix = {
        (initiator, receiver): best_scheme[(initiator, receiver)] if (initiator, receiver) in selected else Scheme.ZERO
        for initiator in range(n_agents)
        for receiver in range(n_agents)
    }
    objective_score = float(sum(beta[edge].detach().cpu() for edge in selected))
    return RoutingAction(
        session=session,
        matrix=matrix,
        decisions=(),
        permutation=(),
        mode=RoutingMode.INFERENCE,
        objective_score=objective_score,
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

    def forward(self, base_query: torch.Tensor, base_history: HistoryEncoding) -> WeightingOutput:
        if base_query.shape != (TEXT_DIMENSION,):
            raise ValueError("weighting requires one 384-dimensional fixed query representation")
        query = self.query_map(base_query)
        contextual = self.history_map.contextualize(base_history)
        logits = torch.stack(
            [
                self.reliability_head(query, self.history_map.conditioned(base_history, contextual, agent))
                for agent in range(self.n_agents)
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
