"""Offline contracts for frozen representations and the concrete neural architecture."""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
import random
from collections.abc import Sequence
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from torch.nn import functional as F

from examples.adaptive_multi_agent_collab.config import EncoderConfig, NetworkConfig
from examples.adaptive_multi_agent_collab.models.encoders import (
    EventIdentity,
    FixedBaseEncoders,
    HistoryEncoding,
    MiniLMTextEncoder,
    OfflineTextEncoder,
    answer_views,
    offline_encoder_configuration,
    serialize_history,
    serialize_query,
)
from examples.adaptive_multi_agent_collab.models.policies import (
    AnswerStateEncoder,
    RoutingPolicy,
    RoutingRolloutIdentity,
    RoutingSubstream,
    WeightingModel,
    _direct_candidate_mass_nll,
    decode_capacity_feasible,
    derive_routing_seed,
    exact_b_matching,
    legal_label_mask,
    legal_schemes,
    masked_softmax,
    routing_beta,
    routing_generator,
    sample_capacity_feasible,
)
from examples.adaptive_multi_agent_collab.schemas import (
    ConversationTag,
    HistoryRecord,
    QueryExample,
    RoutingMode,
    SCHEME_ORDER,
    Scheme,
)


def _small_network() -> NetworkConfig:
    return NetworkConfig(
        representation_dim=16, feedforward_dim=24, answer_byte_dim=8, transformer_layers=1, transformer_heads=4
    )


def _inputs(n_agents: int = 3):
    encoder = FixedBaseEncoders(n_agents, configuration=offline_encoder_configuration())
    query = encoder.encode_query("Choose the best answer: A or B?")
    history = encoder.encode_history(
        (
            HistoryRecord(
                session=1,
                event="revision-response",
                actor_id=0,
                tag=ConversationTag(1, 0, 1, Scheme.DEBATE),
                round_index=2,
                content="Answer A",
            ),
        )
    )
    return encoder, query, history


def _routing_probabilities(n_agents: int) -> torch.Tensor:
    logits = torch.arange(n_agents * n_agents * len(SCHEME_ORDER), dtype=torch.float32).reshape(
        n_agents, n_agents, len(SCHEME_ORDER)
    )
    return masked_softmax(logits / 10.0, legal_label_mask(n_agents))


def _brute_force_score(beta: Sequence[Sequence[float]], outgoing: Sequence[int], incoming: Sequence[int]) -> float:
    return _brute_force_solution(beta, outgoing, incoming)[0]


def _brute_force_solution(
    beta: Sequence[Sequence[float]], outgoing: Sequence[int], incoming: Sequence[int]
) -> tuple[float, tuple[int, ...]]:
    n_agents = len(beta)
    best = 0.0
    best_bits = (0,) * (n_agents * n_agents)
    for bits in itertools.product((0, 1), repeat=n_agents * n_agents):
        if any(sum(bits[j * n_agents + i] for i in range(n_agents)) > outgoing[j] for j in range(n_agents)):
            continue
        if any(sum(bits[j * n_agents + i] for j in range(n_agents)) > incoming[i] for i in range(n_agents)):
            continue
        value = sum(bits[j * n_agents + i] * beta[j][i] for j in range(n_agents) for i in range(n_agents))
        if value > best or (value == best and bits < best_bits):
            best, best_bits = value, bits
    return best, best_bits


def test_legal_scheme_sets_and_masks_cover_self_cross_and_single_agent() -> None:
    assert legal_schemes(0, 0) == (Scheme.ZERO, Scheme.SELF)
    assert legal_schemes(0, 1) == (
        Scheme.ZERO,
        Scheme.DEBATE,
        Scheme.JUDGE,
        Scheme.TEACHER,
        Scheme.REVIEWER,
    )

    singleton = legal_label_mask(1)
    assert singleton.shape == (1, 1, len(SCHEME_ORDER))
    assert singleton[0, 0].tolist() == [True, True, False, False, False, False]

    mask = legal_label_mask(2)
    assert mask[0, 1].tolist() == [True, False, True, True, True, True]
    assert mask[1, 1].tolist() == [True, True, False, False, False, False]


