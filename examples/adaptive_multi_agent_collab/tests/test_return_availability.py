"""Selected alpha losses retain only their actual reward dependencies."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.config import CostConfig, SelectionConfig, TerminationConfig
from examples.adaptive_multi_agent_collab.learning.evaluation import (
    EvaluationPlan,
    EvaluationRecord,
    _trajectory_evaluation,
    scoring_configuration,
)
from examples.adaptive_multi_agent_collab.infrastructure.randomness import GenerationContext
from examples.adaptive_multi_agent_collab.schemas import AgentCallKind, QueryExample, Scheme
from examples.adaptive_multi_agent_collab.learning.training import returns_and_future, mixed_alpha_loss
from examples.adaptive_multi_agent_collab.tests.test_runtime import _matrix, _runtime
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import Hooks, config_for, make_trainer


@pytest.mark.parametrize(
    ("rewards", "returns", "future"),
    [
        ([], [], []),
        ([None], [None], [0.0]),
        ([None, 2.0, 3.0], [None, 5.0, 3.0], [5.0, 3.0, 0.0]),
        ([1.0, None, 3.0], [None, None, 3.0], [None, 3.0, 0.0]),
        ([1.0, 2.0, None], [None, None, None], [None, None, 0.0]),
        ([None, None], [None, None], [None, 0.0]),
    ],
)
def test_returns_use_only_their_reward_suffix(rewards, returns, future):
    assert returns_and_future(rewards) == (returns, future)


def tie_runtime():
    runtime, agents = _runtime(
        3,
        {
            0: {"revision": [{"answer": "A", "explanation": "first"}, {"answer": "B", "explanation": "second"}]},
            1: {"role": {"status": "continue", "feedback": "Check the available evidence."}},
            2: {"role": {"status": "continue", "feedback": "Check the alternative."}},
        },
        {
            1: _matrix(3, {(0, 1): Scheme.REVIEWER, (0, 2): Scheme.REVIEWER}),
            2: _matrix(3, {(0, 1): Scheme.REVIEWER}),
        },
        termination=TerminationConfig(min_sessions=2, max_sessions=2),
        costs=CostConfig(),
    )

    class UnknownTieUsage:
        def __init__(self):
            self.ties = 0

        async def invoke(self, request):
            output = await agents[0].invoke(request)
            if request.kind is AgentCallKind.TIE:
                self.ties += 1
                return replace(
                    output,
                    attempts=(),
                    usage=replace(output.usage, input_tokens=None, monetary_cost=None),
                )
            return output

    proxy = UnknownTieUsage()
    runtime.agents[0] = proxy
    return runtime, proxy


def test_only_first_tie_cost_missing_keeps_shaped_alpha_but_not_g1_or_selection():
    runtime, proxy = tie_runtime()
    trajectory = asyncio.run(runtime.run(QueryExample("synthetic-tie", "Pick A, B or C", ("A", "B", "C"), "A")))
    assert trajectory.completed and proxy.ties == 1
    first, second = trajectory.sessions
    assert first.normalized_cost is None and first.immediate_reward is None and first.return_value is None
    assert second.immediate_reward is not None
    assert first.future_return == second.return_value == second.immediate_reward
    assert second.future_return == 0.0
    assert all(len(session.conversation_credits) == len(session.traces) for session in trajectory.sessions)
    assert trajectory.routing_eligible and trajectory.routing_eligible_for("mixed_q", p_mix=1.)
    assert not trajectory.selection_eligible
    assert not trajectory.selection_eligible
    evaluation = _trajectory_evaluation(trajectory, seed_replay_supported=False)
    plan = EvaluationPlan(
        (trajectory.example.example_id,),
        "alphaval",
        42,
        "comp-val",
        scoring_configuration=scoring_configuration(replace(runtime.config, selection=SelectionConfig(1.0))),
    )
    assert EvaluationRecord(plan, (evaluation,)).score is None
    assert evaluation.normalized_cost is None
    assert trajectory.future_returns == (second.immediate_reward, 0.0)
    with pytest.raises(ValueError, match="return_value"):
        _ = trajectory.complete_returns

    # A fresh graph uses the original credits plus F, without substituting G1.
    log_probabilities = torch.tensor([-0.2, -0.3, -0.4], requires_grad=True)
    actions, credits, coefficients = [], [], []
    offset = 0
    for session in trajectory.sessions:
        decisions = []
        for tag, credit in session.conversation_credits.items():
            decisions.append(
                SimpleNamespace(
                    edge=(tag.initiator_id, tag.receiver_id),
                    tag=tag,
                    scheme=tag.scheme,
                    free=True,
                    q_value=log_probabilities[offset],
                )
            )
            coefficients.append(credit + session.future_return)
            offset += 1
        actions.append(SimpleNamespace(decisions=decisions))
        credits.append(session.conversation_credits)
    loss = mixed_alpha_loss(actions, credits, trajectory.future_returns, local_labels=[{} for _ in actions], zero_labels=[{} for _ in actions], conversation_costs=[{} for _ in actions], p_mix=1., lambda_conversation=.01)
    loss.backward()
    assert torch.allclose(log_probabilities.grad, (log_probabilities.detach() - torch.tensor(coefficients))/len(coefficients))


def test_prefix_future_return_never_includes_later_shared_reward():
    runtime, proxy = tie_runtime()
    short = TerminationConfig(min_sessions=1, max_sessions=1)
    long = TerminationConfig(min_sessions=2, max_sessions=2)
    result = asyncio.run(
        runtime.run_shared_prefix(
            QueryExample("synthetic-tie", "Pick A, B or C", ("A", "B", "C"), "A"),
            (short, long),
            generation_context=GenerationContext(42, "sys-val", "synthetic-tie"),
        )
    )
    assert proxy.ties == 1 and all(item.completed for item in result.prefixes)
    first, second = result.prefixes
    assert first.sessions[0].return_value is None
    assert first.sessions[0].future_return == 0.0
    assert second.sessions[0].future_return == second.sessions[1].immediate_reward
    assert second.sessions[1].future_return == 0.0


@pytest.mark.parametrize("p_mix", [0., .75, 1.])
def test_trainer_requests_only_selected_objective_returns(p_mix):
    class SelectedReturns:
        completed = True

        def __init__(self, original):
            self.example, self.sessions = original.example, original.sessions

        def routing_eligible_for(self, requested, **kwargs):
            assert requested == "mixed_q"
            return True

        @property
        def future_returns(self):
            assert p_mix > 0, "local endpoint must not request F"
            return (0.1,)

        @property
        def complete_returns(self):
            raise AssertionError("mixed loss must not request G")
            return (0.7,)

    class SelectedHooks(Hooks):
        async def trajectory(self, component, split, *, routing_identity):
            original = await super().trajectory(component, split, routing_identity=routing_identity)
            return SelectedReturns(original)

    config = config_for(numerical=1, bootstrap=0)
    config = replace(config, training=replace(config.training, p_mix=p_mix))
    trainer = make_trainer(config)
    hooks = SelectedHooks()
    trainer.hooks = hooks
    hooks.bind(trainer)
    trainer.state.phase = "routing"
    asyncio.run(trainer._numerical_phase("routing"))
    assert trainer.state.attempts["routing"] == trainer.state.updates["routing"] == 1
    assert trainer.state.workflow_attempt_counter == len(hooks.identities) == 1
    assert float(trainer.alpha.value.detach()) != 0.0


def test_missing_later_reward_skips_whole_shaped_trajectory():
    runtime, _ = tie_runtime()
    trajectory = asyncio.run(runtime.run(QueryExample("synthetic-tie", "Pick", ("A", "B", "C"), "A")))
    returns, future = returns_and_future([0.0, None])
    trajectory.sessions = [replace(session, routing=replace(session.routing, decisions=tuple(
        SimpleNamespace(edge=(tag.initiator_id,tag.receiver_id),tag=tag,scheme=tag.scheme,free=True,
                        forced_zero=False,session=tag.session,initiator_id=tag.initiator_id,receiver_id=tag.receiver_id)
        for tag in (trace.tag for trace in session.traces)))) for session in trajectory.sessions]
    trajectory.sessions = [
        replace(session, immediate_reward=(0.0 if i == 0 else None), return_value=returns[i], future_return=future[i])
        for i, session in enumerate(trajectory.sessions)
    ]
    assert not trajectory.routing_eligible_for("mixed_q", p_mix=1.)
    assert not trajectory.selection_eligible
    assert trajectory.sessions[-1].future_return == 0.0
    with pytest.raises(ValueError, match="future_return"):
        _ = trajectory.future_returns

    class MissingLaterHooks(Hooks):
        async def trajectory(self, component, split, *, routing_identity):
            await super().trajectory(component, split, routing_identity=routing_identity)
            return trajectory

    trainer = make_trainer(config_for(numerical=1, bootstrap=0))
    hooks = MissingLaterHooks()
    trainer.hooks = hooks
    hooks.bind(trainer)
    trainer.state.phase = "routing"
    asyncio.run(trainer._numerical_phase("routing"))
    assert trainer.state.attempts["routing"] == 1 and trainer.state.updates["routing"] == 0
    assert trainer.state.workflow_attempt_counter == len(hooks.identities) == 1
    assert float(trainer.alpha.value.detach()) == 0.0
    assert trainer.state.phase_records[-1]["failures"] == [
        {"attempt": 1, "status": "required_routing_credit_unavailable"}
    ]


def test_local_endpoint_does_not_require_unrelated_conversation_credits():
    runtime, _ = tie_runtime()
    trajectory = asyncio.run(runtime.run(QueryExample("synthetic-tie", "Pick", ("A", "B", "C"), "A")))
    trajectory.sessions = [replace(session, routing=replace(session.routing, decisions=tuple(
        SimpleNamespace(edge=(tag.initiator_id,tag.receiver_id),tag=tag,scheme=tag.scheme,free=True,
                        forced_zero=False,session=tag.session,initiator_id=tag.initiator_id,receiver_id=tag.receiver_id)
        for tag in (trace.tag for trace in session.traces)))) for session in trajectory.sessions]
    trajectory.sessions = [replace(session, conversation_credits={}) for session in trajectory.sessions]
    assert trajectory.routing_eligible_for("mixed_q", p_mix=0.)
    assert not trajectory.routing_eligible_for("mixed_q", p_mix=1.)
    with pytest.raises(ValueError, match="unknown routing objective"):
        trajectory.routing_eligible_for("invalid")


def test_missing_conversation_credit_is_not_invented_as_zero():
    decision = SimpleNamespace(edge=(0,1), session=1, scheme=Scheme.REVIEWER, free=True, q_value=torch.tensor(-.2))
    with pytest.raises(ValueError, match="conversation credit unavailable"):
        mixed_alpha_loss([SimpleNamespace(decisions=[decision])], [{}], [0.],
                         local_labels=[{}], zero_labels=[{}], conversation_costs=[{}], p_mix=1., lambda_conversation=.01)
