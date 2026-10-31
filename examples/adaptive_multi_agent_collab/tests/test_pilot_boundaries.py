"""Offline fatal boundaries and read-only revised-condition accounting lineage."""

import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
import torch

from examples.adaptive_multi_agent_collab.learning.calibration import PilotObservation, SafetyCalibrationPlan, calibrate_safety
from examples.adaptive_multi_agent_collab.config import ResourceConfig, SafetyConfig
from examples.adaptive_multi_agent_collab.providers.resources import AccountingFailure, ExpenditureLedger
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment


@pytest.mark.parametrize(
    "status,sticky", [("accounting-failed", False), ("execution-failed", True), ("budget-exhausted", False)]
)
def test_fatal_pilot_preserves_partial_record_and_never_initializes_next_query(tmp_path, status, sticky):
    trainer, hooks, _, _, _ = _build_offline_experiment(pilot_count=3)
    hooks.expenditure_ledger = ExpenditureLedger(trainer.config.resources)
    seen = []

    async def run(example, **_):
        seen.append(example.example_id)
        if sticky:
            hooks.expenditure_ledger.halt("original provider identity mismatch")
        return SimpleNamespace(
            example=example,
            sessions=(),
            call_durations=(0.2,),
            call_records=(
                {
                    "logical_call": {"call_type": "initial"},
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "monetary_cost": 0.001,
                        "paid_monetary_cost": None,
                    },
                    "error": "original provider identity mismatch",
                },
            ),
            completed=False,
            error="original provider identity mismatch",
            status=status,
        )

    hooks.runtime_factory = lambda *_: SimpleNamespace(run=run)
    path = tmp_path / "checkpoint.pt"
    state = asyncio.run(trainer.run_pilot(hooks, checkpoint_path=path))
    assert seen == ["pilot-0"] and state.phase == "failed" and not state.pilot_complete
    report = hooks.safety_record.to_dict()
    assert report["status"] == "incomplete" and not report["human_accepted"]
    assert report["planned_workflow_attempts"] == 3 and report["attempted_workflows"] == 1
    assert report["completed_workflows"] == report["completed_sessions"] == 0
    assert report["observations"][0]["error"] == "original provider identity mismatch"
    assert report["observations"][0]["call_records"][0]["usage"]["paid_monetary_cost"] is None
    payload = torch.load(path, weights_only=False)
    assert payload["hook_state"]["safety_record"]["status"] == "incomplete"
    assert payload["request_boundary_pending"] is False
    resumed, fresh, _, _, _ = _build_offline_experiment(pilot_count=3)
    fresh.expenditure_ledger = ExpenditureLedger(resumed.config.resources)
    before = copy.deepcopy(resumed.alpha.state_dict())
    with pytest.raises(ValueError, match="halted"):
        resumed.resume_from_checkpoint(path, hooks=fresh)
    assert all(torch.equal(value, resumed.alpha.state_dict()[key]) for key, value in before.items())
    assert not fresh.pilot_observations


def test_recoverable_failed_workflows_finish_attempt_coverage_without_claiming_success():
    trainer, hooks, _, _, _ = _build_offline_experiment(pilot_count=2)
    seen = []

    async def run(example, **_):
        seen.append(example.example_id)
        return SimpleNamespace(
            example=example,
            sessions=(),
            call_durations=(),
            call_records=(),
            completed=False,
            error="schema repair exhausted",
            status="initialization-failed",
        )

    hooks.runtime_factory = lambda *_: SimpleNamespace(run=run)
    state = asyncio.run(trainer.run_pilot(hooks))
    report = hooks.safety_record.to_dict()
    assert state.pilot_complete and seen == ["pilot-0", "pilot-1"]
    assert report["attempted_all_queries"] and report["stage_complete"]
    assert report["completed_workflows"] == report["completed_sessions"] == 0
    assert not report["human_accepted"] and hooks.safety_record.requires_review
    assert report["execution_counters"]["fallbacks"] is None
    assert report["coverage_by_call_type"]["tie"]["valid"] is None


def test_incomplete_last_attempt_cannot_be_mislabeled_completed_or_accepted():
    plan = SafetyCalibrationPlan(("only",), 42)
    observation = PilotObservation(
        "only", (), (), (), False, status="accounting-failed", operational_halt={"reason": "missing native usage"}
    )
    record = calibrate_safety(plan, (observation,), SafetyConfig())
    assert record.attempted_all_queries and not record.execution_complete
    with pytest.raises(ValueError, match="incomplete or halted"):
        record.accept_review({}, condition_fingerprint="a" * 64)


@pytest.mark.parametrize("command", ["pilot", "train", "compatibility"])
def test_cli_incomplete_stage_returns_nonzero_and_prints_preserved_result(monkeypatch, capsys, command):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import experiment

    async def failed(*_, **__):
        return {
            "phase": "failed",
            "status": "failed",
            "stage_complete": False,
            "execution_failures": [{"error": "original accounting error"}],
        }

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(
        experiment,
        {"pilot": "pilot_experiment", "train": "run_experiment", "compatibility": "compatibility_experiment"}[command],
        failed,
    )
    args = [command, "fixture.json", "--output", "fixture", "--authorization", "pending.json"]
    if command == "train":
        args += ["--resume", "fixture.pt"]
    assert cli.main(args) == 1
    assert json.loads(capsys.readouterr().out)["execution_failures"][0]["error"] == "original accounting error"