def test_stochastic_sampler_respects_capacities_and_marks_forced_zeros() -> None:
    probabilities = torch.zeros((2, 2, len(SCHEME_ORDER)))
    zero, self_index = SCHEME_ORDER.index(Scheme.ZERO), SCHEME_ORDER.index(Scheme.SELF)
    debate = SCHEME_ORDER.index(Scheme.DEBATE)
    probabilities[0, 0, self_index] = 1.0
    probabilities[0, 1, debate] = 1.0
    probabilities[1, 0, debate] = 1.0
    probabilities[1, 1, self_index] = 1.0
    permutation = ((0, 0), (0, 1), (1, 0), (1, 1))

    action = sample_capacity_feasible(
        probabilities,
        SCHEME_ORDER,
        outgoing_capacity=(1, 1),
        incoming_capacity=(1, 1),
        session=1,
        permutation=permutation,
    )

    assert action.mode is RoutingMode.TRAIN
    assert action.matrix == {
        (0, 0): Scheme.SELF,
        (0, 1): Scheme.ZERO,
        (1, 0): Scheme.ZERO,
        (1, 1): Scheme.SELF,
    }
    assert [decision.forced_zero for decision in action.decisions] == [False, True, True, False]
    for decision in action.decisions:
        if decision.forced_zero:
            assert decision.legal_schemes == (Scheme.ZERO,)
            assert decision.probability == 1.0
            assert decision.log_probability.item() == 0.0
            assert decision.masked_probabilities[zero] == 1.0
        assert min((*decision.remaining_out, *decision.remaining_in)) >= 0
    assert sum(scheme.is_active for scheme in action.matrix.values()) == 2


def test_freely_sampled_zero_is_not_misclassified_as_capacity_forced() -> None:
    probabilities = torch.tensor([[[0.75, 0.25, 0.0, 0.0, 0.0, 0.0]]], requires_grad=True)
    action = sample_capacity_feasible(
        probabilities,
        SCHEME_ORDER,
        outgoing_capacity=1,
        incoming_capacity=1,
        session=1,
        permutation=((0, 0),),
        generator=torch.Generator().manual_seed(0),
    )
    decision = action.decisions[0]

    assert decision.scheme is Scheme.ZERO
    assert decision.free and not decision.forced_zero
    assert decision.probability == pytest.approx(0.75)
    assert decision.log_probability.item() == pytest.approx(math.log(0.75))
    assert not decision.log_probability.requires_grad


def test_sampler_retains_exact_masked_probabilities_log_probability_and_permutation() -> None:
    logits = torch.arange(2 * 2 * len(SCHEME_ORDER), dtype=torch.float32).reshape(2, 2, len(SCHEME_ORDER))
    logits.requires_grad_()
    probabilities = masked_softmax(logits / 7.0, legal_label_mask(2))
    generator_seed = 1234
    first = sample_capacity_feasible(
        probabilities,
        SCHEME_ORDER,
        2,
        2,
        session=2,
        generator=torch.Generator().manual_seed(generator_seed),
    )
    second = sample_capacity_feasible(
        probabilities,
        SCHEME_ORDER,
        2,
        2,
        session=2,
        generator=torch.Generator().manual_seed(generator_seed),
    )

    assert first.permutation == second.permutation
    assert first.matrix == second.matrix
    assert len(first.decisions) == 4
    for decision in first.decisions:
        assert decision.free
        index = SCHEME_ORDER.index(decision.scheme)
        expected = decision.masked_probabilities[index]
        assert sum(decision.masked_probabilities) == pytest.approx(1.0)
        assert decision.probability == pytest.approx(expected)
        assert decision.log_probability.item() == pytest.approx(math.log(expected))
        assert tuple(value > 0.0 for value in decision.masked_probabilities) == decision.legal_mask
        assert decision.base_probabilities == pytest.approx(
            probabilities[decision.initiator_id, decision.receiver_id].detach().tolist()
        )


@pytest.mark.parametrize("n_agents", [1, 2, 3])
def test_exact_b_matching_matches_brute_force_on_small_random_cases(n_agents: int) -> None:
    randomizer = random.Random(100 + n_agents)
    for _ in range(12):
        beta = [[randomizer.randint(-4, 8) / 4.0 for _ in range(n_agents)] for _ in range(n_agents)]
        outgoing = tuple(randomizer.randrange(n_agents + 1) for _ in range(n_agents))
        incoming = tuple(randomizer.randrange(n_agents + 1) for _ in range(n_agents))
        selected = exact_b_matching(beta, outgoing, incoming)
        score = sum(beta[j][i] for j, i in selected)
        expected_score, expected_bits = _brute_force_solution(beta, outgoing, incoming)
        expected = tuple((j, i) for j in range(n_agents) for i in range(n_agents) if expected_bits[j * n_agents + i])

        assert score == pytest.approx(expected_score)
        assert selected == expected
        assert all(beta[j][i] > 0.0 for j, i in selected)
        assert all(sum(j == sender for j, _ in selected) <= outgoing[sender] for sender in range(n_agents))
        assert all(sum(i == receiver for _, i in selected) <= incoming[receiver] for receiver in range(n_agents))


