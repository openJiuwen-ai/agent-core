"""A prospective amendment cannot rewrite its parent or replenish its ledger."""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import experiment, supplement
from examples.adaptive_multi_agent_collab.learning.calibration import TerminationCalibrationPlan
from examples.adaptive_multi_agent_collab.config import Config
from examples.adaptive_multi_agent_collab.learning.evaluation import EvaluationPlan, FinalTestPlan, scoring_configuration


@pytest.fixture
def registered(tmp_path, monkeypatch):
    config = Config()
    config = replace(config, training=replace(config.training, bootstrap_weighting_attempts=30),
                     selection=replace(config.selection, lambda_select=.002))
    ids = tuple([supplement.amendment()["unchanged_weighting_recovery_query"], *[f"q{i}" for i in range(19)]])
    plans = {key: EvaluationPlan(ids, domain, 42, partition,
                                {"specification_sha256": "a" * 64}, scoring_configuration(config))
             for key, domain, partition in (("weighting", "wval", "comp-val"),
                                            ("routing", "alphaval", "comp-val"),
                                            ("prompt", "promptval", "comp-val"),
                                            ("system", "sysval", "sys-val"))}
    prepared = experiment.PreparedExperiment(
        config=config, dataset=SimpleNamespace(), plans=plans, pilot_plan=None,
        termination_plan=TerminationCalibrationPlan(plans["system"], (config.termination,), config.termination,
                                                   3, (2, 2, 2), (2, 2, 2), tuple(s.value for s in config.scheme_order)),
        final_test_plan=FinalTestPlan(("test",), (42, 43, 44)), backends={}, textgrad={},
        specification_sha256="a" * 64, authorization_manifest_fingerprint="b" * 64,
        resource_profile_fingerprint="c" * 64,
        campaign_binding={"campaign_id": "fixture", "directory": str(tmp_path)},
    )
    monkeypatch.setattr(experiment, "prepare_experiment", lambda _path: prepared)
    monkeypatch.setattr(experiment, "planning_hooks", lambda _prepared: SimpleNamespace(condition_fingerprint="d" * 64))
    manifest = tmp_path / "manifest.json"
    formal = tmp_path / "source.tex"
    approval = tmp_path / "approval.txt"
    forecast = tmp_path / "forecast.json"
    for path, content in ((manifest, "{}"), (formal, "fixture specification"), (approval, "fixture approval"), (forecast, "{}")):
        path.write_text(content)
    phases = [
        {"phase": "weighting_bootstrap", "attempts": 30, "updates": 8},
        {"phase": "routing_bootstrap", "attempts": 10, "updates": 10,
         "parameter_fingerprints_exit": {"alpha": "alpha", "weighting": "W"},
         "records": [{"plan": plans["routing"].to_dict(), "examples": [
             {"example_id": identifier, "normalized_cost": None if index == 0 else 0.2}
             for index, identifier in enumerate(ids)]}]},
    ]
    payload = {
        "request_boundary_pending": False, "gamma": {"fixture": "fixed"},
        "trainer_state": {"phase": "calibration", "phase_progress": {}, "outer_cycle": 0,
                          "pilot_complete": True, "calibration_complete": False,
                          "bootstrap_complete": {"weighting": True, "routing": True},
                          "attempts": {"weighting": 30, "routing": 10, "prompt": 0},
                          "updates": {"weighting": 8, "routing": 10, "prompt": 0},
                          "workflow_attempt_counter": 40, "phase_records": phases},
        "hook_state": {"sample_counters": {"weighting": 30, "routing": 10, "prompt": 0},
                       "expenditure_ledger": {"entries": [{"dispatch_id": 883, "charged": None,
                                                            "reserved": 0.0288,
                                                            "usage": {"failure_kind": "provider-unavailable",
                                                                      "generation_id": None}}]}},
    }
    snapshot = tmp_path / "source_checkpoint.pt"
    torch.save(payload, snapshot)
    plan = supplement.prepare_supplement_plan(manifest, snapshot, formal, approval, forecast)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    return SimpleNamespace(prepared=prepared, payload=payload, plan=plan, path=plan_path,
                           manifest=manifest, snapshot=snapshot, formal=formal, approval=approval,
                           forecast=forecast, directory=tmp_path)


