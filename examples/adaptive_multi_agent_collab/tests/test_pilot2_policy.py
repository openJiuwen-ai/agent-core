"""Independent policy approval on disposable, network-blocked campaign fixtures."""

from __future__ import annotations

import copy
import asyncio
import json
import os
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import policy_activation as policy
from examples.adaptive_multi_agent_collab.config import Config
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import proposed_failure_policy
from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger
from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager


@pytest.fixture
def independent(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.operations import campaigns

    anchor = tmp_path / "Pilot_2"
    anchor.mkdir()
    source = anchor / "strategyqa.json"
    base = Config()
    config = replace(
        base,
        failure_policy=proposed_failure_policy(),
        resources=replace(base.resources, pilot_budget=5),
    )
    specification = {
        "config": config.to_dict(),
        "campaign_directory": str(anchor),
        "independent_campaign": {
            "protocol": "independent_campaign_v1",
            "campaign_id": "Pilot_2",
            "registry_path": str(tmp_path / "registry.json"),
            "policy_plan_path": str(anchor / "policy_plan.json"),
            "shared_evidence_path": str(tmp_path / "shared.json"),
        },
    }
    source.write_text(json.dumps(specification))
    specification = json.loads(source.read_text())
    (anchor / "pilot_forecast.json").write_text(
        json.dumps({"fixture": "synthetic frozen forecast", "pilot_budget_usd": 5})
    )
    registration = {
        "campaign_id": "Pilot_2",
        "directory": str(anchor),
        "manifest_path": str(source),
        "manifest_sha256": policy._sha(source),
        "lifecycle": "registered",
        "condition_fingerprint": "c" * 64,
        "manifest_fingerprint": "m" * 64,
        "resource_profile_fingerprint": "r" * 64,
        "limits": asdict(config.resources),
        "policy": asdict(config.failure_policy),
    }

    def validate(value, _directory):
        if value != specification or policy._sha(source) != registration["manifest_sha256"]:
            raise ValueError("independent campaign registration changed")
        return copy.deepcopy(registration)

    monkeypatch.setattr(campaigns, "validate_campaign", validate)
    prepared = SimpleNamespace(
        config=config,
        authorization_manifest_fingerprint=registration["manifest_fingerprint"],
        resource_profile_fingerprint=registration["resource_profile_fingerprint"],
    )
    monkeypatch.setattr(policy, "prepare_experiment", lambda _path: prepared)
    monkeypatch.setattr(
        policy,
        "planning_hooks",
        lambda _prepared: SimpleNamespace(condition_fingerprint=registration["condition_fingerprint"]),
    )
    ledger = ExpenditureLedger(config.resources)
    ledger.stage = "pilot"
    (anchor / "expenditure_ledger.json").write_text(json.dumps(ledger.state_dict()))
    payload = {
        "schema_version": CheckpointManager.schema_version,
        "request_boundary_pending": False,
        "alpha": {"weights": torch.tensor([0.0, 0.0])},
        "weighting": {"weights": torch.tensor([0.0, 0.0])},
        "trainer_state": {"phase": "unstarted", "pilot_complete": False},
        "hook_state": {"expenditure_ledger": ledger.state_dict()},
    }
    torch.save(payload, anchor / "checkpoint.pt")
    (anchor / "initial_checkpoint.pt").write_bytes((anchor / "checkpoint.pt").read_bytes())
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal

    journal = DurableJournal([anchor], ledger.state_dict())
    journal.publish(ledger.state_dict(), anchor / "checkpoint.pt", safe=True)
    asyncio.run(journal.aclose())
    return SimpleNamespace(
        anchor=anchor,
        source=source,
        specification=specification,
        config=config,
        payload=payload,
        registration=registration,
    )


def _plan(fixture):
    return policy.build_independent_policy_plan(fixture.source, fixture.anchor)


def _approve(fixture):
    path = fixture.anchor / "policy_authorization_pending.json"
    authorization = json.loads(path.read_text())
    formal = fixture.anchor / "synthetic_formal.txt"
    formal.write_text(
        "Synthetic independent campaign uncertainty approval; eight requests and USD0.50 holds."
    )
    approval = fixture.anchor / "synthetic_approval.txt"
    approval.write_text("Disposable approval of policy only, no pilot or spending.")
    authorization.update(
        approved=True,
        formal_specification={"approved": True, **policy._reference(formal)},
        approval_source=policy._reference(approval),
        reviewer="offline fixture",
        rationale="Synthetic policy-only approval for independent zero-expenditure fixture.",
    )
    path.write_text(json.dumps(authorization))
    return path


def _activate(fixture):
    return policy.activate_policy_plan(
        fixture.anchor / "policy_plan.json", fixture.anchor / "policy_authorization_pending.json"
    )


def test_independent_plan_is_pending_idempotent_and_never_adopts_history(independent):
    before = {
        name: (independent.anchor / name).read_bytes()
        for name in ("checkpoint.pt", "expenditure_ledger.json")
    }
    plan = _plan(independent)
    assert plan == _plan(independent)
    authorization = json.loads((independent.anchor / "policy_authorization_pending.json").read_text())
    assert authorization["approved"] is False
    assert "historical_adoption" not in authorization
    assert "source_ledger_sha256" not in authorization
    assert not any(authorization["stage_authorizations"].values())
    assert plan["policy"] == asdict(proposed_failure_policy())
    assert all((independent.anchor / name).read_bytes() == content for name, content in before.items())
    with pytest.raises(ValueError, match="pending"):
        _activate(independent)
    with pytest.raises(ValueError, match="pending"):
        policy.validate_policy_condition(independent.specification, independent.anchor)


@pytest.mark.parametrize("change", ("campaign", "condition", "stage", "historical", "formal"))
def test_independent_policy_rejects_old_or_broadened_authorization(independent, change):
    _plan(independent)
    path = _approve(independent)
    value = json.loads(path.read_text())
    if change == "campaign":
        value["campaign_id"] = "Pilot_1"
    elif change == "condition":
        value["condition_fingerprint"] = "old condition"
    elif change == "stage":
        value["stage_authorizations"]["pilot"] = True
    elif change == "historical":
        value["historical_adoption"] = {"approved": True, "dispatch_id": 400}
    else:
        value["formal_specification"]["approved"] = False
    path.write_text(json.dumps(value))
    before = (independent.anchor / "expenditure_ledger.json").read_bytes()
    with pytest.raises(ValueError):
        _activate(independent)
    assert (independent.anchor / "expenditure_ledger.json").read_bytes() == before
    assert not (independent.anchor / "policy_activation.json").exists()


def test_independent_activation_changes_only_own_policy_and_preserves_zero_budget(independent):
    _plan(independent)
    _approve(independent)
    initial_journal = (independent.anchor / "accounting_progress.jsonl").read_bytes()
    receipt = _activate(independent)
    assert _activate(independent) == receipt
    assert receipt["pilot_authorized"] is False and receipt["imported_historical_state"] is False
    from examples.adaptive_multi_agent_collab.infrastructure.storage import load_ledger
    state = load_ledger(independent.anchor / "expenditure_ledger.json")
    assert state["entries"] == [] and state["halted"] is None
    assert state["limits"]["pilot_budget"] == 5
    assert state["failure_policy"]["historical_adoptions"] == []
    assert state["failure_policy"]["config"] == asdict(proposed_failure_policy())
    payload = torch.load(independent.anchor / "checkpoint.pt", weights_only=False)
    for name in ("alpha", "weighting"):
        assert torch.equal(payload[name]["weights"], independent.payload[name]["weights"])
    assert payload["trainer_state"] == independent.payload["trainer_state"]
    assert payload["hook_state"]["expenditure_ledger"] == state
    assert (independent.anchor / "accounting_progress.jsonl").read_bytes().startswith(initial_journal)
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal

    journal = read_journal(independent.anchor)
    assert journal["state"] == state
    assert journal["boundary"] is not None
    assert receipt["activated_journal"]["sha256"] == policy._sha(independent.anchor / "accounting_progress.jsonl")
    from examples.adaptive_multi_agent_collab.operations.experiment import validate_journal_resume

    validate_journal_resume(independent.anchor / "checkpoint.pt", independent.anchor)
    ledger = ExpenditureLedger(independent.config.resources)
    policy.bind_policy_dispatch(ledger, independent.source, lambda: True)
    assert ledger.state_dict() == state
    assert ledger.stage_summary("pilot")["paid"] == 0
    assert ledger.stage_summary("pilot")["unresolved"] == 0


@pytest.mark.parametrize("publication", ["checkpoint.pt", "accounting_progress.jsonl"])
def test_interrupted_independent_activation_finishes_without_reset(independent, monkeypatch, publication):
    _plan(independent)
    _approve(independent)
    actual_replace = os.replace

    def interrupt(source, target):
        if target == independent.anchor / publication:
            raise OSError("synthetic interruption after ledger publication")
        return actual_replace(source, target)

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(OSError, match="synthetic interruption"):
        _activate(independent)
    assert not (independent.anchor / "policy_activation.json").exists()
    ledger_after_interruption = (independent.anchor / "expenditure_ledger.json").read_bytes()
    with pytest.raises(ValueError, match="pending"):
        policy.validate_policy_condition(independent.specification, independent.anchor)
    monkeypatch.setattr(os, "replace", actual_replace)
    receipt = _activate(independent)
    assert (independent.anchor / "expenditure_ledger.json").read_bytes() == ledger_after_interruption
    assert _activate(independent) == receipt


def test_independent_activation_does_not_reset_changed_campaign_state(independent):
    _plan(independent)
    _approve(independent)
    path = independent.anchor / "expenditure_ledger.json"
    state = json.loads(path.read_text())
    state["stage"] = "development"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="changed from its initial"):
        _activate(independent)
    assert json.loads(path.read_text())["stage"] == "development"


