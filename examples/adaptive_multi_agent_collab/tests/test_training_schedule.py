"""Deterministic full-record training schedules, phase boundaries, and checkpoint preflight."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import os
import random
import stat
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from torch import nn

from examples.adaptive_multi_agent_collab.config import (
    Config,
    PhaseConfig,
    PromptPhaseConfig,
    TrainingConfig,
    SelectionConfig,
)
from examples.adaptive_multi_agent_collab.models.encoders import offline_encoder_configuration
from examples.adaptive_multi_agent_collab.learning.evaluation import (
    EvaluationPlan,
    EvaluationRecord,
    ExampleEvaluation,
    scoring_configuration,
)
from examples.adaptive_multi_agent_collab.workflows.prompts import PromptOccurrence, PromptProposal, seed_prompt_collection
from examples.adaptive_multi_agent_collab.schemas import ConversationTag, QueryExample, Scheme
from examples.adaptive_multi_agent_collab.learning.training import (
    CheckpointManager,
    InterleavedTrainer,
    PromptOptimizer,
    TrainerState,
)


class ScalarModule(nn.Module):
    def __init__(self, value=0.0):
        super().__init__()
        self.value = nn.Parameter(torch.tensor(float(value)))


class Proposer:
    def __init__(self):
        self.calls = []

    def metadata(self):
        return {"backend": "offline_schedule_fixture", "protocol": "single_proposal"}

    async def propose(self, spec, evidence, *, iteration_id):
        self.calls.append((spec.key, iteration_id))
        assert all(item.split == "train" for item in evidence)
        return PromptProposal(spec.template + "\nReusable improvement.")


def config_for(
    *,
    cycles=1,
    numerical=2,
    prompt_iterations=2,
    patience=3,
    bootstrap=1,
    attempts=None,
    outer_epsilon=1e-4,
    outer_patience=10,
):
    phase = PhaseConfig(
        max_updates=numerical, max_attempts=attempts or max(1, numerical), validation_interval=1, patience=10
    )
    return Config(
        n_agents=2,
        selection=SelectionConfig(1.0),
        encoders=offline_encoder_configuration(),
        training=TrainingConfig(
            bootstrap_weighting_updates=bootstrap,
            bootstrap_routing_updates=bootstrap,
            bootstrap_weighting_attempts=max(1, bootstrap),
            max_outer_cycles=cycles,
            weighting_phase=phase,
            routing_phase=phase,
            prompt_phase=PromptPhaseConfig(max_iterations=prompt_iterations, patience=patience),
            gradient_clip_norm=None,
            outer_min_improvement=outer_epsilon,
            outer_patience=outer_patience,
        ),
    )


def make_trainer(config, *, trainer_type=InterleavedTrainer, **kwargs):
    alpha, weighting = ScalarModule(), ScalarModule()
    return trainer_type(
        alpha=alpha,
        weighting=weighting,
        gamma=seed_prompt_collection(),
        alpha_optimizer=torch.optim.SGD(alpha.parameters(), lr=0.1, momentum=0.2),
        weighting_optimizer=torch.optim.SGD(weighting.parameters(), lr=0.1, momentum=0.2),
        prompt_optimizer=PromptOptimizer(Proposer()),
        config=config,
        answer_order=("A", "B"),
        **kwargs,
    )


class Hooks:
    """Serializable synthetic workflows with fresh autograd graphs on every training call."""

    def __init__(
        self, *, system_scores=(0.8,), failures=True, skip_weighting=False, wrong_objective=None, changing_plan=False
    ):
        self.system_scores = system_scores
        self.failures = failures
        self.skip_weighting = skip_weighting
        self.wrong_objective = wrong_objective
        self.changing_plan = changing_plan
        self.trainer = None
        self.events = []
        self.identities = []
        self.prompt_components = []
        self.parameter_inputs: dict[str, list] = {"weighting": [], "routing": []}
        self.freeze_checks = []
        self.validations = {"weighting": 0, "routing": 0, "prompt": 0, "system": 0}
        self.pilot_count = 0
        self.calibration_count = 0
        self.prompt_plan_count = 0
        self.example = QueryExample("train-query", "Pick", ("A", "B"), "A")

    def bind(self, trainer):
        self.trainer = trainer

    def state_dict(self):
        return copy.deepcopy(
            {
                "condition": "fixed-offline-dataset",
                "events": self.events,
                "identities": self.identities,
                "prompt_components": self.prompt_components,
                "parameter_inputs": self.parameter_inputs,
                "freeze_checks": self.freeze_checks,
                "validations": self.validations,
                "pilot_count": self.pilot_count,
                "calibration_count": self.calibration_count,
                "prompt_plan_count": self.prompt_plan_count,
            }
        )

    def validate_state(self, state):
        if state.get("condition") != "fixed-offline-dataset":
            raise ValueError("wrong dataset condition")
        for key in ("events", "identities", "prompt_components", "freeze_checks"):
            if not isinstance(state.get(key), list):
                raise ValueError("invalid sampling state")

    def load_state_dict(self, state):
        self.validate_state(state)
        for key, value in state.items():
            if key != "condition":
                setattr(self, key, copy.deepcopy(value))

    def restore_selected_state(self, state):
        self.validate_state(state)  # This synthetic hook has no mutable query sampler.

    def _frozen_evaluation(self):
        models = (self.trainer.alpha, self.trainer.weighting)
        result = all(not model.training and not next(model.parameters()).requires_grad for model in models)
        self.freeze_checks.append(result)
        assert result and not torch.is_grad_enabled()

    async def pilot_safety(self):
        self._frozen_evaluation()
        self.events.append("pilot")
        self.pilot_count += 1
        current = self.trainer.config
        self.trainer.apply_configuration(
            dataclasses.replace(current, safety=dataclasses.replace(current.safety, max_internal_rounds=12))
        )

    async def calibrate_termination(self):
        self._frozen_evaluation()
        self.events.append("calibration")
        self.calibration_count += 1
        current = self.trainer.config
        self.trainer.apply_configuration(
            dataclasses.replace(current, termination=dataclasses.replace(current.termination, max_sessions=2))
        )

    async def trajectory(self, component, split, *, routing_identity):
        assert split == "train" and torch.is_grad_enabled()
        alpha, weighting = self.trainer.alpha, self.trainer.weighting
        assert alpha.training and weighting.training
        assert alpha.value.requires_grad == (component == "routing")
        assert weighting.value.requires_grad == (component == "weighting")
        self.events.append(component)
        self.identities.append(dataclasses.asdict(routing_identity))
        module = weighting if component == "weighting" else alpha
        self.parameter_inputs[component].append(float(module.value.detach()))
        if component == "weighting":
            logits = torch.stack((weighting.value, -weighting.value))
            session = SimpleNamespace(
                resolved_answers=("A", "A") if self.skip_weighting else ("A", "B"),
                weights=torch.softmax(logits, dim=0),
                weight_logits=logits,
            )
            return SimpleNamespace(completed=True, example=self.example, sessions=(session,))
        conversation_tag = ConversationTag(1, 0, 1, Scheme.REVIEWER)
        decision = SimpleNamespace(
            edge=(0, 1),
            scheme=Scheme.REVIEWER,
            q_value=alpha.value,
            log_probability=nn.functional.logsigmoid(alpha.value),
            free=True,
            tag=conversation_tag,
        )
        session = SimpleNamespace(
            routing=SimpleNamespace(decisions=(decision,)), conversation_credits={conversation_tag: 0.4},
            routing_local_labels={conversation_tag: 1.}, routing_zero_labels={0: 1.},
            conversation_costs={conversation_tag: .1}, future_return=.1
        )
        return SimpleNamespace(
            completed=True, example=self.example, sessions=(session,), future_returns=(0.1,), complete_returns=(0.7,)
        )

    def _plan(self, domain, *, changed=False):
        partition = "sys-val" if domain == "sysval" else "comp-val"
        ids = (f"{partition}-a", f"{partition}-b") if not changed else ("changed-a", "changed-b")
        return EvaluationPlan(
            ids,
            domain,
            42,
            partition,
            {"backend": "fixed", "temperature": 0},
            scoring_configuration(self.trainer.config),
        )

    def _record(self, domain, score, *, objective="selection", changed=False):
        plan = self._plan(domain, changed=changed)
        return EvaluationRecord(
            plan,
            tuple(
                ExampleEvaluation(
                    identifier,
                    "A",
                    1,
                    1.0 - score,
                    True,
                    2,
                    20,
                    complete_return=-99.0,
                    informative_sessions=1,
                    weighting_loss_sum=max(0.0, -score),
                )
                for identifier in plan.ordered_example_ids
            ),
            objective,
        )

    async def validate(self, component):
        assert not torch.is_grad_enabled()
        assert not self.trainer.alpha.training and not self.trainer.weighting.training
        self.events.append(f"validate:{component}")
        self.validations[component] += 1
        domain = "alphaval" if component == "routing" else "wval"
        objective = "selection" if component == "routing" else "negative_weighting_nll"
        if self.wrong_objective == component:
            objective = "negative_weighting_nll" if component == "routing" else "selection"
        value = self.trainer.alpha.value if component == "routing" else self.trainer.weighting.value
        score = float(value.detach()) if component == "routing" else min(-0.01, -1 + float(value.detach()))
        return self._record(
            domain, score, objective=objective, changed=self.changing_plan and self.validations[component] > 1
        )

    async def prompt_evidence(self, component):
        self._frozen_evaluation()
        self.events.append(f"prompt:{component}")
        self.prompt_components.append(component)
        return (
            PromptOccurrence(
                component,
                "rendered",
                "raw",
                "B" if self.failures else "A",
                "A",
                {},
                -1,
                query="Pick",
                example_id="train-query",
                split="train",
            ),
        )

    async def prompt_validation_batch(self):
        self.prompt_plan_count += 1
        return self._plan("promptval")

    async def evaluate_prompts(self, collection, batch):
        self._frozen_evaluation()
        assert batch == self._plan("promptval")
        self.validations["prompt"] += 1
        self.events.append("validate:prompt")
        count = sum(template.count("Reusable improvement.") for template in collection.to_dict().values())
        score = (
            -2.0
            + 0.01 * count
            + 0.01 * float(self.trainer.alpha.value.detach())
            + 0.01 * float(self.trainer.weighting.value.detach())
        )
        return self._record("promptval", score)

    async def system_validation(self):
        assert not torch.is_grad_enabled()
        assert not self.trainer.alpha.training and not self.trainer.weighting.training
        self.events.append("system")
        index = self.validations["system"]
        self.validations["system"] += 1
        score = self.system_scores[min(index, len(self.system_scores) - 1)]
        objective = "negative_weighting_nll" if self.wrong_objective == "system" else "selection"
        return self._record("sysval", score, objective=objective)


def test_schedule_orders_pilot_bootstrap_calibration_and_fresh_frozen_numerical_updates() -> None:
    trainer = make_trainer(config_for())
    hooks = Hooks()
    state = asyncio.run(trainer.fit(hooks))
    assert hooks.events[:7] == [
        "pilot",
        "validate:weighting",
        "weighting",
        "validate:weighting",
        "validate:routing",
        "routing",
        "validate:routing",
    ]
    assert hooks.events[7] == "calibration"
    assert hooks.pilot_count == hooks.calibration_count == 1
    assert state.pilot_complete and state.calibration_complete and all(state.bootstrap_complete.values())
    assert state.updates == {"weighting": 3, "routing": 3, "prompt": 2}
    assert state.attempts == state.updates
    assert len(hooks.identities) == 6 and len({item["trajectory_id"] for item in hooks.identities}) == 6
    assert all(values == sorted(set(values)) for values in hooks.parameter_inputs.values())
    assert all(hooks.freeze_checks)
    assert trainer.config.safety.max_internal_rounds == 12
    assert trainer.config.termination.max_sessions == 2
    numerical = [record for record in state.phase_records if record["phase"] in {"weighting", "routing"}]
    assert all(len(record["history"]) == 3 for record in numerical)  # entry baseline plus two updates
    for record in numerical:
        assert len({evaluation["plan"]["ordered_example_ids"] for evaluation in record["records"]}) == 1
        assert all(len(evaluation["examples"]) == 2 for evaluation in record["records"])
    assert state.phase == "complete"


def test_skipped_weighting_attempts_are_bounded_and_zero_prompt_phase_is_not_evaluated() -> None:
    trainer = make_trainer(config_for(numerical=2, attempts=3, bootstrap=0, prompt_iterations=0))
    hooks = Hooks(skip_weighting=True)
    state = asyncio.run(trainer.fit(hooks))
    assert state.attempts["weighting"] == 3 and state.updates["weighting"] == 0
    assert state.weighting_diagnostics.skipped_w_updates == 3
    assert state.weighting_diagnostics.total_completed_sessions == 3
    assert hooks.validations["weighting"] == 1
    assert state.attempts["prompt"] == hooks.validations["prompt"] == hooks.prompt_plan_count == 0


def test_prompt_cursor_and_patience_are_local_while_accepted_collection_continues() -> None:
    trainer = make_trainer(config_for(cycles=2, numerical=0, bootstrap=0, prompt_iterations=10, patience=2))
    hooks = Hooks(failures=False, system_scores=(0.5, 0.6))
    state = asyncio.run(trainer.fit(hooks))
    keys = tuple(trainer.gamma.components)
    assert hooks.prompt_components == list(keys[:4])
    assert state.prompt_cursor == 2 and state.attempts["prompt"] == 4 and state.updates["prompt"] == 0
    assert hooks.prompt_plan_count == 2 and hooks.validations["prompt"] == 2
    phases = [record for record in state.phase_records if record["phase"] == "prompt"]
    assert [(record["iterations"], record["stale"]) for record in phases] == [(2, 2), (2, 2)]


class BoundaryInterrupted(RuntimeError):
    pass


class InterruptAfterBoundary(InterleavedTrainer):
    def __init__(self, *, interrupt_phase, **kwargs):
        super().__init__(**kwargs)
        self.interrupt_phase = interrupt_phase
        self.interrupted = False

    def _checkpoint(self, *, safe_boundary: bool = True) -> None:
        super()._checkpoint(safe_boundary=safe_boundary)
        progress = self.state.phase_progress
        count = progress.get("iterations", progress.get("updates", 0))
        if safe_boundary and self.state.phase == self.interrupt_phase and count == 1 and not self.interrupted:
            self.interrupted = True
            raise BoundaryInterrupted("durable completed iteration")


@pytest.mark.parametrize("phase", ["weighting", "prompt"])
def test_resume_after_completed_iteration_matches_uninterrupted_schedule_exactly(tmp_path, phase) -> None:
    config = config_for(cycles=2, prompt_iterations=3)
    full = make_trainer(config)
    full_hooks = Hooks(system_scores=(0.4, 0.5))
    asyncio.run(full.fit(full_hooks, checkpoint_path=tmp_path / "uninterrupted.pt"))
    path = tmp_path / f"{phase}.pt"
    first = make_trainer(config, trainer_type=InterruptAfterBoundary, interrupt_phase=phase)
    first_hooks = Hooks(system_scores=(0.4, 0.5))
    with pytest.raises(BoundaryInterrupted):
        asyncio.run(first.fit(first_hooks, checkpoint_path=path))
    saved = torch.load(path, weights_only=False)
    assert saved["trainer_state"]["phase"] == phase
    resumed = make_trainer(config)
    resumed_hooks = Hooks(system_scores=(0.4, 0.5))
    resumed.resume_from_checkpoint(path, hooks=resumed_hooks)
    asyncio.run(resumed.fit(resumed_hooks, checkpoint_path=path))
    torch.testing.assert_close(resumed.alpha.value, full.alpha.value, rtol=0, atol=0)
    torch.testing.assert_close(resumed.weighting.value, full.weighting.value, rtol=0, atol=0)
    assert resumed.gamma.to_dict() == full.gamma.to_dict()
    assert dataclasses.asdict(resumed.state) == dataclasses.asdict(full.state)
    assert resumed_hooks.state_dict() == full_hooks.state_dict()
    assert resumed_hooks.pilot_count == resumed_hooks.calibration_count == 1
    assert (
        first.prompt_optimizer.proposer.calls + resumed.prompt_optimizer.proposer.calls
        == full.prompt_optimizer.proposer.calls
    )


def test_best_joint_selection_is_strict_and_earliest_tied_cycle_independent_of_patience_epsilon() -> None:
    trainer = make_trainer(config_for(cycles=3, numerical=1, prompt_iterations=1, outer_epsilon=0.1))
    hooks = Hooks(system_scores=(0.7, 0.8, 0.800001, 0.800001))
    state = asyncio.run(trainer.fit(hooks))
    assert trainer.best_system["cycle"] == 2
    assert state.best_system_score == 0.800001
    assert state.outer_stale == 2
    assert state.outer_cycle == 3
    torch.testing.assert_close(trainer.alpha.value, trainer.best_system["alpha"]["value"])
    torch.testing.assert_close(trainer.weighting.value, trainer.best_system["weighting"]["value"])
    assert trainer.gamma.to_dict() == trainer.best_system["gamma"]
    assert sum(value.count("Reusable improvement.") for value in trainer.gamma.to_dict().values()) == 2


@pytest.mark.parametrize("component", ["weighting", "routing", "system"])
def test_validation_rejects_wrong_objective_before_comparing_scores(component) -> None:
    trainer = make_trainer(config_for(bootstrap=0, numerical=1, prompt_iterations=0))
    hooks = Hooks(wrong_objective=component)
    with pytest.raises(ValueError, match="objective|sysval.*trajectories"):
        asyncio.run(trainer.fit(hooks))


def test_numerical_phase_rejects_changed_validation_minibatch() -> None:
    trainer = make_trainer(config_for(bootstrap=0, numerical=1, prompt_iterations=0))
    with pytest.raises(ValueError, match="minibatch"):
        asyncio.run(trainer.fit(Hooks(changing_plan=True)))


def _manager_setup():
    alpha, weighting = ScalarModule(0.2), ScalarModule(-0.3)
    alpha_optimizer = torch.optim.Adam(alpha.parameters(), lr=0.01)
    weighting_optimizer = torch.optim.Adam(weighting.parameters(), lr=0.02)
    for model, optimizer in ((alpha, alpha_optimizer), (weighting, weighting_optimizer)):
        model.value.square().backward()
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
    return {
        "alpha": alpha,
        "weighting": weighting,
        "alpha_optimizer": alpha_optimizer,
        "weighting_optimizer": weighting_optimizer,
        "config": config_for(),
    }


def test_checkpoint_round_trip_restores_models_optimizers_prompts_counters_and_all_cpu_rngs(tmp_path) -> None:
    objects = _manager_setup()
    state = TrainerState(phase="prompt", prompt_cursor=4)
    state.weighting_diagnostics.record_attempt((SimpleNamespace(resolved_answers=("A", "B")),), "A", successful=True)
    gamma = seed_prompt_collection()
    gamma = gamma.replace_template("tie", gamma.components["tie"].template + "\nSaved improvement.")
    path = tmp_path / "joint.pt"
    random.seed(71)
    np.random.seed(72)
    torch.manual_seed(73)
    CheckpointManager.save(path, **objects, gamma=gamma, state=state)
    expected_draws = random.random(), np.random.random(), torch.rand(3)
    fresh = _manager_setup()
    with torch.no_grad():
        fresh["alpha"].value.fill_(9)
    loaded_gamma, loaded_state, payload = CheckpointManager.load(path, **fresh)
    assert loaded_gamma.to_dict() == gamma.to_dict()
    assert dataclasses.asdict(loaded_state) == dataclasses.asdict(state)
    torch.testing.assert_close(fresh["alpha"].value, objects["alpha"].value)
    torch.testing.assert_close(fresh["weighting"].value, objects["weighting"].value)
    assert random.random() == expected_draws[0]
    assert np.random.random() == expected_draws[1]
    torch.testing.assert_close(torch.rand(3), expected_draws[2], rtol=0, atol=0)
    assert fresh["alpha_optimizer"].param_groups[0]["lr"] == objects["alpha_optimizer"].param_groups[0]["lr"]
    assert payload["schema_version"] == CheckpointManager.schema_version


def test_checkpoint_syncs_file_before_replace_and_directory_before_return(tmp_path, monkeypatch):
    path = tmp_path / "durable.pt"
    path.write_bytes(b"earlier checkpoint")
    calls = []
    original_fsync = os.fsync

    def fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            calls.append("directory")
            assert torch.load(path, weights_only=False)["request_boundary_pending"] is False
        else:
            calls.append("file")
            assert path.read_bytes() == b"earlier checkpoint"
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    CheckpointManager.save(path, **_manager_setup(), gamma=seed_prompt_collection(), state=TrainerState())
    assert calls == ["file", "directory"] and not path.with_name(path.name + ".pending").exists()


def test_failed_checkpoint_file_sync_preserves_previous_boundary(tmp_path, monkeypatch):
    path = tmp_path / "durable.pt"
    path.write_bytes(b"earlier checkpoint")

    def fsync(_descriptor):
        raise OSError("durability unavailable")

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="durability unavailable"):
        CheckpointManager.save(path, **_manager_setup(), gamma=seed_prompt_collection(), state=TrainerState())
    assert path.read_bytes() == b"earlier checkpoint" and not path.with_name(path.name + ".pending").exists()


@pytest.mark.parametrize(
    "corruption", ["schema", "architecture", "model_shape", "optimizer_shape", "torch_rng", "gamma", "config", "hook"]
)
def test_checkpoint_preflight_rejects_corruption_before_any_caller_state_changes(tmp_path, corruption) -> None:
    objects = _manager_setup()
    path = tmp_path / "corrupt.pt"
    CheckpointManager.save(path, **objects, gamma=seed_prompt_collection(), state=TrainerState())
    payload = torch.load(path, weights_only=False)
    if corruption == "schema":
        payload["schema_version"] = 1
    elif corruption == "architecture":
        payload["architecture_identifiers"]["answer_state_architecture_id"] = "obsolete"
    elif corruption == "model_shape":
        payload["weighting"]["value"] = torch.zeros(2)
    elif corruption == "optimizer_shape":
        first = next(iter(payload["alpha_optimizer"]["state"].values()))
        first["exp_avg"] = torch.zeros(2)
    elif corruption == "torch_rng":
        payload["torch_random_state"] = torch.zeros(3, dtype=torch.uint8)
    elif corruption == "gamma":
        payload["gamma"]["tie"] = "Missing all structural fields"
    elif corruption == "config":
        payload["declared_config"]["training"]["weight_decay"] = 0.5
    else:
        payload["hook_state"] = {"condition": "wrong"}
    torch.save(payload, path)
    fresh = _manager_setup()
    with torch.no_grad():
        fresh["alpha"].value.fill_(7)
        fresh["weighting"].value.fill_(8)
    before = (fresh["alpha"].value.detach().clone(), fresh["weighting"].value.detach().clone())
    optimizer_before = copy.deepcopy(fresh["alpha_optimizer"].state_dict())
    random.seed(9)
    np.random.seed(10)
    torch.manual_seed(11)
    python_rng, numpy_rng, torch_rng = random.getstate(), np.random.get_state(), torch.random.get_rng_state()
    with pytest.raises((ValueError, RuntimeError, TypeError, KeyError)):
        CheckpointManager.load(path, **fresh, hook_validator=Hooks().validate_state)
    torch.testing.assert_close(fresh["alpha"].value, before[0], rtol=0, atol=0)
    torch.testing.assert_close(fresh["weighting"].value, before[1], rtol=0, atol=0)
    assert fresh["alpha_optimizer"].state_dict()["param_groups"] == optimizer_before["param_groups"]
    for identifier, values in fresh["alpha_optimizer"].state_dict()["state"].items():
        for key, value in values.items():
            torch.testing.assert_close(value, optimizer_before["state"][identifier][key], rtol=0, atol=0)
    assert random.getstate() == python_rng
    assert np.array_equal(np.random.get_state()[1], numpy_rng[1])
    torch.testing.assert_close(torch.random.get_rng_state(), torch_rng, rtol=0, atol=0)


@pytest.mark.parametrize("phase", ["weighting", "prompt"])
def test_extending_outer_ceiling_can_resume_inside_an_unchanged_active_phase(tmp_path, phase) -> None:
    original = config_for(cycles=1, prompt_iterations=3)
    extended = config_for(cycles=2, prompt_iterations=3)
    path = tmp_path / "extended.pt"
    first = make_trainer(original, trainer_type=InterruptAfterBoundary, interrupt_phase=phase)
    with pytest.raises(BoundaryInterrupted):
        asyncio.run(first.fit(Hooks(system_scores=(0.4, 0.5)), checkpoint_path=path))
    resumed = make_trainer(extended)
    hooks = Hooks(system_scores=(0.4, 0.5))
    resumed.resume_from_checkpoint(path, hooks=hooks)
    asyncio.run(resumed.fit(hooks, checkpoint_path=path))
    assert resumed.state.outer_cycle == 2
    assert hooks.pilot_count == hooks.calibration_count == 1
    assert resumed.state.updates == {"weighting": 5, "routing": 5, "prompt": 6}


def test_system_validation_plan_cannot_change_between_outer_checkpoint_comparisons() -> None:
    class ChangingSystemHooks(Hooks):
        async def system_validation(self):
            result = await super().system_validation()
            if self.validations["system"] > 1:
                return self._record("sysval", result.score, changed=True)
            return result

    trainer = make_trainer(config_for(cycles=2, numerical=0, prompt_iterations=0, bootstrap=0))
    with pytest.raises(ValueError, match="system|plan|configuration"):
        asyncio.run(trainer.fit(ChangingSystemHooks(system_scores=(0.4, 0.5))))


@pytest.mark.parametrize("corruption", ["incomplete_evaluation", "score_disagrees"])
def test_checkpoint_preflight_rejects_invalid_best_system_evaluation(tmp_path, corruption) -> None:
    config = config_for(numerical=1, prompt_iterations=1)
    path = tmp_path / "best.pt"
    original = make_trainer(config)
    asyncio.run(original.fit(Hooks(), checkpoint_path=path))
    payload = torch.load(path, weights_only=False)
    if corruption == "incomplete_evaluation":
        payload["best_system"]["evaluation"]["examples"] = payload["best_system"]["evaluation"]["examples"][:1]
    else:
        payload["best_system"]["score"] += 1.0
    torch.save(payload, path)
    fresh = make_trainer(config)
    with torch.no_grad():
        fresh.alpha.value.fill_(7)
        fresh.weighting.value.fill_(8)
    hooks = Hooks()
    with pytest.raises(ValueError):
        fresh.resume_from_checkpoint(path, hooks=hooks)
    assert fresh.alpha.value.item() == 7 and fresh.weighting.value.item() == 8
    assert fresh.best_system is None and hooks.trainer is None


def test_prompt_component_cursor_wraps_the_complete_registry() -> None:
    trainer = make_trainer(config_for(numerical=0, bootstrap=0, prompt_iterations=16, patience=20))
    hooks = Hooks(failures=False, system_scores=(0.7, 0.8))
    asyncio.run(trainer.fit(hooks))
    keys = list(trainer.gamma.components)
    assert len(keys) == 15
    assert hooks.prompt_components == keys + keys[:1]
    assert trainer.state.prompt_cursor == 1


def test_incomplete_numerical_trajectory_consumes_attempts_without_parameter_updates() -> None:
    class IncompleteHooks(Hooks):
        async def trajectory(self, component, split, *, routing_identity):
            result = await super().trajectory(component, split, routing_identity=routing_identity)
            result.completed = False
            result.status = "initialization-failed"
            return result

    trainer = make_trainer(config_for(numerical=1, bootstrap=0, prompt_iterations=0))
    hooks = IncompleteHooks()
    state = asyncio.run(trainer.fit(hooks))
    assert state.attempts == {"weighting": 1, "routing": 1, "prompt": 0}
    assert state.updates == {"weighting": 0, "routing": 0, "prompt": 0}
    assert trainer.alpha.value.item() == trainer.weighting.value.item() == 0
    assert len(hooks.identities) == 2
    assert state.weighting_diagnostics.failed_w_updates == 1


def test_final_partial_interval_validation_and_skipped_attempts_follow_successful_updates():
    config = config_for(numerical=3, bootstrap=0, prompt_iterations=0, attempts=5)
    phase = dataclasses.replace(config.training.weighting_phase, validation_interval=2)
    config = dataclasses.replace(
        config,
        training=dataclasses.replace(
            config.training, weighting_phase=phase, routing_phase=dataclasses.replace(phase, max_updates=0)
        ),
    )
    trainer = make_trainer(config)
    hooks = Hooks(system_scores=(0.7, 0.8))
    state = asyncio.run(trainer.fit(hooks))
    record = next(item for item in state.phase_records if item["phase"] == "weighting")
    assert record["updates"] == 3
    assert len(record["records"]) == 3  # Incoming, update two, and changed phase exit.
    assert record["last_validated_update"] == 3
    skipped = make_trainer(config)
    skipped_hooks = Hooks(skip_weighting=True)
    asyncio.run(skipped.fit(skipped_hooks))
    assert skipped_hooks.validations["weighting"] == 1
    assert skipped.state.attempts["weighting"] == 5 and skipped.state.updates["weighting"] == 0


def test_checkpoint_zero_is_selectable_and_attempt_highwater_survives_best_restore():
    trainer = make_trainer(config_for(numerical=1, bootstrap=0, prompt_iterations=0))
    hooks = Hooks(system_scores=(0.9, 0.8))
    state = asyncio.run(trainer.fit(hooks))
    assert trainer.best_system["cycle"] == 0 and state.eligible_checkpoints == [0, 1]
    assert trainer.alpha.value.item() == trainer.weighting.value.item() == 0.0
    assert state.workflow_attempt_counter == 2
    assert trainer.allocate_workflow_attempt() == 2
    selected = trainer.best_system
    python_rng, numpy_rng, torch_rng = random.Random(), np.random.RandomState(), torch.Generator()
    python_rng.setstate(selected["python_random_state"])
    numpy_rng.set_state(selected["numpy_random_state"])
    torch_rng.set_state(selected["torch_random_state"])
    assert random.random() == python_rng.random()
    assert np.random.random() == numpy_rng.random()
    assert torch.equal(torch.rand(1), torch.rand(1, generator=torch_rng))


def test_checkpoint_zero_only_run_never_enters_regular_training():
    trainer = make_trainer(config_for(cycles=0, numerical=1, bootstrap=0, prompt_iterations=1))
    hooks = Hooks()
    state = asyncio.run(trainer.fit(hooks))
    assert state.phase == "complete" and state.outer_cycle == 0
    assert state.eligible_checkpoints == [0] and trainer.best_system["cycle"] == 0
    assert not hooks.identities and not hooks.prompt_components


@pytest.mark.parametrize("status", ["budget-exhausted", "accounting-failed", "binding-failed", "counting-failed"])
def test_fatal_failure_is_incomplete_stage_and_never_validation_plateau(status):
    class BudgetHooks(Hooks):
        async def trajectory(self, component, split, *, routing_identity):
            result = await super().trajectory(component, split, routing_identity=routing_identity)
            result.completed, result.status = False, status
            return result

    trainer = make_trainer(config_for(numerical=1, bootstrap=0, prompt_iterations=0))
    state = asyncio.run(trainer.fit(BudgetHooks()))
    assert state.phase == "failed" and state.outer_cycle == 0 and state.outer_stale == 0
    assert state.execution_failures[-1]["budget_exhausted"] is (status == "budget-exhausted")
    assert state.updates["weighting"] == 0 and state.attempts["weighting"] == 1


def test_pending_validation_resumes_without_repeating_durable_adam_update(tmp_path):
    class InterruptedValidation(Hooks):
        async def validate(self, component):
            if component == "weighting" and self.validations[component] == 1:
                raise InterruptedError("between Adam and scheduled validation")
            return await super().validate(component)

    config = config_for(numerical=1, bootstrap=0, prompt_iterations=0)
    path = tmp_path / "pending.pt"
    interrupted = make_trainer(config)
    with pytest.raises(InterruptedError):
        asyncio.run(interrupted.fit(InterruptedValidation(), checkpoint_path=path))
    resumed, hooks = make_trainer(config), Hooks()
    resumed.resume_from_checkpoint(path, hooks=hooks)
    assert resumed.state.updates["weighting"] == 1
    assert resumed.state.phase_progress["last_validated_update"] == 0
    asyncio.run(resumed.fit(hooks))
    assert resumed.state.attempts["weighting"] == 1 and hooks.validations["weighting"] == 2


@pytest.mark.parametrize("failure_site", ["proposal", "candidate", "baseline", "evidence"])
def test_prompt_budget_failure_stops_stage_without_new_evidence_batch(failure_site):
    from examples.adaptive_multi_agent_collab.learning.evaluation import WorkflowExecutionFailure
    from examples.adaptive_multi_agent_collab.providers.resources import BudgetExhausted

    class BudgetProposer(Proposer):
        async def propose(self, spec, evidence, *, iteration_id):
            if failure_site == "proposal":
                raise BudgetExhausted("development cap")
            return await super().propose(spec, evidence, iteration_id=iteration_id)

    class BudgetHooks(Hooks):
        async def prompt_evidence(self, component):
            result = await super().prompt_evidence(component)
            if failure_site == "evidence":
                raise WorkflowExecutionFailure("budget", budget_exhausted=True)
            return result

        async def evaluate_prompts(self, collection, batch):
            result = await super().evaluate_prompts(collection, batch)
            if failure_site == "baseline" or (failure_site == "candidate" and self.validations["prompt"] > 1):
                raise WorkflowExecutionFailure("budget", budget_exhausted=True)
            return result

    trainer = make_trainer(config_for(numerical=0, bootstrap=0, prompt_iterations=4))
    trainer.prompt_optimizer = PromptOptimizer(BudgetProposer())
    hooks = BudgetHooks()
    state = asyncio.run(trainer.fit(hooks))
    assert state.phase == "failed" and state.execution_failures[-1]["budget_exhausted"]
    assert len(hooks.prompt_components) == (0 if failure_site == "baseline" else 1)
    assert state.attempts["prompt"] == (0 if failure_site == "baseline" else 1)
    assert state.updates["prompt"] == 0
    assert hooks.validations["system"] == 1  # Checkpoint zero only.


@pytest.mark.parametrize("boundary", ["reserved", "completed_dispatch", "missing_field"])
def test_unresolved_request_checkpoint_rejected_before_mutating_resume_state(tmp_path, boundary):
    trainer = make_trainer(config_for())
    hooks = Hooks()
    hooks.bind(trainer)
    trainer.hooks = hooks
    path = tmp_path / "request-boundary.pt"
    trainer.checkpoint_path = path
    trainer._request_checkpoint()
    if boundary == "completed_dispatch":
        trainer._request_checkpoint()  # Settlement is still before a durable workflow outcome.
        trainer._checkpoint(safe_boundary=False)  # Next prompt-evidence allocation cannot clear it.
    trainer.save_checkpoint(path)  # Manual saves retain the pending state.
    payload = torch.load(path, weights_only=False)
    assert payload["request_boundary_pending"] is True
    if boundary == "missing_field":
        payload.pop("request_boundary_pending")
        torch.save(payload, path)
    fresh = make_trainer(config_for())
    fresh_hooks = Hooks()
    with torch.no_grad():
        fresh.alpha.value.fill_(9)
    before_rng = torch.random.get_rng_state().clone()
    before_python = random.getstate()
    before_numpy = np.random.get_state()
    with pytest.raises(ValueError, match="unresolved physical request boundary"):
        fresh.resume_from_checkpoint(path, hooks=fresh_hooks)
    assert fresh.alpha.value.item() == 9 and fresh.state.phase == "unstarted"
    assert not fresh_hooks.events and not fresh.alpha_optimizer.state
    assert torch.equal(torch.random.get_rng_state(), before_rng)
    assert random.getstate() == before_python and np.array_equal(np.random.get_state()[1], before_numpy[1])
    trainer._checkpoint()  # The caller now commits the enclosing completed workflow/iteration.
    assert torch.load(path, weights_only=False)["request_boundary_pending"] is False
    fresh.resume_from_checkpoint(path, hooks=fresh_hooks)
    assert fresh.alpha.value.item() == trainer.alpha.value.item()
