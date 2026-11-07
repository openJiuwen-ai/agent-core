"""Inherited accepted safeguards do not fabricate an extension pilot result."""
import asyncio
import copy
import hashlib
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
    _build_offline_experiment, _enable_stage_authorization, _stage_artifact,
)


@pytest.fixture
def inherited_profile(tmp_path):
    parent, old, *_ = _build_offline_experiment(pilot_count=1)
    asyncio.run(parent.run_pilot(old))
    _enable_stage_authorization(parent, old)
    old.authorize_stage(_stage_artifact(old,"development"))
    payload = parent.checkpoint_payload()
    # Synthetic selected-parent boundary. This test exercises profile/approval
    # plumbing, not a claim of numerical convergence or paid development.
    payload["trainer_state"]["phase"] = "complete"
    payload["best_system"] = {"synthetic_selected_parent": True}
    path = tmp_path / "parent.pt"
    torch.save(payload,path)
    trainer,hooks,*_ = _build_offline_experiment(pilot_count=1)
    _enable_stage_authorization(trainer,hooks)
    hooks.execution_metadata["registered_extension"] = {
        "registration_fingerprint":"c"*64,"plan_fingerprint":"d"*64,
        "parent_condition":"e"*64,"extension_condition":"f"*64,
        "parent_checkpoint_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
        "references":{"parent_checkpoint":str(path)},
    }
    return trainer,hooks,old,path


def test_explicit_profile_record_keeps_local_pilot_empty_and_needs_new_approval(inherited_profile):
    trainer,hooks,parent,_ = inherited_profile
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    assert not trainer.state.pilot_complete and not hooks.pilot_observations and hooks.safety_record is None
    with pytest.raises(ValueError,match="explicit development"):
        hooks.require_development()
    approval = _stage_artifact(hooks,"development")
    assert approval["pilot_fingerprint"] == parent.safety_record.fingerprint
    hooks.authorize_stage(approval)
    hooks.require_development()
    assert not trainer.state.pilot_complete and hooks.safety_record is None
    assert hooks.state_dict()["extension_parent_acceptance"] == hooks.extension_parent_acceptance


def test_profile_checkpoint_roundtrip_and_mismatch_rejected_before_mutation(inherited_profile):
    _,hooks,_,_ = inherited_profile
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    hooks.authorize_stage(_stage_artifact(hooks,"development"))
    state = hooks.state_dict()
    hooks.validate_state(state)
    before = copy.deepcopy(hooks.extension_parent_acceptance)
    bad = copy.deepcopy(state)
    bad["extension_parent_acceptance"]["parent_pilot_fingerprint"] = "0"*64
    with pytest.raises(ValueError,match="parent safeguard"):
        hooks.load_state_dict(bad)
    assert hooks.extension_parent_acceptance == before
    hooks.load_state_dict(state)
    assert not hooks.trainer.state.pilot_complete and not hooks.pilot_observations


def test_extension_context_without_explicit_record_does_not_unlock_development(inherited_profile):
    _,hooks,_,_ = inherited_profile
    with pytest.raises(ValueError,match="complete separately executed pilot"):
        hooks.authorize_stage(_stage_artifact(hooks,"development"))
    with pytest.raises(ValueError,match="missing.*inherited"):
        hooks.validate_state(hooks.state_dict())


def test_changed_parent_checkpoint_is_not_reused(inherited_profile):
    _,hooks,_,path = inherited_profile
    path.write_bytes(path.read_bytes()+b"changed-source")
    with pytest.raises(ValueError,match="checkpoint changed"):
        hooks.expected_extension_parent_acceptance()


def test_parent_profile_record_cannot_be_injected_into_primary(inherited_profile):
    _,hooks,_,_ = inherited_profile
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    hooks.execution_metadata.pop("registered_extension")
    with pytest.raises(ValueError,match="explicit registered extension"):
        hooks.require_development()


