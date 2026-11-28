"""Prospective W slots retain learned state and stop at the reviewed boundary."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict, replace

import pytest
import torch

from examples.adaptive_multi_agent_collab.config import Config, TrainingConfig, WorkflowRecoveryConfig
from examples.adaptive_multi_agent_collab.learning.training import InterleavedTrainer
from examples.adaptive_multi_agent_collab.workflows.recovery import WorkflowRecoveryStopped
from examples.adaptive_multi_agent_collab.schemas import Trajectory
from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger
from examples.adaptive_multi_agent_collab.tests.test_bootstrap_boundary import assert_optimizer_equal
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import (
    BoundaryInterrupted, Hooks, config_for, make_trainer,
)


class SupplementHooks(Hooks):
    def require_development(self, *, weighting_supplement=False):
        assert weighting_supplement

    async def validate_supplement_routing(self, *, weighting_changed):
        self.events.append(f"supplement-routing:{weighting_changed}")
        return await self.validate("routing")


def prepared(trainer_type=InterleavedTrainer):
    config = config_for(bootstrap=10)
    config = replace(
        config, workflow_recovery=WorkflowRecoveryConfig(enabled=True),
        training=replace(config.training, bootstrap_weighting_attempts=50,
                         weighting_phase=replace(config.training.weighting_phase, validation_interval=10)),
    )
    trainer = make_trainer(config, trainer_type=trainer_type)
    for component, count in (("alpha", 10), ("weighting", 8)):
        module = getattr(trainer, component)
        optimizer = torch.optim.Adam(module.parameters(), lr=.001)
        setattr(trainer, f"{component}_optimizer", optimizer)
        for _ in range(count):
            optimizer.zero_grad(set_to_none=True)
            (module.value - 1).square().backward()
            optimizer.step()
    trainer.state.phase = "calibration"
    trainer.state.pilot_complete = True
    trainer.state.bootstrap_complete = {"weighting": True, "routing": True}
    trainer.state.attempts.update(weighting=30, routing=10)
    trainer.state.updates.update(weighting=8, routing=10)
    trainer.state.workflow_attempt_counter = 40
    trainer.state.phase_records = [
        {"phase": "weighting_bootstrap", "attempts": 30, "updates": 8, "reason": "budget"},
        {"phase": "routing_bootstrap", "attempts": 10, "updates": 10, "reason": "budget"},
    ]
    return trainer


def test_only_weighting_bootstrap_default_is_increased_and_legacy_serialization_preserved():
    training = TrainingConfig()
    assert (training.bootstrap_weighting_attempts, training.bootstrap_weighting_updates) == (50, 10)
    assert (training.weighting_phase.max_attempts, training.weighting_phase.max_updates) == (30, 20)
    assert training.bootstrap_routing_updates == 10
    legacy = Config(training=replace(training, bootstrap_weighting_attempts=30)).to_dict()
    assert "workflow_recovery" not in legacy
    assert Config.from_dict(legacy).to_dict() == legacy
    enabled = replace(Config.from_dict(legacy), workflow_recovery=WorkflowRecoveryConfig(enabled=True))
    assert enabled.to_dict()["workflow_recovery"]["max_replacements"] == 1
    assert Config.from_dict(enabled.to_dict()) == enabled


def test_supplement_two_updates_preserves_alpha_adam_prompts_and_original_phases(tmp_path):
    trainer, hooks = prepared(), SupplementHooks()
    alpha = copy.deepcopy(trainer.alpha.state_dict())
    alpha_adam = copy.deepcopy(trainer.alpha_optimizer.state_dict())
    prompts, old_phases = trainer.gamma.to_dict(), copy.deepcopy(trainer.state.phase_records)
    state = asyncio.run(trainer.fit(hooks, checkpoint_path=tmp_path / "state.pt", weighting_supplement=True))
    assert state.phase == "calibration" and not state.calibration_complete
    assert state.attempts == {"weighting": 32, "routing": 10, "prompt": 0}
    assert state.updates == {"weighting": 10, "routing": 10, "prompt": 0}
    assert state.workflow_attempt_counter == 42
    assert state.phase_records[:2] == old_phases
    assert state.phase_records[2]["phase"] == "weighting_supplement"
    assert hooks.events == ["validate:weighting", "weighting", "weighting", "validate:weighting",
                            "supplement-routing:True", "validate:routing"]
    assert hooks.calibration_count == hooks.pilot_count == 0
    assert not state.system_validation_records and not trainer.prompt_optimizer.proposer.calls
    assert trainer.gamma.to_dict() == prompts
    torch.testing.assert_close(trainer.alpha.state_dict()["value"], alpha["value"], rtol=0, atol=0)
    assert_optimizer_equal(trainer.alpha_optimizer.state_dict(), alpha_adam)
    assert int(trainer.weighting_optimizer.state[trainer.weighting.value]["step"]) == 10
    assert [r["workflow_attempt_id"] for r in state.training_loss_records] == [40, 41]
    assert all(r["phase"] == "weighting_supplement" and r["status"] == "updated"
               for r in state.training_loss_records)


def test_supplement_twenty_skipped_slots_no_revisit_or_unchanged_exit_validation(tmp_path):
    trainer, hooks = prepared(), SupplementHooks(skip_weighting=True)
    before_adam = copy.deepcopy(trainer.weighting_optimizer.state_dict())
    state = asyncio.run(trainer.fit(hooks, checkpoint_path=tmp_path / "state.pt", weighting_supplement=True))
    assert state.attempts["weighting"] == 50 and state.updates["weighting"] == 8
    assert state.workflow_attempt_counter == 60
    assert hooks.events.count("weighting") == 20
    assert hooks.validations == {"weighting": 1, "routing": 1, "prompt": 0, "system": 0}
    assert "supplement-routing:False" in hooks.events
    assert_optimizer_equal(trainer.weighting_optimizer.state_dict(), before_adam)
    assert len(state.training_loss_records) == 20
    assert all(r["status"] == "skipped" and r["loss"] is None for r in state.training_loss_records)
    original = (tmp_path / "state.pt").read_bytes()
    restored, restored_hooks = prepared(), SupplementHooks(skip_weighting=True)
    restored.resume_from_checkpoint(tmp_path / "state.pt", hooks=restored_hooks)
    asyncio.run(restored.fit(restored_hooks, checkpoint_path=tmp_path / "state.pt", weighting_supplement=True))
    assert asdict(restored.state) == asdict(state)
    assert (tmp_path / "state.pt").read_bytes() == original
    assert restored_hooks.state_dict() == hooks.state_dict()


class InterruptedSupplement(InterleavedTrainer):
    def _checkpoint(self, *, safe_boundary=True):
        super()._checkpoint(safe_boundary=safe_boundary)
        if self.state.phase == "weighting_supplement" and self.state.phase_progress.get("updates") == 1:
            raise BoundaryInterrupted("saved first supplementary Adam step")


def test_supplement_resume_does_not_repeat_update_slot_or_matching_validation(tmp_path):
    uninterrupted, full_hooks = prepared(), SupplementHooks()
    asyncio.run(uninterrupted.fit(full_hooks, checkpoint_path=tmp_path / "full.pt", weighting_supplement=True))
    interrupted, hooks = prepared(InterruptedSupplement), SupplementHooks()
    with pytest.raises(BoundaryInterrupted):
        asyncio.run(interrupted.fit(hooks, checkpoint_path=tmp_path / "resume.pt", weighting_supplement=True))
    resumed, resumed_hooks = prepared(), SupplementHooks()
    resumed.resume_from_checkpoint(tmp_path / "resume.pt", hooks=resumed_hooks)
    asyncio.run(resumed.fit(resumed_hooks, checkpoint_path=tmp_path / "resume.pt", weighting_supplement=True))
    assert asdict(resumed.state) == asdict(uninterrupted.state)
    assert resumed_hooks.state_dict() == full_hooks.state_dict()
    for name in ("alpha_optimizer", "weighting_optimizer"):
        assert_optimizer_equal(getattr(resumed, name).state_dict(), getattr(uninterrupted, name).state_dict())


@pytest.mark.parametrize("change", ["regular", "unrecovered", "counter", "calibrated", "incomplete-bootstrap"])
def test_supplement_rejects_incompatible_boundary_without_new_work(tmp_path, change):
    trainer, hooks = prepared(), SupplementHooks()
    if change == "regular":
        trainer.state.phase = "weighting"
    elif change == "unrecovered":
        trainer.config = replace(trainer.config, workflow_recovery=WorkflowRecoveryConfig())
    elif change == "counter":
        trainer.state.attempts["weighting"] += 1
    elif change == "calibrated":
        trainer.state.calibration_complete = True
    else:
        trainer.state.bootstrap_complete["routing"] = False
    before = asdict(trainer.state)
    with pytest.raises(ValueError):
        asyncio.run(trainer.fit(hooks, checkpoint_path=tmp_path / "absent.pt", weighting_supplement=True))
    assert not hooks.events and asdict(trainer.state) == before
    assert not (tmp_path / "absent.pt").exists()


@pytest.mark.parametrize("phase", ["weighting_supplement", "routing_supplement_validation"])
def test_ordinary_fit_cannot_resume_unfinished_supplement(phase):
    trainer = prepared()
    trainer.state.phase = phase
    hooks = SupplementHooks()
    with pytest.raises(ValueError, match="dedicated continuation scope"):
        asyncio.run(trainer.fit(hooks))
    assert not hooks.events


@pytest.mark.parametrize("publication_failure", [False, True])
def test_fit_terminal_replacement_stop_keeps_accounting_identity_and_unsafe_publication(tmp_path, publication_failure):
    class StoppedTrainer(InterleavedTrainer):
        async def checkpoint_async(self, *, safe_boundary=True):
            if publication_failure:
                raise OSError("synthetic checkpoint publication failure")
            await super().checkpoint_async(safe_boundary=safe_boundary)

    class StoppedReplacementHooks(SupplementHooks):
        def state_dict(self):
            return {**super().state_dict(), "expenditure_ledger": self.expenditure_ledger.state_dict()}

        async def trajectory(self, component, split="train", *, routing_identity):
            assert component == "weighting" and split == "train" and routing_identity.update_id == 40
            value = Trajectory(self.example, (), ())
            value.status, value.error = "accounting-failed", "synthetic terminal replacement halt"
            value.training_execution_id = self.trainer.allocate_workflow_attempt()
            self.trainer._request_checkpoint()
            self.terminal_error = WorkflowRecoveryStopped(value)
            raise self.terminal_error

    trainer, hooks = prepared(StoppedTrainer), StoppedReplacementHooks()
    ledger = ExpenditureLedger(replace(trainer.config.resources, development_budget=80, currency="USD"))
    ledger.reserve(.02, kind="role", model="offline", metadata={})
    ledger.halt("synthetic terminal replacement halt")
    hooks.expenditure_ledger = ledger
    before_ledger = copy.deepcopy(ledger.state_dict())
    old_optimizer = copy.deepcopy(trainer.weighting_optimizer.state_dict())
    old_parameters = copy.deepcopy(trainer.weighting.state_dict())
    path = tmp_path / "interrupted.pt"
    if publication_failure:
        with pytest.raises(WorkflowRecoveryStopped, match="synthetic terminal") as raised:
            asyncio.run(trainer.fit(hooks, checkpoint_path=path, weighting_supplement=True))
        assert raised.value is hooks.terminal_error
        assert raised.value.cleanup_errors[0]["type"] == "OSError"
    else:
        state = asyncio.run(trainer.fit(hooks, checkpoint_path=path, weighting_supplement=True))
        assert state is trainer.state
    assert trainer.state.phase == "failed" and not trainer.state.calibration_complete
    assert hooks.calibration_count == 0 and not trainer.state.system_validation_records
    assert trainer.state.execution_failures[-1]["phase"] == "weighting_supplement"
    assert trainer.state.execution_failures[-1]["operational_stop"]
    record = trainer.state.training_loss_records[-1]
    assert (record["query_id"], record["workflow_attempt_id"], record["slot_original_execution_id"]) == (
        "train-query", 41, 40
    )
    assert record["status"] == "failed" and record["reason"] == "WorkflowRecoveryStopped"
    assert record["loss"] is None and record["normalization_denominator"] is None
    assert trainer.state.attempts["weighting"] == 31 and trainer.state.updates["weighting"] == 8
    assert trainer.state.workflow_attempt_counter == 42
    assert_optimizer_equal(trainer.weighting_optimizer.state_dict(), old_optimizer)
    torch.testing.assert_close(trainer.weighting.state_dict()["value"], old_parameters["value"], rtol=0, atol=0)
    assert trainer._request_boundary_pending
    saved = torch.load(path, map_location="cpu", weights_only=False)
    assert saved["request_boundary_pending"]
    assert ledger.state_dict() == before_ledger == saved["hook_state"]["expenditure_ledger"]
    assert ledger.entries[0]["charged"] is None and ledger.entries[0]["reserved"] == .02
    if not publication_failure:
        assert saved["trainer_state"]["phase"] == "failed"
        assert saved["trainer_state"]["training_loss_records"][-1] == record
