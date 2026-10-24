"""Offline W→alpha bootstrap boundaries preserve scientific schedules and resume state."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict, replace

import pytest
import torch

from examples.adaptive_multi_agent_collab.learning.training import InterleavedTrainer
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import (
    BoundaryInterrupted,
    Hooks,
    config_for,
    make_trainer,
)


class BootstrapHooks(Hooks):
    def require_development(self, *, bootstrap_only=False):
        assert bootstrap_only  # This fixture grants no calibration/regular-cycle execution.


def bootstrap_trainer(config, **kwargs):
    trainer = make_trainer(config, **kwargs)
    trainer.alpha_optimizer = torch.optim.Adam(trainer.alpha.parameters(), lr=0.01)
    trainer.weighting_optimizer = torch.optim.Adam(trainer.weighting.parameters(), lr=0.01)
    trainer.state.phase = "pilot_complete"
    trainer.state.pilot_complete = True
    return trainer


def assert_optimizer_equal(left, right):
    assert left["param_groups"] == right["param_groups"]
    assert left["state"].keys() == right["state"].keys()
    for identifier, values in left["state"].items():
        for key, value in values.items():
            torch.testing.assert_close(value, right["state"][identifier][key], rtol=0, atol=0)


def test_bootstrap_only_preserves_schedule_and_stops_after_routing_exit_validation(tmp_path):
    config = config_for(bootstrap=2, numerical=3)
    trainer = bootstrap_trainer(config)
    hooks = BootstrapHooks()
    path = tmp_path / "bootstrap.pt"
    state = asyncio.run(trainer.fit(hooks, checkpoint_path=path, bootstrap_only=True))
    assert hooks.events == [
        "validate:weighting", "weighting", "validate:weighting", "weighting", "validate:weighting",
        "validate:routing", "routing", "validate:routing", "routing", "validate:routing",
    ]
    assert state.phase == "calibration" and not state.calibration_complete
    assert state.bootstrap_complete == {"weighting": True, "routing": True}
    assert state.updates == state.attempts == {"weighting": 2, "routing": 2, "prompt": 0}
    assert state.workflow_attempt_counter == 4 and state.outer_cycle == 0
    assert hooks.pilot_count == hooks.calibration_count == hooks.validations["system"] == 0
    assert trainer.config == config and not trainer.prompt_optimizer.proposer.calls
    payload = torch.load(path, weights_only=False)
    assert payload["trainer_state"]["phase"] == "calibration"
    assert payload["request_boundary_pending"] is False
    assert [record["last_validated_update"] for record in state.phase_records] == [2, 2]
    before = path.read_bytes(), copy.deepcopy(asdict(state)), copy.deepcopy(hooks.state_dict())
    resumed = bootstrap_trainer(config)
    resumed_hooks = BootstrapHooks()
    resumed.resume_from_checkpoint(path, hooks=resumed_hooks)
    asyncio.run(resumed.fit(resumed_hooks, checkpoint_path=path, bootstrap_only=True))
    assert path.read_bytes() == before[0]  # No boundary rewrite, validation, or new workflow.
    assert asdict(resumed.state) == before[1] and resumed_hooks.state_dict() == before[2]
    for name in ("alpha_optimizer", "weighting_optimizer"):
        assert_optimizer_equal(getattr(resumed, name).state_dict(), getattr(trainer, name).state_dict())


class InterruptBootstrap(InterleavedTrainer):
    def __init__(self, *, interruption, **kwargs):
        super().__init__(**kwargs)
        self.interruption = interruption
        self.did_interrupt = False

    def _checkpoint(self, *, safe_boundary=True):
        super()._checkpoint(safe_boundary=safe_boundary)
        progress = self.state.phase_progress
        after_update = self.state.phase == "weighting_bootstrap" and progress.get("updates") == 1
        after_validation = (
            self.state.phase == "routing_bootstrap" and progress.get("last_validated_update") == 2
        )
        if safe_boundary and not self.did_interrupt and (
            after_update if self.interruption == "weighting_update" else after_validation
        ):
            self.did_interrupt = True
            raise BoundaryInterrupted("durable bootstrap update or validation")


@pytest.mark.parametrize("interruption", ["weighting_update", "routing_exit_validation"])
def test_bootstrap_resume_keeps_adam_sampling_and_completed_validations(tmp_path, interruption):
    config = config_for(bootstrap=2)
    full, full_hooks = bootstrap_trainer(config), BootstrapHooks()
    asyncio.run(full.fit(full_hooks, checkpoint_path=tmp_path / "full.pt", bootstrap_only=True))
    path = tmp_path / "resumed.pt"
    first = bootstrap_trainer(config, trainer_type=InterruptBootstrap, interruption=interruption)
    with pytest.raises(BoundaryInterrupted):
        asyncio.run(first.fit(BootstrapHooks(), checkpoint_path=path, bootstrap_only=True))
    resumed, hooks = bootstrap_trainer(config), BootstrapHooks()
    resumed.resume_from_checkpoint(path, hooks=hooks)
    asyncio.run(resumed.fit(hooks, checkpoint_path=path, bootstrap_only=True))
    assert asdict(resumed.state) == asdict(full.state)
    assert hooks.state_dict() == full_hooks.state_dict()
    torch.testing.assert_close(resumed.alpha.value, full.alpha.value, rtol=0, atol=0)
    torch.testing.assert_close(resumed.weighting.value, full.weighting.value, rtol=0, atol=0)
    for name in ("alpha_optimizer", "weighting_optimizer"):
        assert_optimizer_equal(getattr(resumed, name).state_dict(), getattr(full, name).state_dict())


def test_bootstrap_weighting_skip_budget_does_not_prevent_routing_or_change_allowances():
    config = config_for(bootstrap=2)
    config = replace(config, training=replace(config.training, bootstrap_weighting_attempts=3))
    trainer, hooks = bootstrap_trainer(config), BootstrapHooks(skip_weighting=True)
    state = asyncio.run(trainer.fit(hooks, bootstrap_only=True))
    assert state.attempts == {"weighting": 3, "routing": 2, "prompt": 0}
    assert state.updates == {"weighting": 0, "routing": 2, "prompt": 0}
    assert hooks.validations == {"weighting": 1, "routing": 3, "prompt": 0, "system": 0}
    assert not trainer.weighting_optimizer.state and trainer.config == config
    assert state.phase == "calibration"


@pytest.mark.parametrize("phase", ["unstarted", "pilot", "weighting", "system_baseline", "complete", "failed"])
def test_bootstrap_only_rejects_out_of_scope_state_before_checkpoint_or_model_changes(tmp_path, phase):
    trainer = bootstrap_trainer(config_for())
    trainer.state.phase = phase
    before = copy.deepcopy(asdict(trainer.state))
    path = tmp_path / "must-not-write.pt"
    with pytest.raises(ValueError, match="bootstrap-only"):
        asyncio.run(trainer.fit(BootstrapHooks(), checkpoint_path=path, bootstrap_only=True))
    assert not path.exists() and asdict(trainer.state) == before


def test_full_development_cannot_use_bootstrap_only_hook_approval():
    trainer = bootstrap_trainer(config_for())
    with pytest.raises(AssertionError):
        asyncio.run(trainer.fit(BootstrapHooks()))
    assert trainer.state.phase == "pilot_complete"