def test_prospective_changes_are_exact_and_parent_is_unchanged(registered):
    fixture = registered
    original = fixture.prepared.config.to_dict()
    updated = supplement.apply_prepared(fixture.prepared, fixture.plan)
    expected = copy.deepcopy(original)
    expected["training"]["bootstrap_weighting_attempts"] = 50
    expected["workflow_recovery"] = supplement.amendment()["workflow_recovery"]
    assert updated.config.to_dict() == expected
    assert fixture.prepared.config.to_dict() == original
    assert updated.campaign_binding == fixture.prepared.campaign_binding
    assert updated.resource_profile_fingerprint == fixture.prepared.resource_profile_fingerprint
    assert updated.specification_sha256 != fixture.prepared.specification_sha256
    for name in fixture.prepared.plans:
        assert updated.plans[name].ordered_example_ids == fixture.prepared.plans[name].ordered_example_ids
        assert updated.plans[name].run_seed == fixture.prepared.plans[name].run_seed
        assert updated.plans[name].fingerprint != fixture.prepared.plans[name].fingerprint
    assert updated.termination_plan.validation == updated.plans["system"]


def test_plan_load_verifies_original_immutable_references(registered):
    assert supplement.load_supplement_plan(registered.path, registered.manifest) == registered.plan
    registered.formal.write_text("changed")
    with pytest.raises(ValueError, match="immutable reference"):
        supplement.load_supplement_plan(registered.path, registered.manifest)


@pytest.mark.parametrize("change", ["budget", "slots", "parent", "baseline", "extra"])
def test_plan_rejects_mutation_even_with_recomputed_digest(registered, change):
    plan = copy.deepcopy(registered.plan)
    if change == "budget":
        plan["amendment"]["development_budget"] = 160
    elif change == "slots":
        plan["amendment"]["maximum_new_weighting_slots"] = 21
    elif change == "parent":
        plan["parent"]["resource_profile_fingerprint"] = "0" * 64
    elif change == "baseline":
        plan["baseline"]["receipt_count"] = 0
    else:
        plan["ignored"] = True
    plan["fingerprint"] = supplement._digest({k: v for k, v in plan.items() if k != "fingerprint"})
    registered.path.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        supplement.load_supplement_plan(registered.path, registered.manifest)


def test_forecast_binding_does_not_change_scientific_condition(registered):
    first = supplement.apply_prepared(registered.prepared, registered.plan)
    registered.forecast.write_text('{"cost":19.09}')
    second_plan = supplement.prepare_supplement_plan(
        registered.manifest, registered.snapshot, registered.formal, registered.approval, registered.forecast,
    )
    second = supplement.apply_prepared(registered.prepared, second_plan)
    assert second_plan["fingerprint"] != registered.plan["fingerprint"]
    assert second.specification_sha256 == first.specification_sha256
    assert second.authorization_manifest_fingerprint == first.authorization_manifest_fingerprint
    assert second.continuation_registration == first.continuation_registration


@pytest.mark.parametrize("field,value", [("phase", "weighting_bootstrap"), ("calibration_complete", True),
                                        ("workflow_attempt_counter", 41), ("phase_progress", {"started": True})])
def test_not_the_original_completed_boundary_rejected(registered, field, value):
    payload = copy.deepcopy(registered.payload)
    payload["trainer_state"][field] = value
    with pytest.raises(ValueError, match="boundary"):
        supplement._baseline(payload)


def test_live_checkpoint_cannot_be_used_as_mutable_source_reference(registered):
    target = registered.directory / "checkpoint.pt"
    torch.save(registered.payload, target)
    with pytest.raises(ValueError, match="preserved checkpoint"):
        supplement.prepare_supplement_plan(registered.manifest, target, registered.formal,
                                          registered.approval, registered.forecast)


def test_preservation_is_idempotent_without_copying_the_full_journal(registered):
    directory = registered.directory
    (directory / "checkpoint.pt").write_bytes(registered.snapshot.read_bytes())
    (directory / "expenditure_ledger.json").write_text('{"holds":0.0646656}')
    (directory / "accounting_progress.jsonl").write_text('{"immutable":"prefix"}\n')
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    target = directory / "before"
    target.mkdir()
    (target / "checkpoint.pt.pending").write_bytes(b"interrupted incomplete copy")
    first = supplement._preserve_publications(directory, target)
    assert supplement._preserve_publications(directory, target) == first
    assert not (target / "accounting_progress.jsonl").exists()
    assert all((directory / name).read_bytes() == content for name, content in before.items())
    (target / "checkpoint.pt").write_bytes(b"conflict")
    with pytest.raises(ValueError, match="collision"):
        supplement._preserve_publications(directory, target)


def test_unknown_usage_is_not_changed_by_source_linkage(registered):
    source = supplement.routing_source(registered.plan)
    assert source["failure_evidence"] == registered.payload["hook_state"]["expenditure_ledger"]["entries"][0]
    assert source["failure_evidence"]["charged"] is None
    assert source["failure_evidence"]["usage"]["generation_id"] is None
    assert source["failure_evidence"]["reserved"] == .0288