def test_amended_parent_profile_uses_exact_historical_review_ancestry(inherited_profile, monkeypatch):
    from examples.adaptive_multi_agent_collab.operations import experiment

    _, hooks, original_hooks, path = inherited_profile
    original = torch.load(path, map_location="cpu", weights_only=False)
    amended = copy.deepcopy(original)
    amended["hook_state"]["condition"]["amendment"] = "synthetic published replacement policy"
    amended["hook_state"]["supplement_lineage"] = {"synthetic": "independently checked helper fixture"}
    torch.save(amended, path)
    context = hooks.execution_metadata["registered_extension"]
    context["parent_checkpoint_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    context["references"]["parent_supplement_plan"] = "synthetic checked plan"
    marker = object()
    monkeypatch.setattr(experiment, "prepare_extension_parent", lambda refs: marker)
    parent = SimpleNamespace(_validate_supplement_lineage=lambda lineage: original)
    monkeypatch.setattr(experiment, "planning_hooks", lambda prepared: parent if prepared is marker else None)
    expected = hooks.expected_extension_parent_acceptance()
    assert expected["parent_pilot_fingerprint"] == original_hooks.safety_record.fingerprint
    assert amended["hook_state"]["safety_record"] == original["hook_state"]["safety_record"]
    # An amended checkpoint cannot relabel its old pilot acceptance.
    hooks._extension_parent_source_facts = None
    amended["hook_state"]["safety_record"]["review"]["rationale"] = "changed review"
    torch.save(amended, path)
    context["parent_checkpoint_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="changed its historical pilot acceptance"):
        hooks.expected_extension_parent_acceptance()


def test_selected_extension_parent_reuses_verified_profile_without_fake_pilot(inherited_profile, tmp_path, monkeypatch):
    parent, hooks, original, _ = inherited_profile
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    payload = parent.checkpoint_payload()
    payload["hook_state"] = hooks.state_dict()
    payload["trainer_state"]["phase"] = "complete"
    payload["best_system"] = {"synthetic_selected_extension": True}
    path = tmp_path / "extension_parent.pt"
    torch.save(payload, path)
    child, child_hooks, *_ = _build_offline_experiment(pilot_count=1)
    _enable_stage_authorization(child, child_hooks)
    context = copy.deepcopy(hooks.execution_metadata["registered_extension"])
    context["parent_checkpoint_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    context["references"] = {"parent_checkpoint": str(path), "parent_manifest": "synthetic-parent.json"}
    child_hooks.execution_metadata["registered_extension"] = context
    from examples.adaptive_multi_agent_collab.operations import experiment

    marker = object()
    monkeypatch.setattr(experiment, "prepare_experiment", lambda source: marker)
    monkeypatch.setattr(experiment, "planning_hooks", lambda prepared: hooks if prepared is marker else None)
    child_hooks.extension_parent_acceptance = child_hooks.expected_extension_parent_acceptance()
    assert child_hooks.extension_parent_acceptance["parent_pilot_fingerprint"] == original.safety_record.fingerprint
    child_hooks.authorize_stage(_stage_artifact(child_hooks, "development"))
    child_hooks.require_development()
    assert not child.state.pilot_complete and child_hooks.safety_record is None


def test_parent_extension_acceptance_tampering_is_rejected(inherited_profile, tmp_path, monkeypatch):
    parent, hooks, _, _ = inherited_profile
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    payload = parent.checkpoint_payload()
    payload["hook_state"] = hooks.state_dict()
    payload["trainer_state"]["phase"] = "complete"
    payload["best_system"] = {"synthetic_selected_extension": True}
    payload["hook_state"]["extension_parent_acceptance"]["parent_pilot_fingerprint"] = "0" * 64
    path = tmp_path / "tampered_parent.pt"
    torch.save(payload, path)
    _, child_hooks, *_ = _build_offline_experiment(pilot_count=1)
    context = copy.deepcopy(hooks.execution_metadata["registered_extension"])
    context["parent_checkpoint_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    context["references"] = {"parent_checkpoint": str(path), "parent_manifest": "synthetic-parent.json"}
    child_hooks.execution_metadata["registered_extension"] = context
    from examples.adaptive_multi_agent_collab.operations import experiment

    monkeypatch.setattr(experiment, "prepare_experiment", lambda source: None)
    monkeypatch.setattr(experiment, "planning_hooks", lambda prepared: hooks)
    with pytest.raises(ValueError, match="incompatible accepted-profile lineage"):
        child_hooks.expected_extension_parent_acceptance()


def _selected_extension(inherited_profile, tmp_path):
    trainer, hooks, _, _ = inherited_profile
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    hooks.authorize_stage(_stage_artifact(hooks, "development"))
    trainer.hooks = hooks
    asyncio.run(hooks.calibrate_termination())
    trainer.state.calibration_complete = True
    trainer.state.phase = "complete"
    trainer.best_system = trainer._joint_state(hooks.termination_record.selected_evaluation)
    checkpoint = tmp_path / "selected_extension.pt"
    trainer.save_checkpoint(checkpoint)
    return trainer, hooks, checkpoint


def test_selected_extension_final_test_requires_new_exact_checkpoint_approval(inherited_profile, tmp_path):
    trainer, hooks, checkpoint = _selected_extension(inherited_profile, tmp_path)
    with pytest.raises(ValueError, match="test stage authorization"):
        asyncio.run(hooks.final_test(checkpoint))
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    pending = _stage_artifact(hooks, "test", checkpoint_sha256=digest)
    pending["decision"] = "pending"
    with pytest.raises(ValueError):
        hooks.authorize_stage(pending)
    hooks.authorize_stage(_stage_artifact(hooks, "test", checkpoint_sha256="0" * 64))
    with pytest.raises(ValueError, match="differs from the exact"):
        asyncio.run(hooks.final_test(checkpoint))
    hooks.authorize_stage(_stage_artifact(hooks, "test", checkpoint_sha256=digest))
    report = asyncio.run(hooks.final_test(checkpoint))
    assert report.to_dict()["stage_complete"]
    assert len(report.predictions) == 2
    assert not trainer.state.pilot_complete and hooks.safety_record is None


@pytest.mark.parametrize("missing", ["complete", "selection", "calibration"])
def test_incomplete_or_unselected_extension_cannot_get_test_approval(inherited_profile, tmp_path, missing):
    trainer, hooks, checkpoint = _selected_extension(inherited_profile, tmp_path)
    if missing == "complete":
        trainer.state.phase = "weighting"
    elif missing == "selection":
        trainer.best_system = None
    else:
        trainer.state.calibration_complete = False
    with pytest.raises(ValueError, match="complete selected checkpoint"):
        hooks.authorize_stage(_stage_artifact(hooks, "test", checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest()))


def test_extension_test_planning_rejects_incomplete_saved_selection(inherited_profile, tmp_path, monkeypatch):
    _, hooks, checkpoint = _selected_extension(inherited_profile, tmp_path)
    from examples.adaptive_multi_agent_collab.operations import experiment
    from examples.adaptive_multi_agent_collab.operations.preflight import authorization_bindings

    monkeypatch.setattr(experiment, "planning_hooks", lambda prepared: hooks)
    prepared = SimpleNamespace(extension_registration=hooks.execution_metadata["registered_extension"],
        config=hooks.config, authorization_manifest_fingerprint="fixture-manifest",
        resource_profile_fingerprint=hooks.execution_metadata["resource_profile_fingerprint"])
    bindings, _ = authorization_bindings(prepared, "test", checkpoint)
    assert bindings["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert bindings["pilot_fingerprint"] == hooks.extension_parent_acceptance["parent_pilot_fingerprint"]
    payload = torch.load(checkpoint, weights_only=False)
    payload["best_system"] = None
    torch.save(payload, checkpoint)
    with pytest.raises(ValueError, match="complete selected calibrated checkpoint"):
        authorization_bindings(prepared, "test", checkpoint)


@pytest.mark.parametrize("bad", [None, "approval", "candidate", "prospective", "prospective_unfinished", "prospective_replay"])
def test_preflight_reconstructs_only_declared_approved_escalation_stopping_entry(inherited_profile, tmp_path, monkeypatch, bad):
    trainer, hooks, _, _ = inherited_profile
    from examples.adaptive_multi_agent_collab.operations import experiment
    from examples.adaptive_multi_agent_collab.operations.preflight import authorization_bindings
    from examples.adaptive_multi_agent_collab.tests.test_extension_adaptation import plan as example_plan
    from examples.adaptive_multi_agent_collab.learning.training import _fingerprint

    plan = example_plan(trainer.config)
    scope = {"name":"reviewed_full_blocks", "alpha_parameters":["fixture_alpha"],
        "weighting_parameters":["fixture_weighting"], "weighting_phase":asdict(plan.weighting_phase),
        "routing_phase":asdict(plan.routing_phase), "spending_limit_usd":1.0,
        "physical_attempt_limit":10,"validation_attempt_limit":4,"calibration_attempt_limit":4}
    entry = replace(trainer.config.termination, persistence_length=trainer.config.termination.persistence_length + 1)
    plan = replace(plan, calibration_candidates=(trainer.config.termination, entry), escalation_scopes=(scope,))
    hooks.execution_metadata["registered_extension"]["plan"] = plan.to_dict()
    hooks.extension_parent_acceptance = hooks.expected_extension_parent_acceptance()
    original_plan = replace(hooks.termination_plan, candidates=plan.calibration_candidates)
    hooks.termination_plan = replace(original_plan, bootstrap_configuration=entry)
    payload = trainer.checkpoint_payload()
    payload["hook_state"] = hooks.state_dict()
    payload["extension"] = {"active_scope":scope["name"], "plan_fingerprint":plan.fingerprint,
        "scope_entry_termination":asdict(entry), "scope_authorization":{
            "decision":"approve", "scope_name":scope["name"], "plan_fingerprint":plan.fingerprint,
            "scope_fingerprint":_fingerprint(scope), "approval_source":"explicit synthetic scope approval"}}
    hooks.termination_plan = original_plan
    prospective = {}
    if bad and bad.startswith("prospective"):
        payload["hook_state"] = hooks.state_dict()
        payload["config"]["termination"] = asdict(entry)
        payload["extension"]["active_scope"] = None
        payload["extension"]["progress"] = {"phase":"weighting" if bad == "prospective_unfinished" else "complete"}
        payload["extension"]["scope_history"] = [{"scope":scope["name"]}] if bad == "prospective_replay" else []
        prospective = {"extension_scope":scope["name"],
            "extension_scope_authorization":payload["extension"]["scope_authorization"]}
    if bad == "approval":
        payload["extension"]["scope_authorization"]["scope_fingerprint"] = "0" * 64
    elif bad == "candidate":
        payload["extension"]["scope_entry_termination"]["persistence_length"] += 1
    path = tmp_path / "scoped.pt"
    torch.save(payload, path)
    monkeypatch.setattr(experiment, "planning_hooks", lambda prepared: hooks)
    prepared = SimpleNamespace(extension_registration=hooks.execution_metadata["registered_extension"],
        config=hooks.config, authorization_manifest_fingerprint="fixture-manifest",
        resource_profile_fingerprint=hooks.execution_metadata["resource_profile_fingerprint"])
    if bad is None or bad == "prospective":
        bindings, _ = authorization_bindings(prepared, "development", path, **prospective)
        assert bindings["condition_fingerprint"] == hooks.condition_fingerprint
        assert hooks.termination_plan.bootstrap_configuration == entry
    else:
        with pytest.raises(ValueError, match="unapproved escalation|not predeclared|completed unreplayed"):
            authorization_bindings(prepared, "development", path, **prospective)
