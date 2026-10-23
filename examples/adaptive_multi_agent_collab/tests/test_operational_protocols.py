"""Operational repairs invalidate old conditions before restoring mutable state."""

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.infrastructure import failure_policy, persistence, shutdown
from examples.adaptive_multi_agent_collab.operations.experiment import _execution_metadata
from examples.adaptive_multi_agent_collab.operations.preflight import (
    stage_authorization_template,
    validate_stage_authorization,
)
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
    _build_offline_experiment,
)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "module,constant",
    [
        (persistence, "PERSISTENCE_PROTOCOL"),
        (failure_policy, "DEADLINE_CAUSE_PROTOCOL"),
        (failure_policy, "DISPATCH_ADMISSION_PROTOCOL"),
        (shutdown, "SHUTDOWN_PROTOCOL"),
    ],
)
def test_operational_protocols_invalidate_cache_approval_and_restore_before_mutation(
    tmp_path, monkeypatch, enabled, module, constant
):
    trainer, hooks, *_ = _build_offline_experiment(pilot_count=1)
    prepared = SimpleNamespace(
        config=replace(
            trainer.config,
            failure_policy=replace(
                trainer.config.failure_policy,
                enabled=enabled,
                max_unresolved_requests=8,
            ),
        ),
        specification_sha256="a" * 64,
        authorization_manifest_fingerprint="b" * 64,
        resource_profile_fingerprint="c" * 64,
        extension_registration={},
        continuation_registration={},
    )
    hooks.execution_metadata = _execution_metadata(prepared, {})
    condition = hooks.condition_fingerprint
    cache_key = hooks._cache_key(hooks.plans["routing"])
    saved_hooks = copy.deepcopy(hooks.state_dict())
    checkpoint = tmp_path / "original.pt"
    trainer.hooks = hooks
    trainer.save_checkpoint(checkpoint)
    bindings = dict(
        stage="pilot",
        manifest_fingerprint=prepared.authorization_manifest_fingerprint,
        condition_fingerprint=condition,
        resource_profile_fingerprint=prepared.resource_profile_fingerprint,
        approved_budget_usd=5.0,
    )
    approval = stage_authorization_template(**bindings)
    approval.update(
        decision="approve_stage",
        reviewer="offline fixture",
        rationale="disposable condition only",
    )
    validate_stage_authorization(approval, **bindings)

    monkeypatch.setattr(module, constant, "different-operational-contract")
    hooks.execution_metadata = _execution_metadata(prepared, {})
    assert hooks.condition_fingerprint != condition
    assert hooks._cache_key(hooks.plans["routing"]) != cache_key
    with pytest.raises(ValueError, match="differs"):
        validate_stage_authorization(
            approval,
            **{**bindings, "condition_fingerprint": hooks.condition_fingerprint},
        )
    with torch.no_grad():
        next(trainer.alpha.parameters()).add_(1)
    before = copy.deepcopy(trainer.alpha.state_dict())
    with pytest.raises(ValueError, match="incompatible experiment"):
        trainer.resume_from_checkpoint(checkpoint, hooks=hooks)
    assert all(
        torch.equal(before[key], value)
        for key, value in trainer.alpha.state_dict().items()
    )
    assert hooks.pilot_observations == []
    assert saved_hooks["pilot_observations"] == []
    assert approval["condition_fingerprint"] == condition
