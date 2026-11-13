"""Published replacement conditions are independent of later execution approvals."""

from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import experiment, preflight, supplement
from examples.adaptive_multi_agent_collab.workflows.recovery import WorkflowRecoveryConfig
from examples.adaptive_multi_agent_collab.tests.test_supplement_plan import registered  # noqa: F401


@pytest.fixture
def published(registered, monkeypatch):
    fixture = registered
    def planning(value):
        return SimpleNamespace(condition_fingerprint="d" * 64, _condition=lambda: {
            "declared": value.config.to_dict(), "specification": value.specification_sha256,
            "registration": value.continuation_registration,
        })
    monkeypatch.setattr(experiment, "planning_hooks", planning)
    amended = supplement.apply_prepared(fixture.prepared, fixture.plan)
    lineage = {"protocol": "audited_weighting_supplement_v1", "plan_path": str(fixture.path),
               "plan_sha256": supplement._sha(fixture.path),
               "parent_checkpoint": fixture.plan["references"]["checkpoint"]}
    payload = {"declared_config": amended.config.to_dict(), "config": amended.config.to_dict(),
               "hook_state": {"condition": planning(amended)._condition(), "supplement_lineage": lineage}}
    checkpoint = fixture.directory / "published.pt"
    torch.save(payload, checkpoint)
    return SimpleNamespace(**vars(fixture), amended=amended, published=payload,
                           checkpoint=checkpoint, lineage=lineage)


def test_published_condition_reconstruction_is_pure_and_idempotent(published):
    value = published
    before = copy.deepcopy(value.published)
    original = value.prepared.config.to_dict()
    result = supplement.prepare_checkpoint_condition(value.prepared, value.published,
                                                    specification_path=value.manifest)
    assert result.config == value.amended.config and result.config.workflow_recovery.enabled
    assert result.config.training.bootstrap_weighting_attempts == 50
    assert result.specification_sha256 == value.amended.specification_sha256
    assert supplement.prepare_checkpoint_condition(result, value.published) == result
    assert value.published == before and value.prepared.config.to_dict() == original
    # Calibration changes active thresholds, not the original declared condition.
    value.published["config"]["termination"]["routing_opportunity_threshold"] += 1
    assert supplement.prepare_checkpoint_condition(value.prepared, value.published) == result


def test_original_checkpoint_never_acquires_an_amendment(published):
    value = published
    original = {"hook_state": {}}
    assert supplement.prepare_checkpoint_condition(value.prepared, original) is value.prepared
    with pytest.raises(ValueError, match="lacks.*lineage"):
        supplement.prepare_checkpoint_condition(value.amended, original)
    lost = copy.deepcopy(value.published)
    lost["hook_state"].pop("supplement_lineage")
    with pytest.raises(ValueError, match="lacks.*lineage"):
        supplement.prepare_checkpoint_condition(value.prepared, lost)


@pytest.mark.parametrize("change", ["protocol", "hash", "parent", "extra", "condition", "config", "source"])
def test_condition_reconstruction_rejects_changed_lineage_before_mutation(published, change):
    value = published
    bad = copy.deepcopy(value.published)
    line = bad["hook_state"]["supplement_lineage"]
    source = value.manifest
    if change == "protocol":
        line["protocol"] = "invented"
    elif change == "hash":
        line["plan_sha256"] = "0" * 64
    elif change == "parent":
        line["parent_checkpoint"]["sha256"] = "0" * 64
    elif change == "extra":
        line["approve_regular_training"] = True
    elif change == "condition":
        bad["hook_state"]["condition"]["specification"] = "0" * 64
    elif change == "config":
        bad["declared_config"]["workflow_recovery"]["enabled"] = False
    else:
        source = value.directory / "another-manifest.json"
        source.write_text("{}")
    before = copy.deepcopy(bad)
    with pytest.raises(ValueError):
        supplement.prepare_checkpoint_condition(value.prepared, bad, specification_path=source)
    assert bad == before and not value.prepared.config.workflow_recovery.enabled


def test_extension_parent_requires_exact_explicit_supplement_reference(published):
    refs = {"parent_manifest": published.manifest, "parent_checkpoint": published.checkpoint}
    with pytest.raises(ValueError, match="explicit supplement"):
        experiment.prepare_extension_parent(refs)
    refs["parent_supplement_plan"] = published.path
    assert experiment.prepare_extension_parent(refs).config == published.amended.config
    wrong = published.directory / "wrong-plan.json"
    wrong.write_bytes(published.path.read_bytes())
    refs["parent_supplement_plan"] = wrong
    with pytest.raises(ValueError, match="reference differs"):
        experiment.prepare_extension_parent(refs)