def test_exact_b_matching_excludes_nonpositive_edges_and_resolves_ties_stably() -> None:
    beta = [[0.0, -1.0], [2.0, 0.0]]
    assert exact_b_matching(beta, 2, 2) == ((1, 0),)

    tied = [[1.0, 1.0], [1.0, 1.0]]
    first = exact_b_matching(tied, 1, 1)
    assert first == exact_b_matching(tied, 1, 1)
    assert first == ((0, 1), (1, 0))
    assert sum(tied[j][i] for j, i in first) == 2.0


def test_exact_b_matching_secondary_tie_break_never_changes_primary_optimum() -> None:
    beta = [[100.0, 49.0], [50.0, 0.0]]
    selected = exact_b_matching(beta, 1, 1)
    assert selected == ((0, 0),)
    assert sum(beta[j][i] for j, i in selected) == _brute_force_score(beta, (1, 1), (1, 1))


def test_deterministic_decoder_uses_positive_beta_best_scheme_and_exact_objective() -> None:
    values = torch.arange(24, dtype=torch.float32).reshape(2, 2, 6) / 10.0
    beta, best_schemes = routing_beta(values, SCHEME_ORDER)
    action = decode_capacity_feasible(values, SCHEME_ORDER, 1, 1, session=3)
    local_selected = set(exact_b_matching(beta, 1, 1))

    assert action.mode is RoutingMode.INFERENCE
    assert not action.decisions and not action.permutation
    assert set(action.matrix) == {(j, i) for j in range(2) for i in range(2)}
    assert set(action.selected_edges) == local_selected
    assert all(beta[edge].item() > 0.0 for edge in action.selected_edges)
    assert all(action.matrix[edge] is best_schemes[edge] for edge in action.selected_edges)
    assert all(action.matrix[edge] is Scheme.ZERO for edge in set(action.matrix) - local_selected)
    outgoing_degree = tuple(
        sum(edge[0] == sender and label is not Scheme.ZERO for edge, label in action.matrix.items())
        for sender in range(2)
    )
    incoming_degree = tuple(
        sum(edge[1] == receiver and label is not Scheme.ZERO for edge, label in action.matrix.items())
        for receiver in range(2)
    )
    assert all(degree <= 1 for degree in (*outgoing_degree, *incoming_degree))
    expected = sum(beta[edge].item() for edge in action.selected_edges)
    assert action.objective_score == pytest.approx(expected)
    assert expected == pytest.approx(_brute_force_score(beta.tolist(), outgoing=(1, 1), incoming=(1, 1)))


def test_weighted_vote_scores_are_the_direct_candidate_probabilities() -> None:
    model = WeightingModel(3, 4, configuration=_small_network())
    weights = torch.tensor((0.2, 0.3, 0.5))
    vote = model.vote(weights, ("A", "B", "B"), candidate_order=("A", "B", "C", "D"))

    assert vote.candidates == ("A", "B")
    assert vote.scores.tolist() == pytest.approx((0.2, 0.8))
    assert bool((vote.probabilities >= 0).all())
    assert vote.scores.sum().item() == pytest.approx(1.0)
    assert vote.probabilities is vote.scores
    assert vote.probabilities.tolist() == pytest.approx((0.2, 0.8))
    assert vote.probability("B").item() == pytest.approx(0.3 + 0.5)
    assert vote.probabilities.sum().item() == pytest.approx(1.0)
    assert vote.prediction == "B"


def test_candidate_order_controls_only_deterministic_weighted_tie_breaking() -> None:
    model = WeightingModel(2, 4, configuration=_small_network())
    weights = torch.tensor((0.5, 0.5))

    first = model.vote(weights, ("B", "A"), candidate_order=("A", "B", "C"))
    second = model.vote(weights, ("B", "A"), candidate_order=("B", "A", "C"))
    fallback = model.vote(weights, ("B", "A"))
    assert first.prediction == "A"
    assert second.prediction == "B"
    assert fallback.prediction == "A"
    assert first.score("A") == first.score("B")
    assert first.probability("A") == first.probability("B")
    with pytest.raises(ValueError, match="candidate_order"):
        model.vote(weights, ("B", "A"), candidate_order=("A", "C"))


def test_stable_direct_mass_loss_matches_plain_nll_and_handles_multiple_correct_agents() -> None:
    logits = torch.log(torch.tensor((0.2, 0.3, 0.5), dtype=torch.float64))
    weights = torch.softmax(logits, dim=0)

    one_correct = _direct_candidate_mass_nll(logits, ("A", "B", "B"), "A")
    multiple_correct = _direct_candidate_mass_nll(logits, ("A", "A", "B"), "A")

    assert one_correct.item() == pytest.approx(-math.log(weights[0].item()))
    assert multiple_correct.item() == pytest.approx(-math.log((weights[0] + weights[1]).item()))


