# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MoA configuration, spec construction, and diagnostic state contracts."""

from dataclasses import FrozenInstanceError, fields
from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.harness.moa import MoAConfig, VerifierConfig
from openjiuwen.harness.moa.state import (
    MoAState,
    ProposalAttempt,
    ProposalBatch,
    ProposalSource,
    active_moa,
    get_moa_snapshot,
)
from openjiuwen.harness.schema import deep_agent_spec as spec_module
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import RailSpec
from tests.unit_tests.harness.moa.helpers import _spec


@pytest.mark.level1
@pytest.mark.parametrize("field", ["interval", "n_evaluations"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3", None])
def test_verifier_cadence_requires_positive_integers(field, value):
    with pytest.raises(BaseError, match=field) as caught:
        VerifierConfig(**{field: value})
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.level1
@pytest.mark.parametrize("field", ["max_reference_calls", "max_verifier_calls"])
@pytest.mark.parametrize("value", [-1, True, 0.5, "3", float("nan")])
def test_verifier_budgets_require_nonnegative_integers(field, value):
    with pytest.raises(BaseError, match=field) as caught:
        VerifierConfig(**{field: value})
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.level1
@pytest.mark.parametrize("field", ["max_reference_chars", "max_advice_chars"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3", None])
def test_content_limits_require_positive_integers(field, value):
    with pytest.raises(BaseError, match=field) as caught:
        MoAConfig(reference_models=(object(),), **{field: value})
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.level1
@pytest.mark.parametrize("value", [0, -1, True, "3", None, float("nan"), float("inf"), 10**400])
def test_reference_timeout_requires_a_finite_positive_duration(value):
    with pytest.raises(BaseError, match="reference_timeout") as caught:
        MoAConfig(reference_models=(object(),), reference_timeout=value)
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.level1
@pytest.mark.parametrize(
    "arguments",
    [
        {"reference_models": ()},
        {"reference_models": None},
        {"mode": "unknown"},
        {"mode": "verifier_routed"},
        {"verifier": None},
    ],
)
def test_invalid_configuration_uses_the_framework_error_code(arguments):
    with pytest.raises(BaseError) as caught:
        MoAConfig(**{"reference_models": (object(),), **arguments})
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    if arguments.get("reference_models", ()) is None:
        assert isinstance(caught.value.cause, TypeError)


@pytest.mark.level1
def test_unresolvable_skill_path_retains_the_native_error_as_its_cause():
    with pytest.raises(BaseError, match="consultation_skill_dir") as caught:
        MoAConfig(reference_models=(object(),), consultation_skill_dir="consult-reference-models\0")
    assert caught.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    assert isinstance(caught.value.cause, ValueError)


@pytest.mark.level0
@pytest.mark.parametrize("budget", [None, 0, 1])
def test_valid_limits_preserve_public_defaults_freezing_and_model_identity(budget):
    reference = object()
    verifier = VerifierConfig(interval=1, n_evaluations=2, max_reference_calls=budget, max_verifier_calls=budget)
    config = MoAConfig(reference_models=[reference], verifier=verifier, reference_timeout=0.25)
    assert config.reference_models == (reference,)
    assert config.reference_models[0] is reference
    assert config.verifier is verifier
    assert config.max_reference_chars == 12_000
    assert config.max_advice_chars == 48_000
    assert config.verifier.max_reference_calls == budget
    with pytest.raises(FrozenInstanceError):
        config.max_reference_chars = 1


def _moa():
    reference = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="test", api_base="https://example.test/v1"
        ),
        model_config=ModelRequestConfig(model="reference"),
    )
    return MoAConfig(reference_models=(reference,))


@pytest.mark.level0
def test_spec_build_accepts_runtime_moa_without_serializing_dependencies():
    spec = _spec()
    serialized = spec.model_dump_json()
    moa = _moa()
    context = BuildContext(moa=moa)
    agent = spec.build(context)
    assert agent.deep_config.moa is moa
    assert agent.deep_config.moa.reference_models[0] is moa.reference_models[0]
    assert context.moa is moa
    assert spec.model_dump_json() == serialized
    assert "moa" not in serialized


@pytest.mark.level1
def test_providers_and_child_specs_do_not_inherit_root_moa():
    from openjiuwen.harness.manifest import ensure_builtin_elements_registered

    ensure_builtin_elements_registered()
    spec = _spec()
    moa = _moa()
    context = BuildContext(moa=moa, extras={"keep": "value"})
    observed = []

    def provider(params, derived):
        observed.append(derived)
        child = _spec().build(derived)
        assert child.deep_config.moa is None
        return []

    spec.rails = [RailSpec(type="test.moa-context")]
    with patch.dict(spec_module._RAIL_PROVIDER_REGISTRY, {"test.moa-context": provider}):
        parts = spec.resolve_parts(context)
    assert parts.config.moa is moa
    assert len(observed) == 1 and observed[0].moa is None
    assert context.moa is moa and context.extras == {"keep": "value"}


def test_snapshot_is_frozen_detached_and_excludes_reference_text():
    key = (object(), 3)
    batch = ProposalBatch((ProposalSource(1, "secret reference body"),), key)
    batch.attempts.append(ProposalAttempt(key, True, "injected", 21))
    state = MoAState(
        pending=batch,
        active_key=key,
        completed_turns=2,
        reference_calls=1,
        jev_calls=3,
        last_jev_decision=True,
        last_jev_reason="consult",
    )
    token = active_moa.set(state)
    try:
        snapshot = get_moa_snapshot()
        assert snapshot.active_key == snapshot.pending_target == key
        assert snapshot.latest_attempt.identity == (batch.batch_id, 1)
        assert snapshot.latest_attempt.kept_chars == 21
        assert "secret reference body" not in repr(snapshot)
        assert {item.name for item in fields(snapshot)} == {
            "active_key",
            "completed_turns",
            "reference_calls",
            "verifier_calls",
            "previous_score",
            "jev_calls",
            "last_jev_decision",
            "last_jev_reason",
            "pending_target",
            "latest_attempt",
        }
        with pytest.raises(FrozenInstanceError):
            snapshot.reference_calls = 7
        with pytest.raises(FrozenInstanceError):
            snapshot.latest_attempt.reason = "changed"
        assert get_moa_snapshot().latest_attempt.identity == snapshot.latest_attempt.identity
        batch.attempts.append(ProposalAttempt(key, False, "insufficient_context"))
        state.reference_calls = 2
        state.jev_calls = 4
        state.last_jev_decision = False
        state.last_jev_reason = "continue"
        assert get_moa_snapshot().latest_attempt.identity == (batch.batch_id, 2)
        assert snapshot.reference_calls == 1
        assert snapshot.jev_calls == 3
        assert snapshot.last_jev_decision is True
        assert snapshot.last_jev_reason == "consult"
        assert snapshot.latest_attempt.reason == "injected"
    finally:
        active_moa.reset(token)


def test_snapshot_handles_missing_closed_and_expired_state():
    assert get_moa_snapshot() is None
    key = (object(), 1)
    state = MoAState(pending=ProposalBatch((ProposalSource(1, "text"),), key))
    token = active_moa.set(state)
    try:
        assert get_moa_snapshot().latest_attempt is None
        state.expire_pending("test")
        assert get_moa_snapshot().pending_target is None
        state.closed = True
        assert get_moa_snapshot() is None
    finally:
        active_moa.reset(token)


def test_snapshot_attempt_identity_distinguishes_batches_for_the_same_turn():
    key = (object(), 1)
    first = ProposalBatch((ProposalSource(1, "first"),), key, [ProposalAttempt(key, True, "injected")])
    state = MoAState(pending=first, active_key=key)
    token = active_moa.set(state)
    try:
        identity = get_moa_snapshot().latest_attempt.identity
        state.pending = ProposalBatch((ProposalSource(1, "second"),), key, [ProposalAttempt(key, True, "injected")])
        assert get_moa_snapshot().latest_attempt.identity != identity
    finally:
        active_moa.reset(token)