def test_independent_evidence_reuse_does_not_require_policy_activation(independent, monkeypatch):
    from examples.adaptive_multi_agent_collab.operations import campaigns, preflight

    evidence = {
        "scope": "preserved request paths",
        "failure_policy_live_validated": False,
        "response_identity_evidence": {},
    }
    monkeypatch.setattr(campaigns, "shared_compatibility_evidence", lambda *_args: evidence)
    assert (
        preflight.validate_finalized_compatibility(independent.specification, independent.source) is evidence
    )
    assert policy.policy_compatibility_evidence(independent.source) is evidence
    assert not (independent.anchor / "policy_activation.json").exists()


def _approve_forecast(independent):
    path = independent.anchor / "pilot_forecast_review_pending.json"
    review = json.loads(path.read_text())
    source = independent.anchor / "synthetic_forecast_approval.txt"
    source.write_text(
        "Disposable approval of this registered forecast only; no activation or pilot execution."
    )
    review.update(
        decision="approve_forecast",
        approval_source=policy._reference(source),
        reviewer="offline fixture",
        rationale="Synthetic review of fixed workload assumptions and safe incomplete-stage behavior.",
    )
    path.write_text(json.dumps(review))
    return path


def test_frozen_forecast_review_is_separate_pending_and_bound_without_manifest_change(independent):
    from examples.adaptive_multi_agent_collab.operations.preflight import validate_independent_forecast_review

    _plan(independent)
    source_bytes = independent.source.read_bytes()
    with pytest.raises(ValueError, match="pending"):
        validate_independent_forecast_review(independent.specification, independent.source)
    path = _approve_forecast(independent)
    reviewed_bytes = path.read_bytes()
    review = validate_independent_forecast_review(independent.specification, independent.source)
    assert review["forecast_sha256"] == policy._sha(independent.anchor / "pilot_forecast.json")
    assert review["pilot_budget_usd"] == 5
    assert review["scope"] == "pilot_forecast_review_only_no_activation_or_generation"
    assert independent.source.read_bytes() == source_bytes
    assert not (independent.anchor / "policy_activation.json").exists()
    _plan(independent)
    assert path.read_bytes() == reviewed_bytes