def test_stable_direct_mass_loss_is_finite_for_extreme_informative_logits() -> None:
    logits = torch.tensor((-1_000.0, 1_000.0, 999.0), requires_grad=True)

    loss = _direct_candidate_mass_nll(logits, ("A", "B", "B"), "A")
    loss.backward()

    assert torch.isfinite(loss)
    assert logits.grad is not None and bool(torch.isfinite(logits.grad).all())


def test_query_and_answer_views_use_only_task_input_and_ordered_option_text() -> None:
    query = SimpleNamespace(
        query="Question?",
        candidates=("B", "A"),
        option_texts={"A": "first", "B": "second"},
        gold_answer="A",
        metadata={"secret": 3},
    )
    payload = json.loads(serialize_query(query))
    assert payload == {"query": "Question?", "options": [["B", "second"], ["A", "first"]]}
    assert answer_views(query, ("A", "B")) == (
        '{"identifier":"A","text":"first"}',
        '{"identifier":"B","text":"second"}',
    )
    assert answer_views(SimpleNamespace(query="Open question", options=None, open_answer=True), ("canonical",)) == (
        "canonical",
    )
    finite = QueryExample("finite", "Question?", ("B", "A"), option_texts=("second", "first"))
    assert json.loads(serialize_query(finite)) == payload
    assert answer_views(finite, ("A", "B")) == answer_views(query, ("A", "B"))
    query.gold_answer = "B"
    query.metadata = {"secret": 19}
    assert json.loads(serialize_query(query)) == payload
    assert json.loads(serialize_query(SimpleNamespace(query="Open", candidates=("yes", "no")))) == {"query": "Open"}


def test_fixed_query_and_event_caches_reuse_prefixes_without_caching_trainable_features() -> None:
    backend = OfflineTextEncoder()
    encoder = FixedBaseEncoders(3, configuration=offline_encoder_configuration(), text_encoder=backend)
    query = QueryExample("one", "Question?", ("A", "B"), "A")
    original = encoder.encode_query(query)
    torch.testing.assert_close(encoder.encode_query(dataclasses.replace(query, gold_answer="B")), original)
    assert len(backend.calls) == 1
    cache = encoder.new_history_cache()
    empty = cache.snapshot()
    assert empty.vectors.shape == (0, encoder.history_dimension)
    cache.append(HistoryRecord(1, "initiation", actor_id=0, content="First", history_index=1))
    first = cache.snapshot()
    cache.snapshot()
    cache.append(HistoryRecord(1, "role-response", actor_id=1, content="Second", history_index=2))
    whole = cache.snapshot()
    assert len(backend.calls) == 3
    torch.testing.assert_close(first.vectors[0], whole.vectors[0])
    assert first.vectors.shape[0] == 1
    assert whole.history_indices == (1, 2)
    assert not whole.vectors.requires_grad
    with pytest.raises(ValueError, match="monotone"):
        cache.append(HistoryRecord(1, "initiation", history_index=4))
    # Snapshot mutation cannot corrupt the retained fixed cache.
    first.vectors.zero_()
    assert not torch.equal(cache.snapshot().vectors[0], first.vectors[0])


def test_event_structure_is_observable_one_hot_and_supervision_invariant() -> None:
    encoder = FixedBaseEncoders(3, configuration=offline_encoder_configuration())
    event = {
        "event": "role-response",
        "session": 2,
        "actor_id": 1,
        "recipient_id": 0,
        "tag": ConversationTag(2, 0, 1, Scheme.TEACHER),
        "content": "Advice",
        "status": "continue",
        "usage": {"input_tokens": 12, "output_tokens": 7, "model_calls": 1},
        "normalized_cost": 0.019,
    }
    encoded = encoder.encode_history([event])
    secret = {**event, "gold_answer": "secret", "reward": 99, "credit": 12, "correctness": True}
    torch.testing.assert_close(encoded.vectors, encoder.encode_history([secret]).vectors)
    assert serialize_history([event]) == serialize_history([secret])
    changed = encoder.encode_history([{**event, "status": "complete"}])
    torch.testing.assert_close(encoded.vectors[:, :384], changed.vectors[:, :384])
    assert not torch.equal(encoded.vectors[:, 384:], changed.vectors[:, 384:])
    assert encoded.identities[0].routing_relation(0, 1) == 0
    assert encoded.identities[0].routing_relation(1, 0) == 1
    assert encoded.identities[0].weighting_relation(1) == 0
    assert encoded.identities[0].weighting_relation(0) == 1
    assert encoded.identities[0].weighting_relation(2) == 3


