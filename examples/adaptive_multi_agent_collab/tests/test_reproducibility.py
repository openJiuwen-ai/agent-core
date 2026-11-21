"""Offline tests for fixed-encoder and scientific-condition metadata."""

from __future__ import annotations

from dataclasses import replace

import pytest
import torch

from examples.adaptive_multi_agent_collab.config import Config, EncoderConfig, SchemeRegistration, TrainingConfig
from examples.adaptive_multi_agent_collab.models.encoders import (
    FixedBaseEncoders,
    offline_encoder_configuration,
)
from examples.adaptive_multi_agent_collab.models.policies import (
    ANSWER_STATE_ARCHITECTURE_ID,
    ROUTING_ARCHITECTURE_ID,
    WEIGHTING_ARCHITECTURE_ID,
    ROUTING_SEED_DERIVATION_ID,
)
from examples.adaptive_multi_agent_collab.infrastructure.reproducibility import (
    configured_encoder_metadata,
    scientific_configuration,
    validate_scientific_compatibility,
)

IDENTIFIERS = {
    "answer_state_architecture_id": ANSWER_STATE_ARCHITECTURE_ID,
    "routing_architecture_id": ROUTING_ARCHITECTURE_ID,
    "weighting_architecture_id": WEIGHTING_ARCHITECTURE_ID,
    "routing_seed_derivation_id": ROUTING_SEED_DERIVATION_ID,
    "weighting_objective_id": "direct_candidate_mass_nll_v1",
}


def test_fixed_encoder_metadata_records_actual_fixture_and_event_representation() -> None:
    configuration = offline_encoder_configuration(numerical_precision="float64")
    encoder = FixedBaseEncoders(3, configuration=configuration)
    metadata = encoder.metadata()
    assert metadata["architecture_id"] == "frozen_minilm_semantic_registry_event_sequence_v4"
    assert metadata["encoder_type"] == "offline_fixture"
    assert metadata["loader"] == "explicit_offline_fixture"
    assert metadata["model_revision"] == "deterministic_fixture"
    assert metadata["query_dimension"] == 384
    assert metadata["history_event_dimension"] == encoder.history_dimension
    assert "recent_event_limit" not in metadata
    assert metadata["empty_history_representation"] == "empty_event_sequence"
    assert metadata["normalization"] == "chunk_l2_mean_l2"
    empty = encoder.encode_history(()).vectors
    assert empty.shape == (0, encoder.history_dimension) and empty.dtype == torch.float64


def test_encoder_fingerprint_tracks_every_behavioral_configuration_group() -> None:
    base = EncoderConfig()
    baseline = FixedBaseEncoders(3, configuration=base).fingerprint
    variants = (
        (2, base),
        (3, replace(base, model_revision="a" * 40)),
        (3, replace(base, serialization_version="observable_query_event_fields_v2")),
        (3, replace(base, structural_feature_version="one_hot_log1p_observable_events_v2")),
        (3, replace(base, answer_view_version="canonical_answer_with_option_text_v2")),
        (3, replace(base, numerical_precision="float64")),
        (3, replace(base, max_tokens=128)),
        (3, offline_encoder_configuration()),
    )
    assert all(FixedBaseEncoders(agents, configuration=config).fingerprint != baseline for agents, config in variants)


def test_configured_metadata_never_loads_a_backbone_and_matches_event_dimensions() -> None:
    config = Config(encoders=EncoderConfig())
    metadata = configured_encoder_metadata(config)
    assert metadata["query_dimension"] == 384
    assert metadata["history_event_dimension"] == FixedBaseEncoders(config.n_agents).history_dimension
    assert "recent_event_limit" not in metadata
    assert metadata["encoder_type"] == "minilm"


def test_scientific_configuration_accepts_exact_match_and_outer_ceiling_extension() -> None:
    saved = scientific_configuration(Config(), architecture_identifiers=IDENTIFIERS)
    extended = scientific_configuration(
        Config(training=TrainingConfig(max_outer_cycles=8)),
        architecture_identifiers=IDENTIFIERS,
    )

    validate_scientific_compatibility(saved.to_dict(), saved)
    validate_scientific_compatibility(saved.to_dict(), extended)


def test_scientific_configuration_rejects_immutable_change_and_ceiling_reduction() -> None:
    saved = scientific_configuration(
        Config(training=TrainingConfig(max_outer_cycles=5)),
        architecture_identifiers=IDENTIFIERS,
    )
    changed_encoder = scientific_configuration(
        Config(encoders=EncoderConfig(model_revision="b" * 40)),
        architecture_identifiers=IDENTIFIERS,
    )
    reduced = scientific_configuration(
        Config(training=TrainingConfig(max_outer_cycles=4)),
        architecture_identifiers=IDENTIFIERS,
    )

    with pytest.raises(ValueError, match="immutable scientific configuration"):
        validate_scientific_compatibility(saved.to_dict(), changed_encoder)
    with pytest.raises(ValueError, match="only preserve or extend"):
        validate_scientific_compatibility(saved.to_dict(), reduced)


def test_scientific_configuration_detects_corrupted_saved_fingerprint() -> None:
    current = scientific_configuration(Config(), architecture_identifiers=IDENTIFIERS)
    saved = current.to_dict()
    saved["fingerprint"] = "0" * 64

    with pytest.raises(ValueError, match="fingerprint is corrupt"):
        validate_scientific_compatibility(saved, current)


def test_configured_metadata_records_registered_extension_categories_and_scheme():
    base = FixedBaseEncoders(3, configuration=offline_encoder_configuration())
    config = Config(
        encoders=base.configuration,
        scheme_registry=(SchemeRegistration("S_probe", ((0, 1),), "init", "role", "revision", "purpose"),),
        history_categories=(("event", (*base.categories["event"], "new-event")),),
    )
    metadata = configured_encoder_metadata(config)
    assert metadata["schemes"][-1] == "S_probe"
    assert metadata["event_types"][-1] == "new-event"
    assert metadata["history_event_dimension"] == base.history_dimension + 2
    assert metadata == FixedBaseEncoders(
        3, configuration=config.encoders, scheme_order=config.scheme_order,
        categories=dict(config.history_categories),
    ).metadata()
