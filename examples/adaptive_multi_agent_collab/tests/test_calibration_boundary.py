"""Calibration-only control publishes checkpoint zero without training or replay."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import asdict, replace

import pytest
import torch

from examples.adaptive_multi_agent_collab.learning.calibration import PilotObservation, calibrate_safety
from examples.adaptive_multi_agent_collab.operations.preflight import CALIBRATION_SCOPE
from examples.adaptive_multi_agent_collab.tests.test_bootstrap_boundary import assert_optimizer_equal
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import (
    BoundaryInterrupted, Hooks, config_for, make_trainer,
)
from examples.adaptive_multi_agent_collab.tests.test_workflow_recovery import _hooks
from examples.adaptive_multi_agent_collab.tests.test_bootstrap_authorization import accepted_fixture
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _stage_artifact


class CalibrationHooks(Hooks):
    def require_development(self, *, calibration_only=False):
        assert calibration_only


def at_boundary(trainer):
    trainer.state.phase = "calibration"
    trainer.state.pilot_complete = True
    trainer.state.bootstrap_complete = {"weighting": True, "routing": True}
    return trainer


def frozen_state(trainer):
    return copy.deepcopy({
        "alpha": trainer.alpha.state_dict(), "weighting": trainer.weighting.state_dict(),
        "alpha_optimizer": trainer.alpha_optimizer.state_dict(),
        "weighting_optimizer": trainer.weighting_optimizer.state_dict(),
        "gamma": trainer.gamma.to_dict(), "updates": trainer.state.updates,
        "attempts": trainer.state.attempts, "workflow_attempt_counter": trainer.state.workflow_attempt_counter,
    })


def assert_frozen(trainer, before):
    for name in ("alpha", "weighting"):
        for key, value in getattr(trainer, name).state_dict().items():
            torch.testing.assert_close(value, before[name][key], atol=0, rtol=0)
    for name in ("alpha_optimizer", "weighting_optimizer"):
        assert_optimizer_equal(getattr(trainer, name).state_dict(), before[name])
    assert trainer.gamma.to_dict() == before["gamma"]
    for name in ("updates", "attempts", "workflow_attempt_counter"):
        assert getattr(trainer.state, name) == before[name]


def test_calibration_only_preserves_nonempty_adam_and_stops_at_checkpoint_zero(tmp_path):
    trainer = make_trainer(config_for())
    # Inherited moments are intentionally nonzero; calibration must not advance them.
    for name in ("alpha", "weighting"):
        module = getattr(trainer, name)
        optimizer = torch.optim.Adam(module.parameters(), lr=.01)
        module.value.square().add(module.value).backward()
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        setattr(trainer, f"{name}_optimizer", optimizer)
    at_boundary(trainer)
    trainer.state.updates.update(weighting=10, routing=10)
    trainer.state.attempts.update(weighting=43, routing=10)
    trainer.state.workflow_attempt_counter = 53
    before = frozen_state(trainer)
    path, hooks = tmp_path / "checkpoint.pt", CalibrationHooks()
    asyncio.run(trainer.fit(hooks, checkpoint_path=path, calibration_only=True))
    assert hooks.events == ["calibration", "system"]
    assert trainer.state.phase == "weighting" and trainer.state.calibration_complete
    assert trainer.state.outer_cycle == 0 and trainer.state.eligible_checkpoints == [0]
    assert len(trainer.state.system_validation_records) == 1 and trainer.best_system["cycle"] == 0
    assert not trainer.prompt_optimizer.proposer.calls
    assert_frozen(trainer, before)
    payload = torch.load(path, weights_only=False)
    assert payload["request_boundary_pending"] is False
    original_bytes = path.read_bytes()
    state_before = copy.deepcopy(asdict(trainer.state))
    asyncio.run(trainer.fit(hooks, checkpoint_path=path, calibration_only=True))
    assert path.read_bytes() == original_bytes and asdict(trainer.state) == state_before
    assert hooks.events == ["calibration", "system"]


@pytest.mark.parametrize("phase", ["unstarted", "pilot_complete", "weighting_bootstrap", "routing_bootstrap",
                                   "routing", "prompt", "system", "complete", "failed", "weighting"])
def test_calibration_only_rejects_unrelated_progress_before_mutation(tmp_path, phase):
    trainer = at_boundary(make_trainer(config_for()))
    trainer.state.phase = phase
    original = copy.deepcopy(asdict(trainer.state))
    with pytest.raises(ValueError, match="calibration-only"):
        asyncio.run(trainer.fit(CalibrationHooks(), checkpoint_path=tmp_path / "no-write", calibration_only=True))
    assert asdict(trainer.state) == original and not (tmp_path / "no-write").exists()


def production_fixture(*, failure="none", every=False):
    trainer, hooks, calls = _hooks(failure=failure, every=every)
    at_boundary(trainer)
    observation = PilotObservation("train-a", (4,), (1.,), (2.,), True, 1, ())
    hooks.pilot_observations = [observation]
    hooks.safety_record = calibrate_safety(hooks.pilot_plan, (observation,), trainer.config.safety)
    hooks.stage_authorizations["development"] = {"scope": CALIBRATION_SCOPE}
    # This fixture is network blocked and explicitly has authorization_required=False.
    # The scope still exercises the production evaluator's cached-only baseline gate.
    return trainer, hooks, calls


def test_production_calibration_only_reuses_shared_prefixes_for_checkpoint_zero(tmp_path):
    trainer, hooks, calls = production_fixture()
    before = frozen_state(trainer)
    path = tmp_path / "checkpoint.pt"
    asyncio.run(trainer.fit(hooks, checkpoint_path=path, calibration_only=True))
    assert len(calls) == len(hooks.plans["system"].ordered_example_ids) == 2
    assert all(call[1]["generation_context"].domain == "sysval" for call in calls)
    assert trainer.state.eligible_checkpoints == [0] and trainer.best_system["cycle"] == 0
    assert trainer.state.system_validation_records[0] == hooks.termination_record.selected_evaluation.to_dict()
    assert len(hooks.calibration_query_cache) == 1
    assert_frozen(trainer, before)
    # Restore actual hook/cache/checkpoint state. No second calibration or baseline call.
    resumed, restored, fresh_calls = production_fixture()
    restored.stage_authorizations = {}
    # Synthetic scope has no approval fields, so omit it only from the fixture
    # checkpoint; real authorization persistence is covered by dedicated scope tests.
    hooks.stage_authorizations = {}
    trainer.best_system["hook_state"]["stage_authorizations"] = {}
    trainer.save_checkpoint(path)
    resumed.resume_from_checkpoint(path, hooks=restored)
    asyncio.run(resumed.fit(restored, checkpoint_path=path, calibration_only=True))
    assert not fresh_calls and resumed.state.eligible_checkpoints == [0]
    assert_frozen(resumed, before)


def test_partial_calibration_publication_resumes_queries_without_replacement_replenishment(tmp_path):
    trainer, hooks, calls = production_fixture(failure="cost")
    hooks.stage_authorizations = {}
    trainer.hooks, trainer.checkpoint_path = hooks, tmp_path / "partial.pt"
    original_publish = hooks._publish_recovery
    async def interrupt_after_query(**kwargs):
        await original_publish(**kwargs)
        if kwargs.get("safe_boundary"):
            raise BoundaryInterrupted("one complete shared query published")
    hooks._publish_recovery = interrupt_after_query
    with pytest.raises(BoundaryInterrupted):
        asyncio.run(trainer.fit(hooks, checkpoint_path=trainer.checkpoint_path, calibration_only=True))
    assert len(calls) == 2  # One original + one permitted replacement, for all candidates.
    saved = torch.load(trainer.checkpoint_path, weights_only=False)
    assert saved["request_boundary_pending"] is False and not saved["trainer_state"]["calibration_complete"]
    assert len(next(iter(saved["hook_state"]["calibration_query_cache"].values()))) == 1
    resumed, restored, fresh_calls = production_fixture(failure="cost")
    restored.stage_authorizations = {}
    resumed.resume_from_checkpoint(trainer.checkpoint_path, hooks=restored)
    asyncio.run(resumed.fit(restored, checkpoint_path=trainer.checkpoint_path, calibration_only=True))
    assert len(fresh_calls) == 2 and len(restored.recovery_slots) == 2
    assert all(len(slot["executions"]) == 2 for slot in restored.recovery_slots.values())
    assert calls[0][0] != fresh_calls[0][0]
    assert resumed.state.phase == "weighting" and resumed.state.eligible_checkpoints == [0]


def test_unavailable_candidate_cost_retains_all_queries_and_never_selects_checkpoint_zero(tmp_path):
    trainer, hooks, calls = production_fixture(failure="cost", every=True)
    hooks.stage_authorizations = {}
    asyncio.run(trainer.fit(hooks, checkpoint_path=tmp_path / "failed.pt", calibration_only=True))
    assert len(calls) == 4 and len(hooks.recovery_slots) == 2
    assert trainer.state.phase == "failed" and not trainer.state.calibration_complete
    assert hooks.termination_record is None and trainer.best_system is None
    assert not trainer.state.system_validation_records and not trainer.state.eligible_checkpoints
    assert all(len(record["examples"]) == 2 for record in hooks.evaluation_records)
    assert "cost is unavailable" in trainer.state.execution_failures[-1]["error"]


def test_scoped_baseline_cache_miss_never_launches_duplicate_workflows():
    trainer, hooks, calls = production_fixture()
    trainer.state.phase = "system_baseline"
    with pytest.raises(ValueError, match="reuse its matching selected prefixes"):
        asyncio.run(hooks.system_validation())
    assert not calls


def test_scoped_baseline_rejects_changed_cached_prefix_before_dispatch():
    trainer, hooks, calls = production_fixture()
    asyncio.run(hooks.calibrate_termination())
    trainer.state.phase = "system_baseline"
    key = hooks._cache_key(hooks.plans["system"])
    selected = hooks.evaluation_cache[key]
    damaged = replace(selected.examples[0], normalized_cost=99.)
    hooks.evaluation_cache[key] = replace(selected, examples=(damaged, *selected.examples[1:]))
    count = len(calls)
    with pytest.raises(ValueError, match="reuse its matching selected prefixes"):
        asyncio.run(hooks.system_validation())
    assert len(calls) == count == 2


def scoped_fixture(tmp_path):
    trainer, hooks, models, _ = accepted_fixture(bootstrap=False)
    at_boundary(trainer)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"references": {"forecast": {"sha256": "f" * 64}}}))
    plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
    hooks.calibration_lineage = {"plan_path": str(plan), "plan_sha256": plan_sha}
    approval = _stage_artifact(hooks, "development", execution_scope=CALIBRATION_SCOPE,
        calibration_plan_sha256=plan_sha, calibration_forecast_sha256="f" * 64)
    hooks.authorize_stage(approval)
    return trainer, hooks, models


@pytest.mark.parametrize("operation", ["training", "prompt", "prompt_validation", "component_validation"])
def test_calibration_scope_cannot_dispatch_any_other_work(tmp_path, operation):
    trainer, hooks, models = scoped_fixture(tmp_path)
    hooks.require_development(calibration_only=True)
    calls = {
        "training": lambda: hooks.trajectory("weighting", routing_identity=None),
        "prompt": lambda: hooks.prompt_evidence("tie"),
        "prompt_validation": lambda: hooks._evaluate(hooks.plans["prompt"]),
        "component_validation": lambda: hooks.validate("routing"),
    }
    with pytest.raises(ValueError, match="calibration-only"):
        asyncio.run(calls[operation]())
    assert not models[0].calls and not any(hooks.sample_counters.values())
    trainer.state.phase = "weighting"
    with pytest.raises(ValueError, match="calibration-only"):
        hooks.require_development()


def test_calibration_scoped_approval_detects_modified_plan_before_dispatch(tmp_path):
    _, hooks, models = scoped_fixture(tmp_path)
    hooks.require_development(calibration_only=True)
    with open(hooks.calibration_lineage["plan_path"], "a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="recorded hash"):
        hooks.require_development(calibration_only=True)
    assert not models[0].calls