def test_history_snapshot_cannot_truncate_the_complete_event_sequence() -> None:
    encoder = FixedBaseEncoders(2, configuration=offline_encoder_configuration())
    cache = encoder.new_history_cache()
    cache.extend((HistoryRecord(1, "initiation", content="first"), HistoryRecord(1, "role-response", content="second")))
    assert cache.snapshot().history_indices == (1, 2)
    assert cache.snapshot().vectors.shape == (2, encoder.history_dimension)
    with pytest.raises(TypeError, match="recent_event_limit"):
        offline_encoder_configuration(recent_event_limit=1)


class _Tokenizer:
    cls_token_id = 0
    sep_token_id = 99

    def encode(self, text, **kwargs):
        assert kwargs == {"add_special_tokens": False, "truncation": False}
        return list(range(1, len(text) + 1))

    def num_special_tokens_to_add(self, *, pair):
        assert pair is False
        return 2


class _Backbone(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = torch.nn.Embedding(100, 384)
        self.inputs = []
        self.grad_modes = []

    def forward(self, input_ids, attention_mask):
        self.inputs.append(input_ids.clone())
        self.grad_modes.append(torch.is_grad_enabled())
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


def test_minilm_token_aligned_chunks_cover_every_token_and_match_masked_mean_pooling() -> None:
    torch.manual_seed(12)
    model = _Backbone()
    encoder = MiniLMTextEncoder(EncoderConfig(max_tokens=6), tokenizer=_Tokenizer(), model=model)
    assert encoder.encode("").shape == (384,)
    assert model.inputs == []
    result = encoder.encode("abcdefghij")
    assert [value[0].tolist() for value in model.inputs] == [[0, 1, 2, 3, 4, 99], [0, 5, 6, 7, 8, 99], [0, 9, 10, 99]]
    expected = F.normalize(
        torch.stack([F.normalize(model.embedding(value).mean(dim=1).squeeze(0), dim=0) for value in model.inputs]).mean(
            dim=0
        ),
        dim=0,
    )
    torch.testing.assert_close(result, expected)
    torch.testing.assert_close(torch.linalg.vector_norm(result), torch.tensor(1.0))
    assert model.training is False and model.grad_modes == [False, False, False]
    assert all(not parameter.requires_grad for parameter in model.parameters())
    assert not result.requires_grad


def test_real_bert_tokenizer_chunk_framing_matches_native_without_retokenization():
    from transformers import BertTokenizer

    tokenizer = BertTokenizer(
        vocab={
            "[PAD]": 0,
            "[UNK]": 1,
            "[CLS]": 2,
            "[SEP]": 3,
            "[MASK]": 4,
            "synthetic": 5,
            "question": 6,
            ".": 7,
        }
    )
    model = _Backbone()
    encoder = MiniLMTextEncoder(EncoderConfig(max_tokens=6), tokenizer=tokenizer, model=model)
    text = "Synthetic [CLS] question. Synthetic question."
    content = tokenizer.encode(text, add_special_tokens=False, truncation=False)
    assert tokenizer.encode(text, add_special_tokens=True, truncation=False) == [2, *content, 3]
    encoded = encoder.encode(text)
    assert [token for batch in model.inputs for token in batch[0, 1:-1].tolist()] == content
    assert all(batch[0, 0] == 2 and batch[0, -1] == 3 and batch.shape[-1] <= 6 for batch in model.inputs)
    assert encoded.shape == (384,) and encoded.dtype == torch.float32


def test_production_encoder_is_lazy_pinned_and_never_downloads() -> None:
    encoder = FixedBaseEncoders(2)
    assert encoder.metadata()["encoder_type"] == "minilm"
    assert isinstance(encoder.text_encoder, MiniLMTextEncoder)
    assert encoder.text_encoder.model is None
    with pytest.raises(ValueError, match="pinned"):
        encoder.encode_query("Question")
    configuration = EncoderConfig(model_revision="a" * 40)
    model = _Backbone()
    with (
        patch("transformers.AutoTokenizer.from_pretrained", return_value=_Tokenizer()) as tokenizer_loader,
        patch("transformers.AutoModel.from_pretrained", return_value=model) as model_loader,
    ):
        MiniLMTextEncoder(configuration).encode("Question")
    for loader in (tokenizer_loader, model_loader):
        assert loader.call_args.kwargs == {"revision": "a" * 40, "local_files_only": True}
    with pytest.raises(ValueError, match="identity"):
        FixedBaseEncoders(2, text_encoder=OfflineTextEncoder())
    with pytest.raises(ValueError, match="configuration"):
        FixedBaseEncoders(2, text_encoder=MiniLMTextEncoder(EncoderConfig(max_tokens=128)))


def test_primary_dimensions_normalization_and_interaction_heads_are_exact() -> None:
    encoder = FixedBaseEncoders(3, configuration=offline_encoder_configuration())
    alpha = RoutingPolicy(3, encoder.history_dimension)
    weighting = WeightingModel(3, encoder.history_dimension)
    assert alpha.routing_head.hidden.in_features == 768
    assert alpha.routing_head.hidden.out_features == 256
    assert weighting.reliability_head.hidden.in_features == 512
    assert weighting.reliability_head.hidden.out_features == 128
    assert alpha.answer_state_encoder.output_projection.first.in_features == 3 * (64 + 3)
    assert alpha.history_map.readout.first.in_features == 384
    assert isinstance(alpha.history_map.readout.input_norm, torch.nn.Identity)
    assert alpha.answer_state_encoder.output_projection.output_norm.elementwise_affine
    assert not alpha.answer_state_encoder.output_projection.input_norm.elementwise_affine
    assert all(module.p == 0 for module in alpha.modules() if isinstance(module, torch.nn.Dropout))
    assert {id(parameter) for parameter in alpha.parameters()}.isdisjoint(
        id(parameter) for parameter in weighting.parameters()
    )


def test_answer_encoder_keeps_exact_agreement_and_option_ownership() -> None:
    model = AnswerStateEncoder(3, configuration=_small_network())
    answers = ("A", "B", "A")
    feature = model.ordered_features(answers, ("A: one", "B: two", "A: one"))
    blocks = feature.reshape(3, 11)
    torch.testing.assert_close(blocks[:, 8:], torch.tensor([[1.0, 0.0, 1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 1.0]]))
    reordered = model.ordered_features(("B", "A", "A"), ("B: two", "A: one", "A: one"))
    assert not torch.equal(feature, reordered)
    changed_text = model.ordered_features(answers, ("A: changed", "B: two", "A: changed"))
    assert not torch.equal(feature, changed_text)
    torch.testing.assert_close(blocks[:, 8:], changed_text.reshape(3, 11)[:, 8:])
    assert model(answers).shape == (16,)


def test_history_transformers_run_once_per_forward_and_recontextualize_complete_prefix() -> None:
    encoder, query, history = _inputs()
    alpha = RoutingPolicy(3, encoder.history_dimension, configuration=_small_network())
    weighting = WeightingModel(3, encoder.history_dimension, configuration=_small_network())
    calls = {"alpha": 0, "weighting": 0}
    assert alpha.history_map.input_projection.bias is None
    assert weighting.history_map.input_projection.bias is None

    def called(name):
        def record(*args):
            calls[name] += 1

        return record

    alpha.history_map.transformer.register_forward_hook(called("alpha"))
    weighting.history_map.transformer.register_forward_hook(called("weighting"))
    first = alpha(query, history, ("A", "B", "A"))
    weighting(query, history)
    assert calls == {"alpha": 1, "weighting": 1}
    assert torch.equal(first.values, torch.zeros_like(first.values))
    mask = legal_label_mask(3)
    for j, i in itertools.product(range(3), repeat=2):
        legal = mask[j, i]
        torch.testing.assert_close(
            first.probabilities[j, i, legal], torch.full_like(first.probabilities[j, i, legal], 1.0 / legal.sum())
        )
        assert torch.count_nonzero(first.probabilities[j, i, ~legal]) == 0
    old_context = alpha.history_map.contextualize(history)[1]
    cache = encoder.new_history_cache()
    cache.extend(
        (HistoryRecord(1, "revision-response", content="First"), HistoryRecord(1, "role-response", content="Second"))
    )
    newer = cache.snapshot()
    prefix = HistoryEncoding(newer.vectors[:1], newer.identities[:1], newer.history_indices[:1])
    before = alpha.history_map.contextualize(prefix)[1][0]
    after = alpha.history_map.contextualize(newer)[1][0]
    assert not torch.allclose(before, after)
    assert old_context.shape == (1, 16)


def test_empty_history_uses_cls_and_exact_zero_context_for_each_condition() -> None:
    encoder, query, _ = _inputs()
    history = encoder.encode_history(())
    for model in (
        RoutingPolicy(3, encoder.history_dimension, configuration=_small_network()),
        WeightingModel(3, encoder.history_dimension, configuration=_small_network()),
    ):
        inputs = []
        model.history_map.readout.register_forward_pre_hook(lambda module, args: inputs.append(args[0].detach()))
        if isinstance(model, RoutingPolicy):
            model(query, history, ("A", "B", "A"))
        else:
            result = model(query, history)
            torch.testing.assert_close(result.weights, torch.full((3,), 1 / 3))
        assert inputs
        assert all(torch.count_nonzero(value[-16:]) == 0 for value in inputs)
        assert any(torch.count_nonzero(value[16:32]) > 0 for value in inputs)


def test_directional_relation_attention_and_agent_history_are_distinct() -> None:
    encoder, _, history = _inputs()
    alpha = RoutingPolicy(3, encoder.history_dimension, configuration=_small_network())
    contextual = alpha.history_map.contextualize(history)
    forward = alpha.history_map.conditioned(history, contextual, 0, 1)
    reverse = alpha.history_map.conditioned(history, contextual, 1, 0)
    assert not torch.allclose(forward, reverse)
    weighting = WeightingModel(3, encoder.history_dimension, configuration=_small_network())
    contextual_w = weighting.history_map.contextualize(history)
    assert not torch.allclose(
        weighting.history_map.conditioned(history, contextual_w, 0),
        weighting.history_map.conditioned(history, contextual_w, 1),
    )


def test_zero_output_initialization_stages_gradients_then_trains_every_component() -> None:
    torch.manual_seed(42)
    encoder, query, history = _inputs()
    alpha = RoutingPolicy(3, encoder.history_dimension, configuration=_small_network())
    weighting = WeightingModel(3, encoder.history_dimension, configuration=_small_network())
    for model, upstream, loss_fn, output in (
        (
            alpha,
            (alpha.query_map, alpha.answer_state_encoder, alpha.history_map),
            lambda: 0.5 * (alpha(query, history, ("A", "B", "A")).value((0, 1), Scheme.DEBATE) - 1.0).square(),
            alpha.routing_head.output,
        ),
        (
            weighting,
            (weighting.query_map, weighting.history_map),
            lambda: weighting.session_loss(query, history, ("A", "B", "B"), "A"),
            weighting.reliability_head.output,
        ),
    ):
        optimizer = torch.optim.SGD(model.parameters(), lr=0.05)
        loss_fn().backward()
        assert torch.count_nonzero(output.weight.grad) > 0
        assert all(
            parameter.grad is None or torch.count_nonzero(parameter.grad) == 0
            for group in upstream
            for parameter in group.parameters()
        )
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        loss_fn().backward()
        assert all(
            any(
                parameter.grad is not None and torch.count_nonzero(parameter.grad) > 0
                for parameter in group.parameters()
            )
            for group in upstream
        )
    assert encoder.text_encoder.encoder_type == "offline_fixture"


def test_identifier_local_routing_substreams_and_descriptors_are_reproducible() -> None:
    identity = RoutingRolloutIdentity(31, "query-one", 4)
    first = identity.for_session(2)
    assert first.seed == derive_routing_seed(31, "query-one", 4, 2)
    assert RoutingSubstream.from_dict(first.to_dict()) == first
    assert first.rollout_identity == identity
    assert identity.for_session(3).seed != first.seed
    assert "rollout_id" not in first.to_dict()
    seed, generator = routing_generator(identity, 2)
    assert seed == first.seed
    expected = torch.rand(5, generator=generator)
    torch.manual_seed(982)
    torch.rand(25)
    torch.testing.assert_close(torch.rand(5, generator=first.generator()), expected)
    with pytest.raises(ValueError, match="seed"):
        RoutingSubstream.from_dict({**first.to_dict(), "seed": first.seed + 1})


def test_weighting_loss_skips_noninformative_and_uses_direct_realized_mass() -> None:
    encoder, query, history = _inputs()
    model = WeightingModel(3, encoder.history_dimension, configuration=_small_network())
    assert model.session_loss(query, history, ("A", "A", "A"), "A") is None
    assert model.session_loss(query, history, ("B", "C", "C"), "A") is None
    loss = model.session_loss(query, history, ("A", "B", "A"), "A")
    torch.testing.assert_close(loss, -torch.log(torch.tensor(2 / 3)))


def test_heads_expose_the_prescribed_products_and_weighting_absolute_difference() -> None:
    encoder, _, _ = _inputs()
    alpha = RoutingPolicy(3, encoder.history_dimension, configuration=_small_network())
    weighting = WeightingModel(3, encoder.history_dimension, configuration=_small_network())
    query, answers, history = torch.randn(3, 16)
    route = alpha.routing_head.interaction_features(query, answers, history).reshape(6, 16)
    torch.testing.assert_close(route[3], route[0] * route[1])
    torch.testing.assert_close(route[4], route[0] * route[2])
    torch.testing.assert_close(route[5], route[1] * route[2])
    weight = weighting.reliability_head.interaction_features(query, history).reshape(4, 16)
    torch.testing.assert_close(weight[2], weight[0] * weight[1])
    torch.testing.assert_close(weight[3], torch.abs(weight[0] - weight[1]))


def test_relation_biases_condition_attention_before_readout() -> None:
    encoder = FixedBaseEncoders(3, configuration=offline_encoder_configuration())
    history = encoder.encode_history(
        (
            HistoryRecord(
                1,
                "role-response",
                actor_id=1,
                recipient_id=0,
                tag=ConversationTag(1, 0, 1, Scheme.DEBATE),
                content="first",
            ),
            HistoryRecord(
                1,
                "role-response",
                actor_id=0,
                recipient_id=1,
                tag=ConversationTag(1, 1, 0, Scheme.DEBATE),
                content="second",
            ),
        )
    )
    alpha = RoutingPolicy(3, encoder.history_dimension, configuration=_small_network())
    mapping = alpha.history_map
    with torch.no_grad():
        mapping.attention_query.weight.zero_()
        mapping.attention_key.weight.zero_()
        mapping.relation_bias.copy_(torch.tensor([2.0, -1.0, 0.0, 0.0, 0.0]))
    contextual = mapping.contextualize(history)
    contexts = []
    mapping.context_norm.register_forward_pre_hook(lambda module, args: contexts.append(args[0].detach()))
    mapping.conditioned(history, contextual, 0, 1)
    expected = torch.softmax(torch.tensor([2.0, -1.0]), dim=0) @ mapping.attention_value(contextual[1])
    torch.testing.assert_close(contexts[0], expected)
    mapping.conditioned(history, contextual, 1, 0)
    expected_reverse = torch.softmax(torch.tensor([-1.0, 2.0]), dim=0) @ mapping.attention_value(contextual[1])
    torch.testing.assert_close(contexts[1], expected_reverse)
    assert not torch.allclose(contexts[0], contexts[1])


def test_history_rejects_padding_instead_of_attending_to_fake_events() -> None:
    identity = EventIdentity(None, None, None, None, ())
    with pytest.raises(ValueError, match="padding"):
        HistoryEncoding(torch.zeros(1, 400), (identity,), (0,))
    with pytest.raises(ValueError, match="one identity"):
        HistoryEncoding(torch.zeros(3, 400), (identity,), (1,))


def test_training_action_persists_inverse_positions_and_exact_generator_state() -> None:
    encoder, query, history = _inputs()
    alpha = RoutingPolicy(3, encoder.history_dimension, configuration=_small_network())
    generator = torch.Generator().manual_seed(81)
    action = alpha.route_training(query, history, ("A", "B", "A"), 2, 2, session=1, generator=generator)
    assert action.inverse_edge_positions == {edge: index for index, edge in enumerate(action.permutation)}
    assert action.routing_generator_state is not None
    restored = torch.Generator().set_state(torch.tensor(list(action.routing_generator_state), dtype=torch.uint8))
    assert torch.equal(torch.rand(20, generator=restored), torch.rand(20, generator=generator))


def test_finite_self_describing_labels_have_identifier_and_text_views() -> None:
    from examples.adaptive_multi_agent_collab.schemas import QueryExample

    example = QueryExample("q", "Question?", ("yes", "no"), "yes")
    assert answer_views(example, ("yes", "no")) == (
        '{"identifier":"yes","text":"yes"}',
        '{"identifier":"no","text":"no"}',
    )
    opened = QueryExample("q", "Question?", (), "yes", open_answer=True)
    assert answer_views(opened, ("yes", "no")) == ("yes", "no")


def test_appendix_history_layers_initialize_independently_and_reproduce_with_seed():
    from examples.adaptive_multi_agent_collab.models.policies import ConditionedHistory
    from examples.adaptive_multi_agent_collab.config import NetworkConfig

    config = NetworkConfig()
    torch.manual_seed(271)
    first = ConditionedHistory(3, 457, config, routing=True)
    torch.manual_seed(271)
    second = ConditionedHistory(3, 457, config, routing=True)
    layers = first.transformer.layers
    assert len(layers) == 2
    assert not torch.equal(layers[0].self_attn.in_proj_weight, layers[1].self_attn.in_proj_weight)
    assert layers[0].self_attn.in_proj_weight.data_ptr() != layers[1].self_attn.in_proj_weight.data_ptr()
    assert all(torch.equal(value, second.state_dict()[key]) for key, value in first.state_dict().items())
    assert first.transformer.norm is None
    assert all(layer.norm_first and layer.self_attn.num_heads == 4 and layer.self_attn.dropout == 0 for layer in layers)
    assert first.input_projection.bias is None


def test_structural_features_reject_out_of_range_explicit_involved_identity():
    encoder = FixedBaseEncoders(3, configuration=offline_encoder_configuration())
    with pytest.raises(ValueError, match="involved-agent"):
        encoder.encode_history(({"session": 1, "event": "role-response", "involved_agent_ids": (7,)},))