@pytest.mark.parametrize(
    "changed", ("campaign_id", "forecast_sha256", "pilot_budget_usd", "condition_fingerprint")
)
def test_forecast_review_rejects_wrong_campaign_forecast_budget_or_condition(independent, changed):
    from examples.adaptive_multi_agent_collab.operations.preflight import validate_independent_forecast_review

    _plan(independent)
    path = _approve_forecast(independent)
    review = json.loads(path.read_text())
    review[changed] = 6 if changed == "pilot_budget_usd" else "different binding"
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="mismatched"):
        validate_independent_forecast_review(independent.specification, independent.source)


def test_forecast_cannot_change_after_review_and_missing_paperwork_resumes_pending(independent):
    from examples.adaptive_multi_agent_collab.operations.preflight import validate_independent_forecast_review

    _plan(independent)
    path = independent.anchor / "pilot_forecast_review_pending.json"
    path.unlink()
    _plan(independent)
    assert json.loads(path.read_text())["decision"] == "pending"
    _approve_forecast(independent)
    (independent.anchor / "pilot_forecast.json").write_text("changed forecast")
    with pytest.raises(ValueError, match="artifact changed"):
        validate_independent_forecast_review(independent.specification, independent.source)


def test_forecast_review_requires_a_registered_finite_ceiling():
    from examples.adaptive_multi_agent_collab.operations.preflight import independent_forecast_review_template

    for invalid in (None, 0, float("inf"), True):
        with pytest.raises(ValueError, match="finite positive"):
            independent_forecast_review_template({}, invalid)
