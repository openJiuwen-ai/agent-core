"""Fresh campaign initialization with real offline models and disposable state."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import campaigns, experiment, policy_activation, projection
from examples.adaptive_multi_agent_collab.config import Config
from examples.adaptive_multi_agent_collab.models.encoders import FixedBaseEncoders
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import proposed_failure_policy
from examples.adaptive_multi_agent_collab.workflows.prompts import DeterministicFakeProposer
from examples.adaptive_multi_agent_collab.providers.resources import AccountingFailure, ExpenditureLedger
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment


def _read(path):
    return json.loads(path.read_text())


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def initialization(tmp_path, monkeypatch):
    """Only data preparation, published evidence and forecast are synthetic."""
    original_build = experiment.build_experiment
    captured = {}

    def capture(prepared, **kwargs):
        captured["prepared"] = prepared
        return original_build(prepared, **kwargs)

    with monkeypatch.context() as temporary:
        temporary.setattr(experiment, "build_experiment", capture)
        _build_offline_experiment(pilot_count=8)
    baseline = captured["prepared"]
    config = replace(
        baseline.config,
        resources=replace(
            baseline.config.resources,
            pilot_budget=5,
            development_budget=80,
            test_budget=20,
            compatibility_budget=3,
            currency="USD",
        ),
    )
    old = tmp_path / "runs/Pilot_1"
    old.mkdir(parents=True)
    old_checkpoint = old / "checkpoint.pt"
    old_checkpoint.write_bytes(b"Historical checkpoint deliberately cannot be imported or deserialized.")
    old_ledger = old / "expenditure_ledger.json"
    old_ledger.write_text('{"historical":true,"charged":1.25,"unresolved":0.0018816}')
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "config": config.to_dict(),
                "campaign_directory": str(old),
                "backends": baseline.backends,
                "approvals": {"pilot": True},
                "spending_plan": {"pilot": {"reviewed": True}},
                "revised_condition": {"old_checkpoint": str(old_checkpoint)},
                "policy_transition": {"old": True},
            }
        )
    )
    shared = tmp_path / "shared.json"
    shared.write_text('{"synthetic_shared_evidence":true}')
    evidence = {
        "artifacts": {"initial_compatibility": {"path": str(shared), "sha256": campaigns._sha(shared)}},
        "historical_compatibility_report": {"synthetic": True},
        "response_identity_evidence": {},
    }
    monkeypatch.setattr(campaigns, "ROOT", tmp_path)
    monkeypatch.setattr(campaigns, "shared_compatibility_evidence", lambda *_args: copy.deepcopy(evidence))
    monkeypatch.setattr(
        projection,
        "pilot_paid_projection",
        lambda *_args, **_kwargs: {
            "synthetic": True,
            "scenarios": [{"total_paid_tariff_usd": 0.8, "proposed_headroom_usd": 0.2}],
        },
    )
    builds = []
    invocation_count = []

    class ForbiddenModel:
        async def invoke(self, **kwargs):
            invocation_count.append(kwargs)
            raise AssertionError("offline initialization must never request generation")

    def build(prepared):
        np.random.seed(prepared.config.seed)
        trainer, hooks = original_build(
            prepared,
            model_factory=lambda _backend: ForbiddenModel(),
            fixed_encoders=FixedBaseEncoders(prepared.config.n_agents, configuration=prepared.config.encoders),
            proposer=DeterministicFakeProposer(),
        )
        return trainer, hooks

    def builder(prepared):
        result = build(prepared)
        builds.append(result)
        return result

    def prepare(path):
        path = Path(path)
        specification = _read(path)
        chosen = Config.from_dict(specification["config"])
        return replace(
            baseline,
            config=chosen,
            specification_sha256=specification["specification_sha256"],
            authorization_manifest_fingerprint=_digest(specification),
            resource_profile_fingerprint=_digest(asdict(chosen.resources)),
            campaign_binding=campaigns.campaign_binding(path, specification),
        )

    monkeypatch.setattr(experiment, "prepare_experiment", prepare)
    monkeypatch.setattr(policy_activation, "prepare_experiment", prepare)
    monkeypatch.setattr(experiment, "planning_hooks", lambda prepared: build(prepared)[1])
    monkeypatch.setattr(policy_activation, "planning_hooks", lambda prepared: build(prepared)[1])
    destination = tmp_path / "runs/Pilot_2"
    approval = {"source": "Synthetic offline fixture approval", "scope": "Independent initialization only"}
    kwargs = {
        "shared_evidence_path": shared,
        "specification_sha256": "b" * 64,
        "authorization": approval,
        "builder": builder,
    }
    return SimpleNamespace(
        source=source,
        destination=destination,
        kwargs=kwargs,
        builds=builds,
        invocations=invocation_count,
        old_bytes={path: path.read_bytes() for path in (old_checkpoint, old_ledger, source, shared)},
        initialize=lambda: campaigns.initialize_pilot(source, destination, **kwargs),
    )


def test_fresh_initialization_uses_untrained_parameters_and_no_historical_state(initialization):
    item = initialization
    receipt = item.initialize()
    trainer, hooks = item.builds[-1]
    payload = torch.load(item.destination / "checkpoint.pt", weights_only=False)
    manifest = _read(item.destination / "manifest.json")
    assert receipt["phase"] == "unstarted"
    assert receipt["generation_authorized"] is False and receipt["policy_activated"] is False
    assert receipt["imported_execution_state"] is False
    assert manifest["approvals"] == {}
    assert "revised_condition" not in manifest and "policy_transition" not in manifest
    assert manifest["config"]["failure_policy"] == asdict(proposed_failure_policy())
    assert manifest["spending_plan"]["pilot"]["reviewed"] is False
    assert payload["trainer_state"] == asdict(trainer.state)
    assert not trainer.state.pilot_complete and not any(trainer.state.updates.values())
    assert not trainer.alpha_optimizer.state and not trainer.weighting_optimizer.state
    for name in ("alpha", "weighting"):
        assert all(torch.equal(value, payload[name][key]) for key, value in getattr(trainer, name).state_dict().items())
    assert not hooks.pilot_observations and not hooks.evaluation_cache and not hooks.evaluation_records
    assert not hooks.stage_authorizations and hooks.safety_record is None and hooks.termination_record is None
    state = _read(item.destination / "expenditure_ledger.json")
    assert state["entries"] == [] and state["halted"] is None and "failure_policy" not in state
    assert state["campaign_binding"]["campaign_id"] == "Pilot_2"
    assert all(value["paid"] == 0 and value["unresolved"] == 0 for value in receipt["operational_balances"].values())
    assert _read(item.destination / "pilot_authorization_pending.json")["decision"] == "pending"
    assert not _read(item.destination / "policy_authorization_pending.json")["approved"]
    assert all(path.read_bytes() == original for path, original in item.old_bytes.items())
    assert item.invocations == []
    assert experiment.validate_journal_resume(item.destination / "checkpoint.pt", item.destination) == state


def test_pending_independent_policy_refuses_physical_reservation_before_mutation(initialization):
    item = initialization
    item.initialize()
    trainer, hooks = item.builds[-1]
    ledger = ExpenditureLedger(trainer.config.resources)
    ledger.load_state_dict(_read(item.destination / "expenditure_ledger.json"))
    before = ledger.state_dict()
    with pytest.raises(AccountingFailure, match="activation is pending"):
        ledger.reserve(0.01, kind="initial", model="fixture", metadata={"request_hash": "synthetic"})
    assert ledger.state_dict() == before
    assert hooks.expenditure_ledger.entries == [] and item.invocations == []


def test_registered_pilot_cannot_spend_a_new_compatibility_allowance(initialization):
    item = initialization
    item.initialize()
    trainer, _hooks = item.builds[-1]
    ledger = ExpenditureLedger(trainer.config.resources)
    ledger.load_state_dict(_read(item.destination / "expenditure_ledger.json"))
    # An in-memory disposable activation isolates the stage guard from the pending-policy guard.
    ledger.activate_failure_policy(
        proposed_failure_policy(),
        authorization={"approved": True, "source": "synthetic test-only activation"},
        bindings={"condition": "synthetic registered Pilot_2 fixture"},
        execution_guard=lambda: True,
    )
    ledger.stage = "compatibility"
    before = ledger.state_dict()
    assert ledger.stage_summary("compatibility")["attempts"] == 0
    with pytest.raises(AccountingFailure, match="does not own the separate cumulative compatibility allowance"):
        ledger.reserve(0.01, kind="initial", model="fixture", metadata={"request_hash": "synthetic"})
    assert ledger.state_dict() == before
    assert item.invocations == []


def test_duplicate_initialization_does_not_reset_recorded_expenditure(initialization):
    item = initialization
    receipt = item.initialize()
    trainer, _hooks = item.builds[-1]
    # Construct a known-charge disposable receipt separately; no live dispatch or policy approval.
    fixture_ledger = ExpenditureLedger(trainer.config.resources)
    fixture_ledger.stage = "pilot"
    entry = fixture_ledger.reserve(0.1, kind="initial", model="synthetic", metadata={"request_hash": "fixture"})
    fixture_ledger.settle(entry, {"total_cost": 0.03}, status="completed", elapsed=0.1)
    fixture_ledger.campaign_binding = _read(item.destination / "expenditure_ledger.json")["campaign_binding"]
    journal = item.destination / "expenditure_ledger.json"
    journal.write_text(json.dumps(fixture_ledger.state_dict()))
    before = {path: path.read_bytes() for path in item.destination.iterdir() if path.is_file()}
    assert item.initialize() == receipt
    assert len(item.builds) == 1
    assert all(path.read_bytes() == original for path, original in before.items())
    assert _read(journal)["entries"][0]["charged"] == 0.03
    assert item.invocations == []


@pytest.mark.parametrize("wrong_side", ("journal", "checkpoint"))
def test_old_campaign_binding_is_rejected_before_resume_mutation(initialization, wrong_side):
    item = initialization
    item.initialize()
    checkpoint = item.destination / "checkpoint.pt"
    journal = item.destination / "expenditure_ledger.json"
    if wrong_side == "journal":
        state = _read(journal)
        state["campaign_binding"]["campaign_id"] = "Pilot_1"
        journal.write_text(json.dumps(state))
    else:
        payload = torch.load(checkpoint, weights_only=False)
        payload["hook_state"]["expenditure_ledger"].pop("campaign_binding")
        torch.save(payload, checkpoint)
    before = {path: path.read_bytes() for path in (checkpoint, journal)}
    with pytest.raises(ValueError, match="provenance or scope differs|publication differs|published ledger"):
        experiment.validate_journal_resume(checkpoint, item.destination)
    assert all(path.read_bytes() == original for path, original in before.items())


def test_initialization_resumes_pending_plan_publication_without_rebuilding(initialization, monkeypatch):
    item = initialization
    actual = policy_activation.build_independent_policy_plan

    def interrupt(*_args, **_kwargs):
        raise OSError("synthetic interruption after campaign registration")

    monkeypatch.setattr(policy_activation, "build_independent_policy_plan", interrupt)
    with pytest.raises(OSError, match="synthetic interruption"):
        item.initialize()
    assert not (item.destination / "policy_plan.json").exists()
    before = {path: path.read_bytes() for path in item.destination.iterdir() if path.is_file()}
    monkeypatch.setattr(policy_activation, "build_independent_policy_plan", actual)
    receipt = item.initialize()
    assert receipt["generation_authorized"] is False
    assert len(item.builds) == 1
    assert all(path.read_bytes() == original for path, original in before.items())
    assert (item.destination / "policy_plan.json").exists()
    assert _read(item.destination / "policy_authorization_pending.json")["approved"] is False
    assert item.invocations == []
