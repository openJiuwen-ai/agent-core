"""Semantic transfer and independent new blocks under the mixed-Q architecture."""
from __future__ import annotations

import copy

import pytest
import torch

from examples.adaptive_multi_agent_collab.config import NetworkConfig
from examples.adaptive_multi_agent_collab.models.encoders import FixedBaseEncoders, offline_encoder_configuration
from examples.adaptive_multi_agent_collab.models.policies import (
    RoutingPolicy, WeightingModel, decode_capacity_feasible,
    expand_models, models_from_expansion_metadata, routing_beta,
)
from examples.adaptive_multi_agent_collab.schemas import ConversationTag, HistoryRecord, SCHEME_ORDER, Scheme


def networks():
    torch.manual_seed(71)
    configuration = NetworkConfig(representation_dim=16, feedforward_dim=24, answer_byte_dim=8,
                                  transformer_layers=1, transformer_heads=4)
    encoder = FixedBaseEncoders(2, configuration=offline_encoder_configuration())
    alpha = RoutingPolicy(2, encoder.history_dimension, configuration=configuration)
    weights = WeightingModel(2, encoder.history_dimension, configuration=configuration)
    with torch.no_grad():
        alpha.routing_head.output.weight.normal_(std=.1)
        alpha.routing_head.output.bias.normal_(std=.1)
        weights.reliability_head.output.weight.normal_(std=.1)
    return encoder, alpha, weights


def events():
    return [HistoryRecord(1, "revision-response", actor_id=0, recipient_id=1,
                          tag=ConversationTag(1, 0, 1, Scheme.DEBATE), answer="yes", explanation="evidence")]


def test_initial_answer_block_matches_prescribed_single_residual_exactly():
    _, alpha, _ = networks()
    encoder = alpha.answer_state_encoder
    answers = ("yes", "no")
    expected = encoder.output_projection(encoder.ordered_features(answers))
    torch.testing.assert_close(encoder(answers), expected, rtol=0, atol=0)


def test_agent_transfer_uses_semantics_not_positional_padding_and_preserves_parent():
    encoder, alpha, weights = networks()
    extended = expand_models(alpha, weights, encoder, agent_ids=(0, 1, 2), scheme_order=SCHEME_ORDER)
    parent_event = encoder.encode_history(events())
    expanded_event = extended.encoders.encode_history(events())
    assert encoder.semantic_coordinates.index("actor_id:null") != extended.encoders.semantic_coordinates.index("actor_id:null")
    for history in (extended.alpha.history_map, extended.weighting.history_map):
        assert all(torch.count_nonzero(block) == 0 for block in history.input_projection.added)
    torch.testing.assert_close(
        alpha.history_map.input_projection(parent_event.vectors),
        extended.alpha.history_map.input_projection(expanded_event.vectors), rtol=0, atol=0,
    )
    query = encoder.encode_query("synthetic")
    before = alpha(query, parent_event, ("yes", "no"))
    after = extended.alpha(query, expanded_event, ("yes", "no"), active_agent_ids=(0, 1), active_scheme_order=SCHEME_ORDER)
    torch.testing.assert_close(before.values, after.values, rtol=0, atol=0)
    torch.testing.assert_close(weights(query, parent_event).logits,
                               extended.weighting(query, expanded_event, active_agent_ids=(0, 1)).logits, rtol=0, atol=0)
    assert extended.alpha.answer_state_encoder.added_projections[0].first.in_features == 8 + 2 * 2 + 1
    for name in ("sender_query", "receiver_query"):
        table = getattr(extended.alpha.history_map, name)
        torch.testing.assert_close(table.added[0][:, 0], table.weight.mean(dim=1), rtol=0, atol=0)
    table = extended.weighting.history_map.agent_query
    torch.testing.assert_close(table.added[0][:, 0], table.weight.mean(dim=1), rtol=0, atol=0)
    assert extended.alpha.history_map.sender_query.added[0] is not extended.alpha.history_map.receiver_query.added[0]


def test_repeated_additions_keep_separate_answer_normalizations_and_strict_reload():
    encoder, alpha, weights = networks()
    first = expand_models(alpha, weights, encoder, agent_ids=(0, 1, 2), scheme_order=SCHEME_ORDER)
    second = expand_models(first.alpha, first.weighting, first.encoders, agent_ids=(0, 1, 2, 3), scheme_order=SCHEME_ORDER)
    answer = second.alpha.answer_state_encoder
    assert [block.first.in_features for block in answer.added_projections] == [13, 15]
    assert answer.output_projection.first.in_features == 2 * (8 + 2)
    rebuilt = models_from_expansion_metadata(second.metadata, configuration=alpha.configuration,
                                             encoder_configuration=encoder.configuration)
    rebuilt.alpha.load_state_dict(second.alpha.state_dict(), strict=True)
    rebuilt.weighting.load_state_dict(second.weighting.state_dict(), strict=True)
    query = encoder.encode_query("fixture")
    history = second.encoders.encode_history(events())
    torch.testing.assert_close(second.alpha(query, history, ("yes", "no", "yes", "no")).values,
                               rebuilt.alpha(query, history, ("yes", "no", "yes", "no")).values, rtol=0, atol=0)
    assert second.metadata == rebuilt.metadata