def test_supplement_parent_reference_cannot_recurse_through_an_extension(published, monkeypatch):
    source = published.directory / "recursive-extension.json"
    source.write_text('{"experiment_profile":"registered_extension"}')
    monkeypatch.setattr(experiment, "prepare_experiment", lambda _path:
                        pytest.fail("a supplementary parent cannot recurse through extension preparation"))
    with pytest.raises(ValueError, match="primary condition"):
        experiment.prepare_extension_parent({"parent_manifest": source,
            "parent_checkpoint": published.checkpoint, "parent_supplement_plan": published.path})


def test_preflight_authorization_bindings_use_the_published_condition(published, monkeypatch):
    from examples.adaptive_multi_agent_collab.learning.calibration import SafetyCalibrationRecord
    from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager

    payload = copy.deepcopy(published.published)
    payload.update(schema_version=CheckpointManager.schema_version, request_boundary_pending=False,
                   trainer_state={"pilot_complete": True})
    payload["hook_state"]["safety_record"] = {"synthetic": True}
    torch.save(payload, published.checkpoint)
    monkeypatch.setattr(SafetyCalibrationRecord, "from_dict", lambda _value:
                        SimpleNamespace(execution_complete=True, fingerprint="a" * 64))
    bindings, _ = preflight.authorization_bindings(published.prepared, "development", published.checkpoint)
    assert bindings["manifest_fingerprint"] == published.amended.authorization_manifest_fingerprint
    assert bindings["manifest_fingerprint"] != published.prepared.authorization_manifest_fingerprint
    assert "execution_scope" not in bindings  # Reconstruction grants no W supplement or later-stage approval.


def test_amended_parent_still_requires_complete_selected_checkpoint(published, monkeypatch):
    from examples.adaptive_multi_agent_collab.operations import extension_experiment

    payload = copy.deepcopy(published.published)
    payload["trainer_state"] = {"phase": "calibration"}
    payload["best_system"] = None
    torch.save(payload, published.checkpoint)
    refs = {"parent_manifest": published.manifest, "parent_checkpoint": published.checkpoint,
            "parent_supplement_plan": published.path, "extension_manifest": published.manifest}
    from examples.adaptive_multi_agent_collab.tests.test_extension_adaptation import plan
    registration = {"references": refs, "plan": plan(published.amended.config).to_dict()}
    monkeypatch.setattr(extension_experiment, "validate_extension_registration", lambda _path: registration)
    monkeypatch.setattr(extension_experiment, "prepare_experiment", lambda _path: published.amended)
    restored = []
    def build(prepared, **_kwargs):
        assert prepared.config.workflow_recovery.enabled
        parent = SimpleNamespace(resume_from_checkpoint=lambda path, **kwargs: restored.append(path))
        return parent, SimpleNamespace()
    monkeypatch.setattr(extension_experiment, "build_experiment", build)
    with pytest.raises(ValueError, match="selected complete parent"):
        extension_experiment.build_extension_registration("synthetic")
    assert restored == [published.checkpoint]


@pytest.mark.parametrize("operation", ["development", "test"])
def test_supported_build_path_reconstructs_condition_without_selecting_supplement(published, monkeypatch, operation):
    """Stop at actual builder entry, before models, execution, or publication."""
    from examples.adaptive_multi_agent_collab.providers import identity

    class BeforeBuild(Exception):
        pass
    @contextmanager
    def guard(_path):
        yield lambda: True
    seen = []
    def inspect(_source, **kwargs):
        seen.append(kwargs)
        assert not kwargs.get("supplement_plan_path")
        return SimpleNamespace(require_ready=lambda: None)
    def build(prepared):
        assert prepared.config == published.amended.config
        raise BeforeBuild
    monkeypatch.setattr(preflight, "inspect_preflight", inspect)
    monkeypatch.setattr(experiment, "_campaign_directory", lambda *a, **k: published.directory)
    monkeypatch.setattr(experiment, "_campaign_lock", guard)
    monkeypatch.setattr(experiment, "validate_journal_resume", lambda *a, **k: None)
    monkeypatch.setattr(experiment, "build_experiment", build)
    monkeypatch.setattr(identity, "with_registered_response_identity", lambda value, _path: value)
    with pytest.raises(BeforeBuild):
        if operation == "development":
            asyncio.run(experiment.run_experiment(published.manifest, published.directory,
                resume=published.checkpoint, authorization_path="pending-not-read"))
        else:
            asyncio.run(experiment.evaluate_experiment(published.manifest, published.checkpoint,
                published.directory / "test", authorization_path="pending-not-read"))
    assert len(seen) == 1


