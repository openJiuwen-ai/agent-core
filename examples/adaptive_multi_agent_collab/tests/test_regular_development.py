"""Prospective TextGrad-only bindings preserve checkpoint0 and accounting."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.config import Config
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import FailurePolicyConfig
from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal, read_journal
from examples.adaptive_multi_agent_collab.infrastructure.storage import load_checkpoint, load_ledger, strict_json_equal
from examples.adaptive_multi_agent_collab.operations import experiment, regular_development as regular
from examples.adaptive_multi_agent_collab.operations.publication_recovery import apply_patch_values, exact_equal
from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger
from examples.adaptive_multi_agent_collab.workflows.recovery import WorkflowRecoveryConfig


@pytest.fixture
def registered(tmp_path, monkeypatch):
    config = Config()
    config = replace(config, resources=replace(config.resources, development_budget=80, currency="USD"),
                     failure_policy=FailurePolicyConfig(enabled=True, max_unresolved_requests=32,
                                                        max_unresolved_reserved_usd=5),
                     workflow_recovery=WorkflowRecoveryConfig(enabled=True))
    ledger = ExpenditureLedger(config.resources)
    ledger.activate_failure_policy(config.failure_policy, authorization={"approved": True, "source": "fixture"},
                                   bindings={"condition": "fixture"}, execution_guard=lambda: True)
    ledger.stage = "development"
    entry = ledger.reserve(.01, kind="role", model="fixture", metadata={})
    ledger.settle(entry, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    parent = experiment.PreparedExperiment(config=config, dataset=SimpleNamespace(), plans={},
        pilot_plan=None, termination_plan=SimpleNamespace(candidates=(config.termination,)), final_test_plan=None,
        backends={"gpt": {"resources": {"input_price": .4e-6, "output_price": 1.6e-6,
                                          "billing_fee_multiplier": 1., "request_fee": 0.}}},
        textgrad={"config": {"optimizer_engine": "same", "temperature": 0}, "optimizer_backend": "gpt"},
        specification_sha256="a" * 64, authorization_manifest_fingerprint="b" * 64,
        resource_profile_fingerprint="c" * 64, campaign_binding={"campaign_id": "fixture", "directory": str(tmp_path)})
    def hooks(value):
        condition = {"textgrad": value.textgrad, "resource": value.resource_profile_fingerprint}
        return SimpleNamespace(condition_fingerprint=regular._digest(condition), _condition=lambda: condition,
                               execution_metadata={"textgrad": value.textgrad})
    monkeypatch.setattr(experiment, "planning_hooks", hooks)
    monkeypatch.setattr(regular, "_parent_condition", lambda *_args: parent)
    manifest, approval = tmp_path / "manifest.json", tmp_path / "approval.txt"
    manifest.write_text("{}")
    approval.write_text("Offline test only; no production authority.")
    state = {"phase": "weighting", "phase_progress": {}, "outer_cycle": 0,
             "calibration_complete": True, "pilot_complete": True, "eligible_checkpoints": [0],
             "system_validation_records": [{"score": .85}], "bootstrap_complete": {"weighting": True, "routing": True},
             "attempts": {"weighting": 43, "routing": 10, "prompt": 0}, "updates": {"weighting": 10, "routing": 10, "prompt": 0}}
    hook_state = {"condition": hooks(parent)._condition(), "expenditure_ledger": ledger.state_dict(),
                  "sample_counters": {"weighting": 43, "routing": 10, "prompt": 0},
                  "termination_record": {"selected_index": 1}, "evaluation_cache": {"old": {"score": .85}},
                  "calibration_query_cache": {"old": {"query": "original evidence"}},
                  "stage_authorizations": {"development": {"scope": "calibration-only"}}}
    payload = {"request_boundary_pending": False, "declared_config": config.to_dict(),
               "config": replace(config, termination=replace(config.termination, max_sessions=1)).to_dict(),
               "trainer_state": state, "hook_state": hook_state, "alpha": torch.tensor([2.]),
               "alpha_optimizer": {"step": torch.tensor(10.), "exp_avg": torch.tensor([.3])},
               "best_system": {"score": .85, "hook_state": copy.deepcopy(hook_state), "alpha": torch.tensor([2.])}}
    snapshot = tmp_path / "checkpoint0.pt"
    torch.save(payload, snapshot)
    plan = regular.prepare_plan(manifest, snapshot, approval)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    return SimpleNamespace(parent=parent, payload=payload, plan=plan, plan_path=plan_path,
                           manifest=manifest, snapshot=snapshot, approval=approval, directory=tmp_path, ledger=ledger)


def test_amendment_changes_only_textgrad_repair_and_prospective_bindings(registered):
    old = registered.parent
    new = regular.apply_prepared(old, registered.plan)
    assert new.config is old.config and new.plans is old.plans
    assert new.termination_plan is old.termination_plan and new.specification_sha256 == old.specification_sha256
    assert new.textgrad["config"] == {**old.textgrad["config"], "output_repair_limit": 1}
    assert "output_repair_limit" not in old.textgrad["config"]
    assert new.authorization_manifest_fingerprint != old.authorization_manifest_fingerprint
    assert new.resource_profile_fingerprint != old.resource_profile_fingerprint
    with pytest.raises(ValueError, match="exact TextGrad parent"):
        regular.apply_prepared(new, registered.plan)


@pytest.mark.parametrize("change", ["extra", "budget", "parent", "baseline", "source"])
def test_plan_rejects_changed_recomputed_bindings(registered, change):
    plan = copy.deepcopy(registered.plan)
    if change == "extra": plan["ignored"] = True
    elif change == "budget": plan["amendment"]["budget"] = 160
    elif change == "parent": plan["parent"]["condition_fingerprint"] = "0" * 64
    elif change == "baseline": plan["baseline"]["receipt_count"] = 0
    else: registered.approval.write_text("different source")
    plan["fingerprint"] = regular._digest({key: value for key, value in plan.items() if key != "fingerprint"})
    registered.plan_path.write_text(json.dumps(plan))
    with pytest.raises(ValueError): regular.load_plan(registered.plan_path, registered.manifest)


@pytest.mark.parametrize("change", ["unsafe", "phase", "cycle", "calibration", "prompt", "halt", "inflight"])
def test_checkpoint0_boundary_cannot_waive_execution(registered, change):
    payload = copy.deepcopy(registered.payload)
    if change == "unsafe": payload["request_boundary_pending"] = True
    elif change == "phase": payload["trainer_state"]["phase"] = "calibration"
    elif change == "cycle": payload["trainer_state"]["outer_cycle"] = 1
    elif change == "calibration": payload["trainer_state"]["calibration_complete"] = False
    elif change == "prompt": payload["trainer_state"]["attempts"]["prompt"] = 1
    elif change == "halt": payload["hook_state"]["expenditure_ledger"]["halted"] = {"reason": "fatal"}
    else:
        registered.ledger.reserve(.01, kind="role", model="fixture", metadata={})
        payload["hook_state"]["expenditure_ledger"] = registered.ledger.state_dict()
    with pytest.raises(ValueError): regular._baseline(payload)


def test_patch_retains_numerical_state_scoring_caches_policy_and_unknown_hold(registered):
    parent = registered.payload
    amended = regular.apply_prepared(registered.parent, registered.plan)
    lineage = {"protocol": regular.LINEAGE_PROTOCOL, "plan_path": str(registered.plan_path),
               "plan_sha256": regular._sha(registered.plan_path), "parent_checkpoint": registered.plan["references"]["checkpoint"]}
    updated = apply_patch_values(parent, regular.expected_patch(parent, amended, lineage, {"scope": regular.SCOPE}))
    for name in ("alpha", "alpha_optimizer", "trainer_state", "config", "declared_config"):
        assert exact_equal(updated[name], parent[name])
    for name in ("expenditure_ledger", "termination_record", "evaluation_cache", "calibration_query_cache", "sample_counters"):
        assert exact_equal(updated["hook_state"][name], parent["hook_state"][name])
    assert updated["best_system"]["score"] == parent["best_system"]["score"]
    assert updated["best_system"]["hook_state"]["regular_lineage"] == lineage
    assert "regular_lineage" not in parent["hook_state"]


def test_forecast_counts_all_slots_replacements_textgrad_repairs_and_no_calibration(registered):
    amended = regular.apply_prepared(registered.parent, registered.plan)
    projection = {"scenarios": [{"name": "synthetic", "workflows": 120, "assumptions": {"test": True},
        "by_backend": [{"backend_key": "gpt", "model": "same", "components": [
            {"kind": "initial", "estimated_paid_usd": 12.}, {"kind": "tie", "estimated_paid_usd": 36.}]}]}]}
    bindings = dict.fromkeys(("manifest_fingerprint", "condition_fingerprint", "resource_profile_fingerprint", "pilot_fingerprint"), "a" * 64)
    result = regular.forecast(amended, registered.payload, projection, bindings, evidence={"fixture": True})
    assert result["schedule"]["scheduled_workflow_slots"] == 1600
    assert result["schedule"]["maximum_actual_workflow_executions"] == 3200
    assert result["schedule"]["maximum_textgrad_logical_calls"] == 125
    assert result["schedule"]["maximum_textgrad_physical_attempts"] == 375
    assert result["schedule"]["calibration_workflows"] == 0
    assert result["schedule"]["checkpoint0_baseline_regenerations"] == 0
    assert result["scenarios"][0]["workflow_expenditure_usd"] == pytest.approx(1280.)
    assert result["headroom_usd"] == pytest.approx(result["estimated_cost_usd"] * .25 + 4.99)
    assert result["starting_accounting"]["unresolved"] == .01
    assert result["completion_guaranteed"] is False


def test_real_compact_amendment_publication_reload_and_parent_integrity(registered):
    from examples.adaptive_multi_agent_collab.infrastructure.storage import amendment_payload
    original = registered.snapshot.read_bytes()
    amended = regular.apply_prepared(registered.parent, registered.plan)
    lineage = {"protocol": regular.LINEAGE_PROTOCOL, "plan_path": str(registered.plan_path),
               "plan_sha256": regular._sha(registered.plan_path), "parent_checkpoint": registered.plan["references"]["checkpoint"]}
    target = registered.directory / "checkpoint.pt"
    state = registered.payload["hook_state"]["expenditure_ledger"]
    async def run():
        writer = DurableJournal([registered.directory], state, allow_empty_initialization=True)
        try:
            await writer.checkpoint(registered.payload, target, safe=True)
            changes = regular.expected_patch(registered.payload, amended, lineage, {"scope": regular.SCOPE})
            envelope = amendment_payload(registered.snapshot, regular._sha(registered.snapshot), changes, target=target)
            await writer.amendment(envelope, target, state, safe=True, recovery_fingerprint=registered.plan["fingerprint"])
        finally:
            await writer.aclose()
    asyncio.run(run())
    loaded = load_checkpoint(target)
    assert loaded["hook_state"]["regular_lineage"] == lineage
    assert strict_json_equal(load_ledger(registered.directory / "expenditure_ledger.json"), state)
    assert registered.snapshot.read_bytes() == original
    journal = read_journal(registered.directory, include_audit=False)
    assert journal["boundary"]["checkpoint_sha256"] == regular._sha(target)
    assert exact_equal(loaded["alpha_optimizer"], registered.payload["alpha_optimizer"])
    assert regular.prepare_checkpoint_condition(registered.parent, loaded, specification_path=registered.manifest).textgrad["config"]["output_repair_limit"] == 1


@pytest.mark.parametrize("crash_after_publication", [False, True])
def test_transition_entrypoint_idempotence_and_committed_crash_recovery(registered, monkeypatch, crash_after_publication):
    from examples.adaptive_multi_agent_collab.operations import policy_activation, preflight
    fixture = registered
    target = fixture.directory / "checkpoint.pt"
    async def initialize():
        writer = DurableJournal([fixture.directory], fixture.ledger.state_dict(), allow_empty_initialization=True)
        try:
            await writer.checkpoint(fixture.payload, target, safe=True)
        finally:
            await writer.aclose()
    asyncio.run(initialize())
    fixture.snapshot.write_bytes(target.read_bytes())
    fixture.plan = regular.prepare_plan(fixture.manifest, fixture.snapshot, fixture.approval)
    fixture.plan_path.write_text(json.dumps(fixture.plan))
    auth = fixture.directory / "auth.json"
    auth.write_text(json.dumps({"scope": regular.SCOPE, "decision": "approve_stage"}))
    forecast_path = fixture.directory / "forecast.json"
    forecast_path.write_text("{}")
    monkeypatch.setattr(regular, "auth_bindings", lambda *_args: ({}, {}))
    monkeypatch.setattr(preflight, "load_stage_authorization", lambda path, **_kwargs: json.loads(path.read_text()))
    monkeypatch.setattr(policy_activation, "bind_policy_dispatch", lambda ledger, *_args: None)
    monkeypatch.setattr(regular, "bind_amended_policy", lambda *_args: True)
    built = []
    def factory(prepared, **_kwargs):
        ledger = ExpenditureLedger(prepared.config.resources)
        ledger.load_state_dict(fixture.ledger.state_dict())
        hooks = SimpleNamespace(expenditure_ledger=ledger,
            apply_regular=lambda *_args: None, authorize_stage=lambda _artifact: None)
        trainer = SimpleNamespace(resume_from_checkpoint=lambda *_args, **_kwargs: None,
                                  _request_checkpoint=lambda: None)
        built.append(trainer)
        return trainer, hooks
    original_write = experiment._write_json_atomic
    def fail_receipt(path, value):
        if path.name == "transition.json": raise OSError("simulated crash after committed publication")
        return original_write(path, value)
    if crash_after_publication:
        monkeypatch.setattr(experiment, "_write_json_atomic", fail_receipt)
        with pytest.raises(OSError, match="simulated crash"):
            regular.apply_transition(fixture.manifest, fixture.plan_path, auth, forecast_path, build_factory=factory)
        monkeypatch.setattr(experiment, "_write_json_atomic", original_write)
        committed_sequence = read_journal(fixture.directory, include_audit=False)["sequence"]
    first = regular.apply_transition(fixture.manifest, fixture.plan_path, auth, forecast_path, build_factory=factory)
    sequence = read_journal(fixture.directory, include_audit=False)["sequence"]
    assert regular.apply_transition(fixture.manifest, fixture.plan_path, auth, forecast_path, build_factory=factory) == first
    assert read_journal(fixture.directory, include_audit=False)["sequence"] == sequence
    if crash_after_publication: assert sequence == committed_sequence
    assert len(built) == 1
    assert strict_json_equal(load_ledger(fixture.directory / "expenditure_ledger.json"), fixture.ledger.state_dict())
    assert load_checkpoint(target)["trainer_state"] == fixture.payload["trainer_state"]


@pytest.mark.parametrize("field", ["regular_plan_sha256", "regular_forecast_sha256", "condition_fingerprint", "resource_profile_fingerprint"])
def test_regular_authorization_is_bound_to_exact_prospective_scope(field):
    from examples.adaptive_multi_agent_collab.operations.preflight import stage_authorization_template, validate_stage_authorization
    bindings = dict(stage="development", manifest_fingerprint="a" * 64, condition_fingerprint="b" * 64,
        resource_profile_fingerprint="c" * 64, approved_budget_usd=80., pilot_fingerprint="d" * 64,
        execution_scope=regular.SCOPE, regular_plan_sha256="e" * 64, regular_forecast_sha256="f" * 64)
    artifact = stage_authorization_template(**bindings)
    artifact.update(decision="approve_stage", reviewer="offline test", rationale="fixture only")
    assert validate_stage_authorization(artifact, **bindings) == artifact
    with pytest.raises(ValueError, match="differs"):
        validate_stage_authorization({**artifact, field: "0" * 64}, **bindings)
    with pytest.raises(ValueError):
        validate_stage_authorization(artifact, **{**bindings, "execution_scope": "development_calibration_only"})


@pytest.mark.parametrize("location", ["current", "best_system"])
@pytest.mark.parametrize("field,message", [
    ("evaluation_records", "original validation evidence"),
    ("recovery_slots", "historical recovery allowance"),
    ("termination_record", "frozen calibration outcome"),
    ("calibration_query_cache", "preserved calibration prefixes"),
])
def test_regular_parent_evidence_cannot_be_removed_from_current_or_best_state(location, field, message):
    from examples.adaptive_multi_agent_collab.learning.evaluation import Experiment
    hooks = Experiment.__new__(Experiment)
    hooks._condition = lambda: {"fixture": True}
    parent_hooks = {"evaluation_records": [], "recovery_slots": {}, "termination_record": None,
                    "calibration_query_cache": {}}
    parent_hooks[field] = ([{"original": True}] if field == "evaluation_records" else
                           {"historic": {"original": True}})
    parent = {"hook_state": parent_hooks}
    hooks._validate_regular_lineage = lambda _lineage: parent
    hooks._validate_calibration_lineage = lambda _lineage, **_kwargs: None
    hooks._validate_supplement_lineage = lambda _lineage, **_kwargs: None
    hooks.execution_metadata, hooks.sample_counters, hooks.plans = {}, {}, {}
    hooks.trainer = SimpleNamespace(config=Config())
    hooks.pilot_plan = hooks.termination_plan = None
    state = {"schema_version": 4, "condition": {"fixture": True}, "sample_counters": {},
             "evaluation_records": [], "recovery_slots": {}, "termination_record": None,
             "calibration_query_cache": {}}
    payload = {"hook_state": copy.deepcopy(state), "best_system": {"hook_state": copy.deepcopy(state)}}
    selected = payload["hook_state"] if location == "current" else payload["best_system"]["hook_state"]
    # CheckpointManager validates both hook states through this production method;
    # only parent I/O is substituted here, not evidence comparison or validation.
    with pytest.raises(ValueError, match=message):
        hooks.validate_state(selected)


@pytest.mark.parametrize("case", ["same", "newer_accounting", "changed_policy", "changed_receipt", "lock_lost", "wrong_approval"])
def test_bind_amended_policy_preserves_current_durable_accounting_and_original_policy(registered, monkeypatch, case):
    from examples.adaptive_multi_agent_collab.operations import policy_activation, preflight
    from examples.adaptive_multi_agent_collab.providers.resources import AccountingFailure
    fixture = registered
    amended = regular.apply_prepared(fixture.parent, fixture.plan)
    bindings = dict(stage="development", manifest_fingerprint="a" * 64, condition_fingerprint="b" * 64,
        resource_profile_fingerprint="c" * 64, approved_budget_usd=80., pilot_fingerprint="d" * 64,
        execution_scope=regular.SCOPE, regular_plan_sha256="e" * 64, regular_forecast_sha256="f" * 64)
    auth = preflight.stage_authorization_template(**bindings)
    auth.update(decision="approve_stage", reviewer="fixture", rationale="offline fixture only")
    if case == "wrong_approval": auth["regular_plan_sha256"] = "0" * 64
    auth_path = fixture.directory / "authorization.json"
    auth_path.write_text(json.dumps(auth))
    plan = copy.deepcopy(fixture.plan)
    plan["references"]["forecast"] = {"path": str(fixture.directory / "forecast.json"), "sha256": "a" * 64}
    lineage = {"plan_path": str(fixture.plan_path)}
    target = fixture.directory / "regular_development"
    target.mkdir()
    receipt = {"protocol": regular.TRANSITION_PROTOCOL, "lineage": lineage,
        "plan_fingerprint": plan["fingerprint"], "source_checkpoint": plan["references"]["checkpoint"],
        "generation_calls": 0, "spending_reset": False, "authorization": regular._reference(auth_path)}
    if case == "changed_receipt": receipt["spending_reset"] = True
    (target / "transition.json").write_text(json.dumps(receipt))
    activation_checks = []
    monkeypatch.setattr(policy_activation, "_validate_independent_activation", lambda *_args: activation_checks.append(True))
    monkeypatch.setattr(regular, "reconstruct_lineage", lambda *_args, **_kwargs: (fixture.parent, amended, plan))
    monkeypatch.setattr(regular, "auth_bindings", lambda *_args: (bindings, {}))
    if case == "newer_accounting":
        entry = fixture.ledger.reserve(.02, kind="role", model="fixture", metadata={})
        fixture.ledger.settle(entry, {"total_cost": None, "accounting_complete": False}, status="failed", elapsed=0)
    canonical = fixture.ledger.state_dict()
    if case == "changed_policy": canonical["failure_policy"]["authorization"]["source"] = "changed"
    ledger_path = fixture.directory / "expenditure_ledger.json"
    ledger_path.write_text(json.dumps(canonical))
    ledger = ExpenditureLedger(fixture.parent.config.resources)
    before_bytes = ledger_path.read_bytes()
    if case in {"same", "newer_accounting"}:
        assert regular.bind_amended_policy(ledger, fixture.manifest, lambda: True)
        assert strict_json_equal(ledger.state_dict(), canonical)
        assert len(ledger.entries) == (2 if case == "newer_accounting" else 1)
        assert ledger.failure_policy["authorization"]["source"] == "fixture"
        assert ledger.entries[0]["charged"] is None and ledger.entries[0]["reserved"] == .01
    else:
        with pytest.raises((ValueError, AccountingFailure)):
            regular.bind_amended_policy(ledger, fixture.manifest, lambda: case != "lock_lost")
    assert activation_checks == [True]
    assert ledger_path.read_bytes() == before_bytes
