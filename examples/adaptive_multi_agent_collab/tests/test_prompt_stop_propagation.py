"""Hard TextGrad dispositions must reach the optimizer and regular trainer."""

from __future__ import annotations

import asyncio
import copy

import pytest
import torch

from examples.adaptive_multi_agent_collab.learning.training import PromptOptimizer
from examples.adaptive_multi_agent_collab.providers.resources import AccountingFailure, BudgetExhausted, PolicyStopped
from examples.adaptive_multi_agent_collab.schemas import UsageRecord
from examples.adaptive_multi_agent_collab.workflows.prompts import OptimizationAttempt, PromptProposal, seed_prompt_collection
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _plan, _record
from examples.adaptive_multi_agent_collab.tests.test_textgrad_optimizer import occurrence
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import Hooks, config_for, make_trainer


def _audit():
    return PromptProposal(None, attempts=(OptimizationAttempt(
        "evaluation", 1, "mock-engine", "complete input", "retained partial output",
        UsageRecord(model_calls=1, input_tokens=10, output_tokens=2, monetary_cost=None),
    ),))


class StopProposer:
    def __init__(self, error_type=None, *, metadata=None):
        self.error_type, self.metadata_value, self.calls = error_type, metadata, 0

    def metadata(self):
        return {"protocol": "mocked_stop_propagation"}

    async def propose(self, spec, evidence, *, iteration_id):
        self.calls += 1
        if self.error_type is not None:
            error = self.error_type("recorded stop")
            error.prompt_proposal = _audit()
            raise error
        return PromptProposal(spec.template + "\nCheck the stated premise.",
                              attempts=_audit().attempts, metadata=self.metadata_value)


@pytest.mark.parametrize("error_type,disposition,budget", [
    (PolicyStopped, "policy-stop", False),
    (AccountingFailure, "accounting-stop", False),
    (BudgetExhausted, "budget-exhausted", True),
])
def test_prompt_optimizer_retains_graph_audit_and_never_validates_after_hard_stop(error_type, disposition, budget):
    async def run():
        proposer = StopProposer(error_type)
        optimizer = PromptOptimizer(proposer)
        collection, plan, evaluations = seed_prompt_collection(), _plan(), []
        async def evaluate(prompts, batch):
            evaluations.append(prompts.to_dict())
            return _record(batch)
        await optimizer.begin_phase(collection, plan, "condition", evaluate)
        result = await optimizer.step(collection, "role:S_T", [occurrence()], condition="condition",
                                      iteration_id="cycle:1:0", evaluate=evaluate)
        assert len(evaluations) == 1 and proposer.calls == 1
        assert result.collection is collection and result.reason == "execution_stopped"
        assert result.proposed is None and result.proposal.attempts == _audit().attempts
        assert result.proposal.metadata["failure_disposition"] == disposition
        assert result.proposal.metadata["budget_exhausted"] is budget
    asyncio.run(run())


@pytest.mark.parametrize("metadata", [{"operational_stop": True}, {"budget_exhausted": True}])
def test_valid_template_cannot_hide_fatal_metadata_or_trigger_validation(metadata):
    async def run():
        proposer, collection, evaluations = StopProposer(metadata=metadata), seed_prompt_collection(), []
        optimizer = PromptOptimizer(proposer)
        async def evaluate(prompts, batch):
            evaluations.append(prompts.to_dict())
            return _record(batch)
        await optimizer.begin_phase(collection, _plan(), "condition", evaluate)
        result = await optimizer.step(collection, "role:S_T", [occurrence()], condition="condition",
                                      iteration_id="cycle:1:0", evaluate=evaluate)
        assert len(evaluations) == 1 and not result.accepted
        assert result.reason == "execution_stopped" and result.collection is collection
        assert result.proposed is None
    asyncio.run(run())


@pytest.mark.parametrize("error_type,disposition", [
    (PolicyStopped, "policy-stop"), (AccountingFailure, "accounting-stop"),
    (BudgetExhausted, "budget-exhausted"),
])
def test_regular_trainer_stops_after_one_failed_prompt_attempt_and_persists_disposition(tmp_path, error_type, disposition):
    trainer, hooks = make_trainer(config_for(prompt_iterations=2)), Hooks()
    proposer = StopProposer(error_type)
    trainer.prompt_optimizer = PromptOptimizer(proposer)
    trainer.prompt_components = ("role:S_T",)
    trainer.state.phase = "prompt"
    before = trainer.gamma.to_dict()
    checkpoint = tmp_path / "stopped.pt"
    state = asyncio.run(trainer.fit(hooks, checkpoint_path=checkpoint))
    assert state.phase == "failed" and state.attempts["prompt"] == 1
    assert state.updates["prompt"] == 0 and proposer.calls == 1
    assert hooks.validations["prompt"] == 1 and hooks.validations["system"] == 0
    assert trainer.gamma.to_dict() == before
    assert state.execution_failures[-1]["failure_disposition"] == disposition
    record = state.prompt_validation_records[-1]
    assert record["reason"] == "execution_stopped" and record["proposed"] is None
    assert record["proposal"]["attempts"][0]["usage"]["monetary_cost"] is None
    payload = torch.load(checkpoint, weights_only=False)
    assert payload["trainer_state"]["execution_failures"] == state.execution_failures
    restored = make_trainer(config_for(prompt_iterations=2))
    restored.prompt_optimizer = PromptOptimizer(StopProposer(error_type))
    restored.resume_from_checkpoint(checkpoint, hooks=Hooks())
    with pytest.raises(ValueError, match="failed training stage"):
        asyncio.run(restored.fit(Hooks()))
    assert restored.prompt_optimizer.proposer.calls == 0


def test_cancelled_graph_audit_reaches_trainer_without_advancing_cursor_and_unsafe_resume_is_rejected(tmp_path):
    trainer, hooks = make_trainer(config_for(prompt_iterations=2)), Hooks()
    trainer.prompt_optimizer = PromptOptimizer(StopProposer(asyncio.CancelledError))
    trainer.prompt_components = ("role:S_T",)
    trainer.state.phase = "prompt"
    before = copy.deepcopy((trainer.gamma.to_dict(), trainer.state.attempts, trainer.state.prompt_cursor))
    checkpoint = tmp_path / "interrupted.pt"
    async def run():
        with pytest.raises(asyncio.CancelledError) as caught:
            await trainer.fit(hooks, checkpoint_path=checkpoint)
        assert caught.value.prompt_proposal.attempts == _audit().attempts
        assert caught.value.prompt_proposal.metadata["failure_disposition"] == "cancelled"
        # The production experiment shutdown wrapper uses this same unsafe
        # publication after propagated cancellation; it never certifies completion.
        await trainer.checkpoint_async(safe_boundary=False)
    asyncio.run(run())
    assert (trainer.gamma.to_dict(), trainer.state.attempts, trainer.state.prompt_cursor) == before
    assert hooks.validations["prompt"] == 1 and hooks.validations["system"] == 0
    record = trainer.state.prompt_validation_records[-1]
    assert record["reason"] == "interrupted" and not record["completed"]
    assert record["proposal"]["attempts"][0]["usage"]["monetary_cost"] is None
    restored = make_trainer(config_for(prompt_iterations=2))
    restored.prompt_optimizer = PromptOptimizer(StopProposer(asyncio.CancelledError))
    with pytest.raises(ValueError, match="in-flight|unfinished|request boundary|request-boundary"):
        restored.resume_from_checkpoint(checkpoint, hooks=Hooks())
    assert restored.prompt_optimizer.proposer.calls == 0
