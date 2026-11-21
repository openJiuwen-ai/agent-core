"""Detached diagnostics preserve the actual numerical update and durable boundaries."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict
import json
import random
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from examples.adaptive_multi_agent_collab.learning import training
from examples.adaptive_multi_agent_collab.tests.test_bootstrap_boundary import (
    BootstrapHooks,
    InterruptBootstrap,
    assert_optimizer_equal,
    bootstrap_trainer,
)
from examples.adaptive_multi_agent_collab.tests.test_mixed_training import fixture as routing_fixture
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import BoundaryInterrupted, config_for


def assert_scalar_record(record, *, loss, count, denominator, status, reason=None):
    assert record["loss"] == pytest.approx(loss) if loss is not None else record["loss"] is None
    assert record["example_count"] == count
    assert record["normalization_denominator"] == denominator
    assert record["status"] == status
    if reason is not None:
        assert record["reason"] == reason
    # No graph, tensors, nonfinite number or raw workflow evidence enters diagnostics.
    json.dumps(record, allow_nan=False)


def weighting_sessions(parameter):
    logits = torch.stack((parameter, -parameter))
    return [
        SimpleNamespace(resolved_answers=("A", "B"), weights=logits.softmax(0), weight_logits=logits),
        SimpleNamespace(resolved_answers=("B", "A"), weights=logits.softmax(0), weight_logits=logits),
        SimpleNamespace(resolved_answers=("A", "A"), weights=logits.softmax(0), weight_logits=logits),
        SimpleNamespace(resolved_answers=("B", "B"), weights=logits.softmax(0), weight_logits=logits),
    ]


@pytest.mark.parametrize("component", ["weighting", "routing"])
def test_record_is_actual_preupdate_loss_with_identical_graph_rng_and_adam(component, monkeypatch):
    """Compare the production update with and without its optional scalar recorder."""
    snapshots = []
    original_step = training._numerical_step
    actual_losses = []
    differentiations = []

    def observed_step(optimizer, loss, gradient_clip_norm):
        actual_losses.append(float(loss.detach()))
        loss.register_hook(lambda gradient: differentiations.append(gradient.detach().clone()))
        return original_step(optimizer, loss, gradient_clip_norm)

    monkeypatch.setattr(training, "_numerical_step", observed_step)
    objective_name = "weighting_loss_from_sessions" if component == "weighting" else "mixed_alpha_loss"
    original_objective = getattr(training, objective_name)
    objective_calls = []

    def observed_objective(*args, **kwargs):
        objective_calls.append(None)
        return original_objective(*args, **kwargs)

    monkeypatch.setattr(training, objective_name, observed_objective)
    for enabled in (False, True):
        random.seed(73)
        np.random.seed(74)
        torch.manual_seed(75)
        record = {} if enabled else None
        if component == "weighting":
            model = torch.nn.Linear(1, 1)
            parameter = model.weight
            with torch.no_grad():
                parameter.fill_(0.2)
            sessions = weighting_sessions(parameter.reshape(()))
            optimizer = torch.optim.Adam([parameter], lr=0.01)
            result = training.update_weighting(model, optimizer, sessions, "A", loss_record=record)
        else:
            parameter, _, values = routing_fixture()
            # Two selected decisions; the third capacity-forced zero does not enter 2*M.
            optimizer = torch.optim.Adam([parameter], lr=0.01)
            result = training.update_routing(optimizer, **values, loss_record=record)
        snapshots.append({
            "parameter": parameter.detach().clone(),
            "gradient": parameter.grad.clone(),
            "optimizer": copy.deepcopy(optimizer.state_dict()),
            "python_rng": random.getstate(),
            "numpy_rng": np.random.get_state(),
            "torch_rng": torch.get_rng_state().clone(),
            "result": result,
            "record": record,
        })
    first, second = snapshots
    assert actual_losses == [first["result"], second["result"]]
    assert len(objective_calls) == len(differentiations) == 2  # Exactly once per update.
    assert actual_losses[0] == actual_losses[1]
    assert_scalar_record(second["record"], loss=actual_losses[1], count=2,
                         denominator=2 if component == "weighting" else 4, status="updated")
    torch.testing.assert_close(first["parameter"], second["parameter"], rtol=0, atol=0)
    torch.testing.assert_close(first["gradient"], second["gradient"], rtol=0, atol=0)
    assert_optimizer_equal(first["optimizer"], second["optimizer"])
    assert first["python_rng"] == second["python_rng"]
    assert first["numpy_rng"][0] == second["numpy_rng"][0]
    np.testing.assert_array_equal(first["numpy_rng"][1], second["numpy_rng"][1])
    assert first["numpy_rng"][2:] == second["numpy_rng"][2:]
    assert torch.equal(first["torch_rng"], second["torch_rng"])


@pytest.mark.parametrize("component", ["weighting", "routing"])
@pytest.mark.parametrize("kind", ["empty", "frozen", "disconnected", "nonfinite_loss", "nonfinite_gradient"])
def test_skips_and_numerical_failures_retain_loss_without_adam_advance(component, kind):
    class InfiniteGradient(torch.autograd.Function):
        @staticmethod
        def forward(ctx, value):
            return value.clone()

        @staticmethod
        def backward(ctx, gradient):
            return torch.full_like(gradient, float("inf"))

    model = torch.nn.Linear(1, 1)
    parameter = torch.nn.Parameter(torch.tensor(0.2))
    optimizer = torch.optim.Adam([parameter], lr=0.01)
    # Existing moments must not advance on any subsequent skipped/failed attempt.
    parameter.square().backward()
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    before = parameter.detach().clone(), copy.deepcopy(optimizer.state_dict())
    value = parameter
    if kind == "frozen":
        value = parameter.detach()
    elif kind == "disconnected":
        value = torch.tensor(0.2, requires_grad=True)
    elif kind == "nonfinite_loss":
        value = parameter * float("nan")
    elif kind == "nonfinite_gradient":
        value = InfiniteGradient.apply(parameter)
    record = {}
    if component == "weighting":
        sessions = weighting_sessions(value)
        if kind == "empty":
            sessions = sessions[2:]
        call = lambda: training.update_weighting(model, optimizer, sessions, "A", loss_record=record)
        count, denominator = (0, 0) if kind == "empty" else (2, 2)
    else:
        _, _, values = routing_fixture()
        decisions = values["actions"][0].decisions
        for decision in decisions:
            decision.q_value = value
        if kind == "empty":
            values["actions"][0].decisions = decisions[-1:]
        call = lambda: training.update_routing(optimizer, **values, loss_record=record)
        count, denominator = (0, 0) if kind == "empty" else (2, 4)
    if kind.startswith("nonfinite"):
        with pytest.raises(training.NumericalFailure):
            call()
        assert record["status"] == "failed" and record["reason"] == "numerical_failure"
    else:
        assert call() is None
        assert record["status"] == "skipped"
    assert record["example_count"] == count and record["normalization_denominator"] == denominator
    assert (record["loss"] is None) == (kind in {"empty", "nonfinite_loss"})
    if kind in {"frozen", "disconnected"}:
        assert record["reason"] == "no_trainable_gradient" and record["loss"] > 0
    json.dumps(record, allow_nan=False)
    torch.testing.assert_close(parameter, before[0], rtol=0, atol=0)
    assert_optimizer_equal(optimizer.state_dict(), before[1])


def test_missing_one_routing_target_records_null_and_skips_entire_pool():
    parameter, tag, values = routing_fixture()
    optimizer = torch.optim.Adam([parameter], lr=0.01)
    values["conversation_costs"][0][tag] = None
    record = {}
    with pytest.raises(training.MissingRoutingTarget):
        training.update_routing(optimizer, **values, loss_record=record)
    assert_scalar_record(record, loss=None, count=2, denominator=4, status="skipped")
    assert record["reason"] and not optimizer.state and parameter.grad is None


def test_trainer_publishes_loss_and_counters_before_separate_validation(tmp_path, monkeypatch):
    trainer = bootstrap_trainer(config_for(bootstrap=2))
    hooks = BootstrapHooks()
    calls = []
    original_step = training._numerical_step

    def observe(optimizer, loss, gradient_clip_norm):
        calls.append(float(loss.detach()))
        return original_step(optimizer, loss, gradient_clip_norm)

    monkeypatch.setattr(training, "_numerical_step", observe)
    path = tmp_path / "bootstrap.pt"
    asyncio.run(trainer.fit(hooks, checkpoint_path=path, bootstrap_only=True))
    records = trainer.state.training_loss_records
    assert len(records) == len(calls) == 4
    assert [record["loss"] for record in records] == calls
    assert [record["workflow_attempt_id"] for record in records] == [0, 1, 2, 3]
    for index, record in enumerate(records):
        component = "weighting" if index < 2 else "routing"
        local_index = index % 2
        assert record["phase"] == component + "_bootstrap"
        assert record["component"] == component and record["query_id"] == "train-query"
        assert record["phase_attempt"] == record["component_attempt"] == local_index + 1
        assert record["update_index_before"] == record["phase_update_index_before"] == local_index
        assert record["update_index_after"] == record["phase_update_index_after"] == local_index + 1
        assert record["outer_cycle"] == 0 and record["status"] == "updated"
    assert trainer.state.validation_history["weighting"] != [record["loss"] for record in records[:2]]
    assert hooks.events == [
        "validate:weighting", "weighting", "validate:weighting", "weighting", "validate:weighting",
        "validate:routing", "routing", "validate:routing", "routing", "validate:routing",
    ]
    payload = torch.load(path, weights_only=False)
    assert payload["trainer_state"]["training_loss_records"] == records
    assert payload["trainer_state"]["updates"] == {"weighting": 2, "routing": 2, "prompt": 0}
    assert payload["request_boundary_pending"] is False
    # Records are only a trainer reporting field, never synthetic execution inputs.
    assert "training_loss_records" not in json.dumps(hooks.state_dict())


class OutcomeHooks(BootstrapHooks):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    async def trajectory(self, component, split, *, routing_identity):
        trajectory = await super().trajectory(component, split, routing_identity=routing_identity)
        if component == "weighting":
            if self.mode == "uninformative":
                trajectory.sessions[0].resolved_answers = ("A", "A")
            elif self.mode == "frozen":
                trajectory.sessions[0].weights = trajectory.sessions[0].weights.detach()
                trajectory.sessions[0].weight_logits = trajectory.sessions[0].weight_logits.detach()
            elif self.mode == "incomplete":
                trajectory.completed = False
                trajectory.status = "initialization-failed"
                trajectory.error = "synthetic initialization failure"
                trajectory.sessions = ()
            elif self.mode == "numerical_failure":
                trajectory.sessions[0].weight_logits = trajectory.sessions[0].weight_logits * float("nan")
            elif self.mode == "fatal":
                trajectory.completed = False
                trajectory.status = "accounting-failed"
                trajectory.error = "synthetic fatal accounting stop"
        elif self.mode == "missing_target":
            trajectory.sessions[0].future_return = None
        elif self.mode == "ineligible":
            trajectory.routing_eligible = False
        return trajectory


@pytest.mark.parametrize("mode,component", [
    ("uninformative", "weighting"), ("frozen", "weighting"), ("incomplete", "weighting"),
    ("missing_target", "routing"), ("ineligible", "routing"),
])
def test_trainer_loss_record_explains_every_completed_attempt_skip(tmp_path, mode, component):
    trainer, hooks = bootstrap_trainer(config_for(bootstrap=1)), OutcomeHooks(mode)
    path = tmp_path / "skipped.pt"
    asyncio.run(trainer.fit(hooks, checkpoint_path=path, bootstrap_only=True))
    records = [item for item in trainer.state.training_loss_records if item["component"] == component]
    assert len(records) == 1
    record = records[0]
    assert record["status"] in {"skipped", "failed"} and record["reason"]
    assert record["loss"] is None if mode != "frozen" else record["loss"] > 0
    assert record["update_index_before"] == record["update_index_after"] == 0
    assert trainer.state.attempts[component] == 1 and trainer.state.updates[component] == 0
    optimizer = trainer.weighting_optimizer if component == "weighting" else trainer.alpha_optimizer
    assert not optimizer.state
    assert torch.load(path, weights_only=False)["trainer_state"]["training_loss_records"] == trainer.state.training_loss_records


@pytest.mark.parametrize("mode", ["numerical_failure", "fatal"])
def test_failed_attempt_record_is_durable_with_unchanged_optimizer(tmp_path, mode):
    trainer, hooks = bootstrap_trainer(config_for(bootstrap=1)), OutcomeHooks(mode)
    path = tmp_path / "failed.pt"
    asyncio.run(trainer.fit(hooks, checkpoint_path=path, bootstrap_only=True))
    if mode == "fatal":
        assert trainer.state.phase == "failed"
        assert trainer.state.execution_failures[-1]["error"] == "synthetic fatal accounting stop"
        assert "routing" not in hooks.events
    records = trainer.state.training_loss_records
    record = records[0]
    assert record["component"] == "weighting" and record["status"] == "failed"
    assert record["loss"] is None and record["reason"]
    assert record["update_index_before"] == record["update_index_after"] == 0
    assert trainer.state.updates["weighting"] == 0 and trainer.state.attempts["weighting"] == 1
    assert not trainer.weighting_optimizer.state
    payload = torch.load(path, weights_only=False)
    assert payload["trainer_state"]["training_loss_records"] == records
    assert payload["trainer_state"]["updates"]["weighting"] == 0


@pytest.mark.parametrize("interruption", ["weighting_update", "routing_exit_validation"])
def test_checkpoint_resume_has_one_record_per_workflow_and_does_not_repeat_validation(tmp_path, interruption):
    config = config_for(bootstrap=2)
    full, hooks = bootstrap_trainer(config), BootstrapHooks()
    asyncio.run(full.fit(hooks, checkpoint_path=tmp_path / "full.pt", bootstrap_only=True))
    path = tmp_path / "interrupted.pt"
    first = bootstrap_trainer(config, trainer_type=InterruptBootstrap, interruption=interruption)
    with pytest.raises(BoundaryInterrupted):
        asyncio.run(first.fit(BootstrapHooks(), checkpoint_path=path, bootstrap_only=True))
    first_payload = torch.load(path, weights_only=False)
    durable_records = copy.deepcopy(first_payload["trainer_state"]["training_loss_records"])
    assert len(durable_records) == sum(first_payload["trainer_state"]["updates"].values())
    resumed, resumed_hooks = bootstrap_trainer(config), BootstrapHooks()
    resumed.resume_from_checkpoint(path, hooks=resumed_hooks)
    asyncio.run(resumed.fit(resumed_hooks, checkpoint_path=path, bootstrap_only=True))
    assert resumed.state.training_loss_records[:len(durable_records)] == durable_records
    assert asdict(resumed.state) == asdict(full.state)
    assert resumed_hooks.state_dict() == hooks.state_dict()
    assert len({record["workflow_attempt_id"] for record in resumed.state.training_loss_records}) == 4
    for name in ("alpha_optimizer", "weighting_optimizer"):
        assert_optimizer_equal(getattr(full, name).state_dict(), getattr(resumed, name).state_dict())


def test_prebootstrap_checkpoint_without_optional_loss_history_remains_loadable(tmp_path):
    config = config_for(bootstrap=1)
    trainer, hooks = bootstrap_trainer(config), BootstrapHooks()
    trainer.hooks = hooks
    hooks.bind(trainer)
    path = tmp_path / "existing-prebootstrap.pt"
    trainer.save_checkpoint(path)
    payload = torch.load(path, weights_only=False)
    del payload["trainer_state"]["training_loss_records"]
    training.CheckpointManager.write_payload(path, payload)
    before = path.read_bytes()
    resumed, resumed_hooks = bootstrap_trainer(config), BootstrapHooks()
    resumed.resume_from_checkpoint(path, hooks=resumed_hooks)
    assert resumed.state.training_loss_records == []
    assert path.read_bytes() == before  # Read-only compatibility, no historical rewrite.
    asyncio.run(resumed.fit(resumed_hooks, checkpoint_path=path, bootstrap_only=True))
    assert len(resumed.state.training_loss_records) == 2
    assert resumed.state.phase == "calibration" and not resumed.state.calibration_complete


def test_published_training_diagnostics_detach_from_state_and_keep_validation_separate(tmp_path):
    from examples.adaptive_multi_agent_collab.operations.experiment import training_diagnostics

    trainer = bootstrap_trainer(config_for(bootstrap=1))
    asyncio.run(trainer.fit(BootstrapHooks(), checkpoint_path=tmp_path / "checkpoint.pt", bootstrap_only=True))
    before = copy.deepcopy(asdict(trainer.state))
    report = training_diagnostics(trainer)
    assert json.loads(json.dumps(report, allow_nan=False))["training_losses"] == trainer.state.training_loss_records
    assert report["validation_history"] == trainer.state.validation_history
    assert report["phase"] == "calibration" and report["request_boundary_pending"] is False
    report["training_losses"][0]["loss"] = 999
    report["validation_history"]["weighting"].append(999)
    report["phase_records"].clear()
    assert asdict(trainer.state) == before


def test_cancelled_workflow_diagnostic_keeps_existing_unsafe_resume_block(tmp_path):
    class CancelledHooks(BootstrapHooks):
        async def trajectory(self, component, split, *, routing_identity):
            self.trainer._request_checkpoint()
            raise asyncio.CancelledError("synthetic operator stop during workflow")

    trainer, hooks = bootstrap_trainer(config_for(bootstrap=1)), CancelledHooks()
    path = tmp_path / "unsafe.pt"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(trainer.fit(hooks, checkpoint_path=path, bootstrap_only=True))
    assert trainer._request_boundary_pending is True
    assert trainer.state.attempts["weighting"] == 1 and trainer.state.updates["weighting"] == 0
    record = trainer.state.training_loss_records[0]
    assert record["status"] == "failed" and record["reason"] == "CancelledError"
    assert record["query_id"] is None and record["loss"] is None
    assert record["workflow_attempt_id"] == 0
    # Use the same existing asynchronous unsafe publisher as the stage wrapper.
    asyncio.run(trainer.checkpoint_async(safe_boundary=False))
    payload = torch.load(path, weights_only=False)
    assert payload["request_boundary_pending"] is True
    assert payload["trainer_state"]["training_loss_records"] == [record]
    resumed = bootstrap_trainer(config_for(bootstrap=1))
    with pytest.raises(ValueError, match="unresolved physical request boundary"):
        resumed.resume_from_checkpoint(path, hooks=BootstrapHooks())
