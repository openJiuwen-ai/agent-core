"""Scientific eligibility is independent of output validity and receipt availability."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import Any

import pytest
import torch

from examples.adaptive_multi_agent_collab.learning.calibration import collect_pilot_observation
from examples.adaptive_multi_agent_collab.learning.evaluation import (
    EvaluationPlan,
    EvaluationRecord,
    _trajectory_evaluation,
    scoring_configuration,
)
from examples.adaptive_multi_agent_collab.workflows.prompts import failed_evidence, PromptOccurrence
from examples.adaptive_multi_agent_collab.providers.resources import ResourceFailure, PolicyStopped, AccountingFailure
from examples.adaptive_multi_agent_collab.schemas import AgentCallKind, QueryExample, RoutingMode, Scheme, UsageRecord
from examples.adaptive_multi_agent_collab.learning.training import update_weighting
from examples.adaptive_multi_agent_collab.models.policies import RoutingRolloutIdentity
from examples.adaptive_multi_agent_collab.tests.test_runtime import _matrix, _runtime
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import Hooks, config_for, make_trainer
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment


class ReceiptAgent:
    def __init__(self, delegate, *, missing=None, provider_failure=False, initial_failure=False):
        self.delegate = delegate
        self.missing = missing
        self.provider_failure = provider_failure
        self.initial_failure = initial_failure
        self.requests = []
        self.started = []
        self.finished = []

    def begin_logical_request(self, request):
        token = len(self.started)
        self.started.append(request)
        return token

    def finish_logical_request(self, token):
        self.finished.append(token)

    async def invoke(self, request):
        self.requests.append(request)
        if self.initial_failure and request.kind is AgentCallKind.INITIAL:
            raise ResourceFailure("provider unavailable", UsageRecord(model_calls=1))
        if request.kind is AgentCallKind.ROLE:
            usage = UsageRecord(
                input_tokens=5, output_tokens=2, model_calls=1, monetary_cost=0.01, paid_monetary_cost=0.01
            )
            if self.missing == "billing":
                usage = replace(usage, paid_monetary_cost=None)
            elif self.missing == "categories":
                usage = replace(
                    usage,
                    raw_metadata={
                        "accounting_complete": False,
                        "missing_usage": ["reasoning_tokens", "cache_tokens", "is_byok"],
                    },
                )
            elif self.missing == "scientific":
                usage = replace(usage, input_tokens=None, monetary_cost=None, paid_monetary_cost=None)
            if self.provider_failure:
                raise ResourceFailure("isolated ambiguous provider failure", usage)
            return replace(await self.delegate.invoke(request), usage=usage, attempts=())
        return await self.delegate.invoke(request)


def run_case(*, missing=None, provider_failure=False, gold="A"):
    runtime, agents = _runtime(2, {}, {1: _matrix(2, {(0, 1): Scheme.REVIEWER})})
    proxy = ReceiptAgent(agents[1], missing=missing, provider_failure=provider_failure)
    runtime.agents[1] = proxy
    trajectory = asyncio.run(runtime.run(QueryExample("q", "Pick A or B", ("A", "B"), gold), partition="train"))
    return runtime, trajectory, proxy


@pytest.mark.parametrize("provider_failure", [False, True])
def test_complete_fallback_keeps_defined_routing_credit_with_missing_paid_bill(provider_failure):
    _, trajectory, _ = run_case(missing="billing", provider_failure=provider_failure)
    assert trajectory.completed and trajectory.final_answer is not None
    assert trajectory.initial_answers == ("A", "B")
    assert trajectory.sessions[0].resolved_answers == ("A", "B")
    assert not trajectory.accounting_complete
    assert trajectory.routing_eligible and trajectory.weighting_eligible and trajectory.selection_eligible
    assert trajectory.complete_returns and trajectory.future_returns
    if provider_failure:
        assert trajectory.sessions[0].traces[0].safeguard_reason
        assert trajectory.operational_halt is None
        assert all(not item.failed for item in trajectory.prompt_occurrences)


def test_missing_scientific_cost_keeps_outputs_and_w_loss_but_no_alpha_or_selection():
    runtime, trajectory, _ = run_case(missing="scientific")
    assert trajectory.completed and trajectory.weighting_eligible
    assert not trajectory.routing_eligible and not trajectory.selection_eligible
    assert trajectory.sessions[0].normalized_cost is None
    assert trajectory.sessions[0].return_value is None
    assert trajectory.sessions[0].conversation_credits == {}
    per_query = _trajectory_evaluation(trajectory, seed_replay_supported=False)
    from examples.adaptive_multi_agent_collab.config import SelectionConfig

    config = replace(runtime.config, selection=SelectionConfig(1.0))
    plan = EvaluationPlan(("q",), "wval", 42, "comp-val", scoring_configuration=scoring_configuration(config))
    record = EvaluationRecord(plan, (per_query,), "negative_weighting_nll")
    assert record.score is not None
    assert record.mean_normalized_cost is None
    assert EvaluationRecord(plan, (per_query,)).score is None
    assert EvaluationRecord.from_dict(record.to_dict()) == record
    observation = collect_pilot_observation(trajectory)
    assert observation.successful and not observation.accounting_complete
    assert observation.scientific_eligibility == {"routing": False, "weighting": True, "selection": False}


def test_missing_native_categories_are_preserved_without_discarding_defined_objective_cost():
    _, trajectory, _ = run_case(missing="categories")
    assert trajectory.completed and trajectory.routing_eligible and trajectory.selection_eligible
    assert not trajectory.accounting_complete
    assert set(trajectory.usage.missing_coordinates) == {"reasoning_tokens", "cache_tokens", "is_byok"}
    event = next(event for event in trajectory.sessions[0].history if event.event == "conversation-complete")
    assert set(event.usage_missing) == {"reasoning_tokens", "cache_tokens", "is_byok"}
    assert event.normalized_cost is not None


def test_history_missing_indicators_are_immutable_and_reasoning_failure_evidence_survives():
    _, trajectory, _ = run_case(missing="scientific", gold="B")
    event = next(event for event in trajectory.sessions[0].history if event.event == "conversation-complete")
    before = asdict(event)
    assert "input_tokens" in event.usage_missing and "paid_monetary_cost" in event.usage_missing
    # A later external receipt creates a new value; it cannot rewrite observed events.
    reconciled = replace(
        event.usage,
        input_tokens=5,
        monetary_cost=0.01,
        paid_monetary_cost=0.01,
        raw_metadata={"accounting_complete": True, "missing_usage": []},
    )
    assert reconciled.accounting_complete
    assert asdict(event) == before
    role = next(item for item in trajectory.prompt_occurrences if item.component.startswith("role:"))
    assert role.complete_return is None
    assert failed_evidence(role.component, (role,)) == (role,)


def test_provider_only_failure_is_not_prompt_quality_evidence():
    _, trajectory, _ = run_case(provider_failure=True, gold="B")
    assert trajectory.prompt_occurrences
    assert all(item.trace["provider_only_failure"] for item in trajectory.prompt_occurrences)
    assert all(not item.failed for item in trajectory.prompt_occurrences)


@pytest.mark.parametrize("failed_agent", [0, 1])
def test_initial_provider_failure_ends_only_query_and_does_not_initialize_next_agent(failed_agent):
    runtime, agents = _runtime(3, {}, {1: _matrix(3)})
    proxies = {index: ReceiptAgent(agent, initial_failure=index == failed_agent) for index, agent in agents.items()}
    runtime.agents.update(proxies)
    example = QueryExample("q", "Pick", ("A", "B", "C"), "A")
    failed = asyncio.run(runtime.run(example))
    assert not failed.completed and failed.status == "initialization-failed"
    assert failed.operational_halt is None and not proxies[failed_agent + 1].requests
    assert len(failed.initial_records) == failed_agent + 1
    assert failed.usage.model_calls == failed_agent + 1
    proxies[failed_agent].initial_failure = False
    completed = asyncio.run(runtime.run(replace(example, example_id="next")))
    assert completed.completed


def test_unknown_shared_judge_cost_is_not_duplicated_and_keeps_valid_shared_answer():
    runtime, agents = _runtime(
        3,
        {2: {"judge": {"answer": "A", "explanation": "Choose the first submitted answer."}}},
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
    )

    class UnknownJudge(ReceiptAgent):
        async def invoke(self, request):
            output = await super().invoke(request)
            if request.kind is AgentCallKind.JUDGE:
                return replace(
                    output,
                    attempts=(),
                    usage=UsageRecord(
                        input_tokens=None, output_tokens=2, model_calls=1, monetary_cost=None, paid_monetary_cost=None
                    ),
                )
            return output

    runtime.agents[2] = UnknownJudge(agents[2])
    result = asyncio.run(runtime.run(QueryExample("q", "Pick", ("A", "B", "C"), "A")))
    assert result.completed and result.weighting_eligible and not result.routing_eligible
    assert result.sessions[0].resolved_answers == ("A", "A", "C")
    assert all(trace.normalized_cost is None for trace in result.sessions[0].traces)
    assert result.sessions[0].usage.model_calls == 1
    assert result.usage.model_calls == 4
    judges = [event for event in result.sessions[0].history if event.event == "judge-adjudication"]
    assert len(judges) == 1 and judges[0].usage.model_calls == 1


def test_real_weighting_gradient_updates_from_complete_trajectory_with_missing_cost(monkeypatch):
    runtime, agents = _runtime(2, {}, {1: _matrix(2, {(0, 1): Scheme.REVIEWER})})
    runtime.agents[1] = ReceiptAgent(agents[1], missing="scientific")
    monkeypatch.setattr(
        runtime.routing_policy,
        "route_training",
        lambda *args, session, **kwargs: runtime.routing_policy._action(session, RoutingMode.TRAIN),
    )
    trajectory = asyncio.run(
        runtime.run(
            QueryExample("q", "Pick", ("A", "B"), "A"),
            routing_mode=RoutingMode.TRAIN,
            routing_identity=RoutingRolloutIdentity(42, "q", 0),
        )
    )
    assert trajectory.completed and trajectory.weighting_eligible and not trajectory.routing_eligible
    model = runtime.weighting_model
    before = [parameter.detach().clone() for parameter in model.parameters()]
    loss = update_weighting(model, torch.optim.SGD(model.parameters(), lr=0.01), trajectory.sessions, "A", ("A", "B"))
    assert loss is not None and any(
        not torch.equal(old, parameter) for old, parameter in zip(before, model.parameters())
    )


def test_schema_repair_finishes_one_logical_request():
    runtime, agents = _runtime(
        2,
        {
            1: {
                "role": [
                    {"status": "invalid", "feedback": "wrong schema"},
                    {"status": "complete", "feedback": "Checked."},
                ]
            }
        },
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
    )
    proxy = ReceiptAgent(agents[1])
    runtime.agents[1] = proxy
    result = asyncio.run(runtime.run(QueryExample("q", "Pick", ("A", "B"), "A")))
    assert result.completed
    role = [request for request in proxy.requests if request.kind is AgentCallKind.ROLE]
    assert len(role) == 2
    assert role[0].context["provider_logical_request_id"] == role[1].context["provider_logical_request_id"]
    assert len(proxy.started) == len(proxy.finished) == 2  # initialization and repaired role


@pytest.mark.parametrize("hard_peer_failure", [False, True])
def test_operational_drain_waits_for_dispatched_peers_and_preserves_their_outputs(hard_peer_failure):
    async def scenario():
        runtime, agents = _runtime(
            3,
            {1: {"role": {"status": "continue", "feedback": "Revise this answer."}}} if hard_peer_failure else {},
            {1: _matrix(3, {(0, 1): Scheme.REVIEWER, (2, 2): Scheme.SELF})},
        )
        slow_started = asyncio.Event()
        stop: dict[str, Any] = {}
        completed_roles = []

        class DrainingAgent(ReceiptAgent):
            @property
            def dispatch_stop_reason(self):
                return stop or None

            def begin_logical_request(self, request):
                if stop:
                    raise PolicyStopped(stop["reason"])
                return super().begin_logical_request(request)

            async def invoke(self, request):
                if request.kind is AgentCallKind.ROLE:
                    if request.agent_id == 2:
                        slow_started.set()
                        await asyncio.sleep(0.02)
                        if hard_peer_failure:
                            raise AccountingFailure("new serving binding violation", UsageRecord(model_calls=1))
                    else:
                        await slow_started.wait()
                    result = await super().invoke(request)
                    completed_roles.append(request.agent_id)
                    if request.agent_id == 1:
                        stop.update(reason="declared unresolved threshold", operational_drain=True, hard_failure=False)
                    return result
                return await super().invoke(request)

        runtime.agents.update({index: DrainingAgent(agent) for index, agent in agents.items()})
        result = await runtime.run(QueryExample("q", "Pick", ("A", "B", "C"), "A"))
        if hard_peer_failure:
            assert result.status == "accounting-failed" and result.operational_halt["fatal"] is True
            assert result.error == "new serving binding violation"
            assert result.failed_session_traces and not result.completed
            return
        assert completed_roles == [1, 2]
        assert result.status == "policy-stopped" and not result.completed
        assert len(result.sessions) == 1 and all(trace.completed for trace in result.sessions[0].traces)
        assert result.operational_halt["fatal"] is False
        assert result.operational_halt["operational_drain"] is True
        assert result.execution_counters["call_types"]["role"]["dispatched"] == 2
        assert all("cancelled" not in record.get("error", "") for record in result.call_records)

    asyncio.run(scenario())


def test_routing_ineligible_attempts_consume_budget_without_replacement_or_update(tmp_path):
    class MissingCreditHooks(Hooks):
        async def trajectory(self, component, split, *, routing_identity):
            result = await super().trajectory(component, split, routing_identity=routing_identity)
            result.routing_eligible = False
            return result

    config = config_for(numerical=2, bootstrap=0)
    trainer = make_trainer(config)
    hooks = MissingCreditHooks()
    trainer.hooks = hooks
    hooks.bind(trainer)
    trainer.checkpoint_path = tmp_path / "skipped-routing.pt"
    trainer.state.phase = "routing"
    asyncio.run(trainer._numerical_phase("routing"))
    assert trainer.state.attempts["routing"] == 2
    assert trainer.state.updates["routing"] == 0
    assert trainer.state.workflow_attempt_counter == 2
    assert len(hooks.identities) == 2
    assert float(trainer.alpha.value.detach()) == 0
    resumed = make_trainer(config)
    resumed_hooks = MissingCreditHooks()
    resumed.resume_from_checkpoint(trainer.checkpoint_path, hooks=resumed_hooks)
    asyncio.run(resumed._numerical_phase("routing"))
    assert resumed.state.workflow_attempt_counter == 2 and len(resumed_hooks.identities) == 2
    assert resumed.state.attempts["routing"] == 2 and resumed.state.updates["routing"] == 0


def test_prompt_training_batch_preserves_reasoning_failure_when_other_slot_has_provider_failure(monkeypatch):
    trainer, hooks, _, _, _ = _build_offline_experiment()
    hooks.prompt_train_batch_size = 2
    component = next(iter(trainer.gamma.components))
    calls = []
    usable = []

    class BatchRuntime:
        async def run(self, example, **kwargs):
            calls.append(example.example_id)
            complete = len(calls) == 1
            occurrence = PromptOccurrence(
                component,
                "retained input",
                "incorrect reasoning",
                "no",
                "yes",
                {},
                None,
                example_id=example.example_id,
            )
            if complete:
                usable.append(occurrence)
            return SimpleNamespace(
                completed=complete,
                status="completed" if complete else "initialization-failed",
                error=None if complete else "provider unavailable",
                accounting_complete=False,
                prompt_occurrences=(occurrence,) if complete else (),
                call_records=(),
                operational_halt=None,
            )

    monkeypatch.setattr(hooks, "_runtime", lambda *args: BatchRuntime())
    evidence = asyncio.run(hooks.prompt_evidence(component))
    assert evidence == tuple(usable) and failed_evidence(component, evidence) == evidence
    assert len(calls) == hooks.sample_counters["prompt"] == trainer.state.workflow_attempt_counter == 2
    records = hooks.state_dict()["prompt_evidence_records"]
    assert [item["completed"] for item in records] == [True, False]
    assert [item["retained_occurrences"] for item in records] == [1, 0]
    hooks.validate_state(hooks.state_dict())


def test_paired_cost_unavailability_keeps_every_query_and_prevents_acceptance():
    from examples.adaptive_multi_agent_collab.tests.test_training_prompts import (
        FakeProposer,
        prompt_plan,
        evaluation_record,
    )
    from examples.adaptive_multi_agent_collab.workflows.prompts import seed_prompt_collection
    from examples.adaptive_multi_agent_collab.learning.training import PromptOptimizer

    collection = seed_prompt_collection()
    component = next(iter(collection.components))
    plan = prompt_plan()
    baseline = evaluation_record(plan)
    proposed = replace(baseline, examples=(replace(baseline.examples[0], normalized_cost=None), baseline.examples[1]))
    calls = []

    async def evaluate(candidate, received_plan):
        assert received_plan is plan
        calls.append(candidate.to_dict())
        return baseline if len(calls) == 1 else proposed

    async def scenario():
        optimizer = PromptOptimizer(FakeProposer())
        await optimizer.begin_phase(collection, plan, "condition", evaluate)
        occurrence = PromptOccurrence(component, "input", "wrong reasoning", "no", "yes", {}, None)
        return await optimizer.step(
            collection, component, (occurrence,), condition="condition", iteration_id="0", evaluate=evaluate
        )

    result = asyncio.run(scenario())
    assert not result.accepted and result.collection is collection and result.reason == "validation_failed"
    assert len(calls) == 2 and proposed.score is None
    assert tuple(item.example_id for item in proposed.examples) == plan.ordered_example_ids
    assert proposed.per_example_scores[0] is None and proposed.per_example_scores[1] is not None


def test_unavailable_system_score_is_recorded_without_checkpoint_comparison(tmp_path):
    class UnavailableSystem(Hooks):
        async def system_validation(self):
            record = await super().system_validation()
            return replace(record, examples=(replace(record.examples[0], normalized_cost=None), record.examples[1]))

    trainer = make_trainer(config_for(bootstrap=0))
    state = asyncio.run(trainer.fit(UnavailableSystem(), checkpoint_path=tmp_path / "unavailable.pt"))
    assert state.phase == "failed" and state.best_system_score is None and not state.eligible_checkpoints
    record = state.phase_records[-1]
    assert record["reason"] == "selection_cost_unavailable" and len(record["evaluation"]["examples"]) == 2
    assert (tmp_path / "unavailable.pt").is_file()