def _reviewed_hooks():
    from examples.adaptive_multi_agent_collab.learning.calibration import PilotObservation, calibrate_safety
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
        _build_offline_experiment, _enable_stage_authorization, _stage_artifact,
    )
    trainer, hooks, *_ = _build_offline_experiment(pilot_count=1)
    _enable_stage_authorization(trainer, hooks)
    observation = PilotObservation(hooks.pilot_plan.ordered_example_ids[0], (1, 1, 1), (.1, .1, .1), (1., 1., 1.), True)
    hooks.pilot_observations = [observation]
    hooks.safety_record = calibrate_safety(hooks.pilot_plan, (observation,), trainer.config.safety)
    trainer.state.pilot_complete = True
    hooks.authorize_stage(_stage_artifact(hooks, "development"))
    return trainer, hooks


def test_new_condition_approvals_reload_without_expanding_old_supplement_scope(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _stage_artifact

    trainer, hooks = _reviewed_hooks()
    original = {"hook_state": copy.deepcopy(hooks.state_dict())}
    review = copy.deepcopy(hooks.safety_record.review)
    trainer.config = trainer.declared_config = replace(trainer.config,
        workflow_recovery=WorkflowRecoveryConfig(enabled=True))
    hooks.execution_metadata["authorization_manifest_fingerprint"] = "c" * 64
    plan = tmp_path / "scope-plan.json"
    plan.write_text(json.dumps({"references": {"forecast": {"sha256": "f" * 64}}}))
    hooks.supplement_lineage = {"plan_path": str(plan), "plan_sha256": "e" * 64}
    hooks.stage_authorizations = {}
    monkeypatch.setattr(hooks, "_validate_supplement_lineage", lambda _lineage: original)
    trainer.state.phase = "calibration"
    supplement_approval = _stage_artifact(hooks, "development", execution_scope=preflight.SUPPLEMENT_SCOPE,
        supplement_plan_sha256="e" * 64, supplement_forecast_sha256="f" * 64)
    hooks.authorize_stage(supplement_approval)
    hooks.validate_state(hooks.state_dict())
    with pytest.raises(ValueError, match="cannot launch another"):
        hooks.require_development(weighting_supplement=False)
    hooks.require_development(weighting_supplement=True)
    with pytest.raises(ValueError, match="explicit test"):
        hooks.require_stage_authorization("test", checkpoint_sha256="d" * 64)
    # This is a separate synthetic approval, never inferred from the old scope.
    hooks.authorize_stage(_stage_artifact(hooks, "development"))
    hooks.require_development(weighting_supplement=False)
    hooks.authorize_stage(_stage_artifact(hooks, "test", checkpoint_sha256="d" * 64))
    state = hooks.state_dict()
    hooks.validate_state(state)
    hooks.load_state_dict(state)
    assert hooks.safety_record.review == review
    assert original["hook_state"]["stage_authorizations"]["development"] == review
    assert hooks.stage_authorizations["development"]["scope"] == preflight.STAGE_SCOPES["development"]
    bad = copy.deepcopy(state)
    bad["stage_authorizations"]["development"]["condition_fingerprint"] = review["condition_fingerprint"]
    with pytest.raises(ValueError, match="differs from the exact"):
        hooks.validate_state(bad)


def test_restricted_hooks_delegate_the_enabled_replacement_coordinator():
    from examples.adaptive_multi_agent_collab.learning.extension import _ScopedHooks
    from examples.adaptive_multi_agent_collab.tests.test_workflow_recovery import _hooks

    trainer, hooks, calls = _hooks(failure="initial")
    owner = SimpleNamespace(check_spending=lambda: None,
        plan=SimpleNamespace(train_ids=hooks.active_data_plan.train_ids),
        progress=SimpleNamespace(unique_training_ids=[], free_zero=0, schemes={}, outgoing={}, incoming={}))
    wrapped = _ScopedHooks(owner, hooks)
    trainer.hooks = wrapped
    original = trainer.state.workflow_attempt_counter
    result = asyncio.run(trainer._trajectory("weighting"))
    assert trainer.config.workflow_recovery.enabled
    assert len(calls) == 2 and hooks.sample_counters["weighting"] == 1
    assert len(result.workflow_recovery["executions"]) == 2
    assert result.training_execution_id != original