def test_new_blocks_have_separate_adam_state_and_inherited_nonzero_state_is_unchanged():
    encoder, alpha, weights = networks()
    inherited_optimizer = torch.optim.Adam(alpha.parameters(), lr=.001)
    loss = sum(parameter.square().sum() for parameter in alpha.parameters())
    loss.backward()
    inherited_optimizer.step()
    old_state = copy.deepcopy(inherited_optimizer.state_dict())
    old_parameters = {name: parameter.detach().clone() for name, parameter in alpha.named_parameters()}
    assert any(torch.count_nonzero(state["exp_avg"]) for state in old_state["state"].values())
    extended = expand_models(alpha, weights, encoder, agent_ids=(0, 1, 2), scheme_order=SCHEME_ORDER)
    for name, parameter in extended.alpha.named_parameters():
        parameter.requires_grad_(name in extended.alpha_parameters)
    fresh = torch.optim.Adam([parameter for parameter in extended.alpha.parameters() if parameter.requires_grad], lr=.001)
    assert not fresh.state
    query = encoder.encode_query("fixture")
    history = extended.encoders.encode_history(events())
    output = extended.alpha(query, history, ("yes", "no", "yes"))
    output.values.square().sum().backward()
    fresh.step()
    for name, parameter in extended.alpha.named_parameters():
        if name in old_parameters:
            torch.testing.assert_close(parameter, old_parameters[name], rtol=0, atol=0)
    for key, state in inherited_optimizer.state_dict()["state"].items():
        for name, value in state.items():
            torch.testing.assert_close(value, old_state["state"][key][name], rtol=0, atol=0)
    assert all(state["step"].item() == 1 for state in fresh.state.values())


def test_raw_value_decoder_handles_values_that_underflow_probability_ratios():
    values = torch.zeros(1, 1, len(SCHEME_ORDER))
    values[0, 0, SCHEME_ORDER.index(Scheme.ZERO)] = -10000
    values[0, 0, SCHEME_ORDER.index(Scheme.SELF)] = -9998
    beta, _ = routing_beta(values, SCHEME_ORDER)
    assert beta.item() == 2
    action = decode_capacity_feasible(values, SCHEME_ORDER, 1, 1, session=1)
    assert action.matrix[(0, 0)] is Scheme.SELF
    assert action.objective_score == 2
    values[0, 0, SCHEME_ORDER.index(Scheme.SELF)] = -10001
    assert decode_capacity_feasible(values, SCHEME_ORDER, 1, 1, session=1).matrix[(0, 0)] is Scheme.ZERO


def test_category_extension_adds_columns_without_reinterpreting_unknown_or_missing():
    encoder, alpha, weights = networks()
    categories = {"event": (*encoder.categories["event"], "new-event")}
    extended = expand_models(alpha, weights, encoder, agent_ids=(0, 1), scheme_order=SCHEME_ORDER, categories=categories)
    assert extended.encoders.categories["event"][:-1] == encoder.categories["event"]
    assert extended.alpha.history_map.input_projection.added[0].shape[-1] == 1
    assert extended.alpha_parameters == ("history_map.input_projection.added.0",)
    assert extended.weighting_parameters == ("history_map.input_projection.added.0",)
    with pytest.raises(ValueError, match="append"):
        encoder.extended(agent_ids=(1, 0), scheme_order=SCHEME_ORDER)


def test_scheme_extension_zero_row_preserves_existing_values_and_old_alternatives():
    encoder, alpha, weights = networks()
    extra = Scheme("S_probe")
    labels = (*SCHEME_ORDER, extra)
    extended = expand_models(alpha, weights, encoder, agent_ids=(0, 1), scheme_order=labels,
                             legality={extra.value: ((0, 1),)})
    assert torch.count_nonzero(extended.alpha.routing_head.output.added_weights[0]) == 0
    assert torch.count_nonzero(extended.alpha.routing_head.output.added_biases[0]) == 0
    assert len(extended.alpha.answer_state_encoder.added_projections) == 0
    query = encoder.encode_query("fixture")
    before = alpha(query, encoder.encode_history(events()), ("yes", "no"))
    after = extended.alpha(query, extended.encoders.encode_history(events()), ("yes", "no"))
    torch.testing.assert_close(before.values, after.values[:, :, :len(SCHEME_ORDER)], rtol=0, atol=0)
    assert after.values[0, 1, -1] == 0
    assert after.probabilities[0, 1, -1] > 0
    assert after.probabilities[1, 0, -1] == 0
    assert all(after.probabilities[0, 1, index] > 0 for index in (0, 2, 3, 4, 5, 6))
    assert not after.probabilities.requires_grad
    assert extended.encoders.categories["scheme"][-1] == extra.value
    rebuilt = models_from_expansion_metadata(extended.metadata, configuration=alpha.configuration,
                                             encoder_configuration=encoder.configuration)
    rebuilt.alpha.load_state_dict(extended.alpha.state_dict(), strict=True)
    with pytest.raises(ValueError, match="explicit"):
        RoutingPolicy(2, encoder.history_dimension, configuration=alpha.configuration, scheme_order=labels)


def test_value_forward_and_decoder_never_apply_routing_softmax(monkeypatch):
    encoder, alpha, _ = networks()
    query = encoder.encode_query("fixture")
    history = encoder.encode_history(events())
    # Transformer/readout attention and W retain their required softmax. This
    # patches only the legal-label softmax used for routing exploration.
    import examples.adaptive_multi_agent_collab.models.policies as module
    monkeypatch.setattr(module, "masked_softmax", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sampling")))
    alpha(query, history, ("yes", "no"))
    alpha.route_inference(query, history, ("yes", "no"), 1, 1, session=1)


def test_query_embedding_cache_is_shared_when_only_structural_registries_expand():
    encoder, alpha, weights = networks()
    query = encoder.encode_query("fixed query")
    count = len(encoder.text_encoder.calls)
    expanded = expand_models(alpha, weights, encoder, agent_ids=(0, 1, 2), scheme_order=SCHEME_ORDER)
    torch.testing.assert_close(expanded.encoders.encode_query("fixed query"), query, rtol=0, atol=0)
    assert len(encoder.text_encoder.calls) == count