def test_reconciliation_on_disposable_copy_cannot_clear_halt_or_rewrite_request_identity(tmp_path):
    """Only synthetic local state is settled; production artifacts are never loaded here."""
    ledger = ExpenditureLedger(ResourceConfig(pilot_budget=5, currency="USD"))
    ledger.stage = "pilot"
    ledger.reserve(0.02, kind="initial", model="synthetic", metadata={"generation_id": "fixture-receipt"})
    ledger.halt("fixture unresolved charge")
    path = tmp_path / "disposable-ledger.json"
    path.write_text(json.dumps(ledger.state_dict()))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    isolated = ExpenditureLedger(ledger.limits)
    isolated.load_state_dict(json.loads(path.read_text()))
    isolated.settle(
        isolated.entries[0], {"generation_id": "fixture-receipt", "total_cost": 0.01}, status="completed", elapsed=0.1
    )
    assert isolated.stage_summary("pilot")["unresolved"] == 0
    with pytest.raises(AccountingFailure, match="halted"):
        isolated.assert_dispatch_allowed()
    changed = copy.deepcopy(isolated.state_dict())
    changed["entries"][0]["generation_id"] = "different-physical-request"
    with pytest.raises(ValueError, match="physical request identity"):
        isolated.load_state_dict(changed)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_old_checkpoint_schema_rejected_before_mutable_restore(tmp_path):
    trainer, hooks, _, _, _ = _build_offline_experiment()
    trainer.hooks = hooks
    path = tmp_path / "old.pt"
    trainer.save_checkpoint(path)
    payload = torch.load(path, weights_only=False)
    payload["schema_version"] = 9
    torch.save(payload, path)
    before = copy.deepcopy(trainer.alpha.state_dict())
    with pytest.raises(ValueError, match="unsupported checkpoint schema"):
        trainer.resume_from_checkpoint(path, hooks=hooks)
    assert all(torch.equal(value, trainer.alpha.state_dict()[key]) for key, value in before.items())


def test_workflow_protocol_change_invalidates_cache_condition_and_approval(monkeypatch):
    from examples.adaptive_multi_agent_collab.workflows import prompts
    from examples.adaptive_multi_agent_collab.operations.preflight import (
        stage_authorization_template,
        validate_stage_authorization,
    )

    _, hooks, _, _, _ = _build_offline_experiment()
    saved = hooks.state_dict()
    plan = hooks.plans["routing"]
    previous = hooks._cache_key(plan)
    monkeypatch.setattr(prompts, "PROTOCOL_ID", "a-different-fixed-phase-contract")
    assert hooks._cache_key(plan) != previous
    with pytest.raises(ValueError, match="incompatible experiment"):
        hooks.validate_state(saved)
    bindings: dict[str, Any] = dict(
        stage="pilot",
        manifest_fingerprint="a" * 64,
        condition_fingerprint="b" * 64,
        resource_profile_fingerprint="c" * 64,
        approved_budget_usd=5,
    )
    old = {
        **stage_authorization_template(**bindings),
        "protocol": "experiment_stage_authorization_v1",
        "decision": "approve_stage",
        "reviewer": "fixture",
        "rationale": "old condition only",
    }
    with pytest.raises(ValueError, match="differs from the exact"):
        validate_stage_authorization(old, **bindings)


def test_pilot_report_preserves_completed_and_interrupted_session_evidence():
    from examples.adaptive_multi_agent_collab.learning.calibration import collect_pilot_observation
    from examples.adaptive_multi_agent_collab.models.policies import RoutingRolloutIdentity
    from examples.adaptive_multi_agent_collab.infrastructure.randomness import GenerationContext
    from examples.adaptive_multi_agent_collab.schemas import RoutingMode

    _, hooks, _, _, _ = _build_offline_experiment()
    example = hooks.dataset.train[0]
    trajectory = asyncio.run(
        hooks._runtime().run(
            example,
            routing_mode=RoutingMode.TRAIN,
            routing_identity=RoutingRolloutIdentity(42, example.example_id, 0),
            generation_context=GenerationContext(42, "pilot", example.example_id),
            partition="train",
            fixed_horizon=3,
        )
    )
    partial = tuple(trajectory.sessions[1].traces)
    trajectory.sessions = trajectory.sessions[:1]
    trajectory.failed_session_traces = partial
    trajectory.completed = False
    trajectory.status = "accounting-failed"
    trajectory.error = "native usage unavailable after completed prefix"
    trajectory.operational_halt = {"reason": trajectory.error}
    observation = collect_pilot_observation(trajectory)
    report = calibrate_safety(
        SafetyCalibrationPlan((example.example_id,), 42), (observation,), SafetyConfig()
    ).to_dict()
    assert report["completed_sessions"] == 1 and report["partial_sessions"] == 1
    assert report["completed_workflows"] == 0 and not report["stage_complete"]
    assert (
        report["observations"][0]["completed_session_records"][0]["weighted_answer"]
        == trajectory.sessions[0].weighted_answer
    )
    assert report["observations"][0]["partial_session_traces"]
    assert report["observations"][0]["call_records"] == trajectory.call_records
    assert report["execution_counters"]["available"]
    json.dumps(report, allow_nan=False)
