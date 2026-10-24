"""Offline credit, numerical objective, and single-proposal prompt regressions."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import math
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from examples.adaptive_multi_agent_collab.config import Config, NetworkConfig, SelectionConfig
from examples.adaptive_multi_agent_collab.models.encoders import FixedBaseEncoders, offline_encoder_configuration
from examples.adaptive_multi_agent_collab.learning.evaluation import (
    EvaluationPlan,
    EvaluationRecord,
    ExampleEvaluation,
    scoring_configuration,
)
from examples.adaptive_multi_agent_collab.models.policies import WeightingModel
from examples.adaptive_multi_agent_collab.workflows.prompts import (
    OptimizationAttempt,
    PromptOccurrence,
    PromptProposal,
    failed_evidence,
    seed_prompt_collection,
    protocol_instructions,
    SCHEME_SEMANTICS,
)
from examples.adaptive_multi_agent_collab.schemas import (
    ConversationTag,
    SampledDecision,
    Scheme,
    TerminalRecord,
    UsageRecord,
)
from examples.adaptive_multi_agent_collab.learning.training import (
    NumericalFailure,
    PromptOptimizer,
    WeightingDiagnostics,
    allocate_shared_judge_cost,
    conversation_credits,
    frozen,
    modal_answers,
    remove_terminal_by_tag,
    resolved_candidate_scores,
    returns_and_future,
    session_rewards,
    mixed_alpha_loss,
    support_components,
    system_support,
    update_weighting,
    validation_plateaued,
    weighting_loss_from_sessions,
)


def tag(initiator: int, receiver: int, scheme: Scheme = Scheme.REVIEWER) -> ConversationTag:
    return ConversationTag(1, initiator, receiver, scheme)


def occurrence(component: str, *, outcome: str = "B", gold: str = "A") -> PromptOccurrence:
    return PromptOccurrence(component, "rendered", "response", outcome, gold, {}, -1.0)


def test_modal_and_support_terms_match_hand_calculation() -> None:
    support = support_components(("A", "A", "B"), "A", ("A", "B"))
    assert support.rho == pytest.approx(2 / 3)
    assert support.mu == 1.0
    assert support.support == pytest.approx(5 / 6)
    assert support.modes == ("A",)

    tied = support_components(("B", "A"), "A", ("A", "B"))
    assert tied.rho == tied.mu == tied.support == 0.5
    assert tied.modes == ("A", "B")
    assert modal_answers(("B", "A"), ("A", "B")) == ("A", "B")


def test_system_value_rewards_returns_and_future_match_hand_calculation() -> None:
    weights = torch.tensor((0.6, 0.4), requires_grad=True)
    value = system_support(weights, (0.5, 1.0))
    assert value.item() == pytest.approx(0.7)
    value.backward()
    assert weights.grad.tolist() == pytest.approx([0.5, 1.0])

    rewards = session_rewards((0.2, 0.5, 0.4), (0.1, 0.2), 0.5)
    returns, future = returns_and_future(rewards)
    assert rewards == pytest.approx([0.25, -0.2])
    assert returns == pytest.approx([0.05, -0.2])
    assert future == pytest.approx([-0.2, 0.0])


def test_leave_one_tagged_conversation_out_preserves_equal_answers() -> None:
    first, second = tag(0, 1), tag(0, 2)
    records = (
        TerminalRecord(1, 0, "A", first),
        TerminalRecord(1, 0, "A", second),
    )
    reduced = remove_terminal_by_tag(records, first)
    assert reduced == [records[1]]
    with pytest.raises(KeyError):
        remove_terminal_by_tag(records, tag(1, 2))


def test_conversation_credit_fallback_cost_and_detached_weighting() -> None:
    positive, negative = tag(0, 1), tag(0, 2)
    weights = torch.tensor((0.6, 0.4), requires_grad=True)
    raw = {
        0: (TerminalRecord(1, 0, "A", positive), TerminalRecord(1, 0, "B", negative)),
        1: (),
    }
    credits, supports, value = conversation_credits(
        raw,
        {0: "B", 1: "A"},
        weights,
        "A",
        {positive: 0.2, negative: 0.4},
        lambda_conversation=0.1,
        answer_order=("A", "B"),
    )

    assert [item.support for item in supports] == pytest.approx([0.5, 1.0])
    assert value == pytest.approx(0.7)
    assert credits[positive] == pytest.approx(0.6 * (0.5 - 0.0) - 0.02)
    assert credits[negative] == pytest.approx(0.6 * (0.5 - 1.0) - 0.04)
    assert weights.grad is None


def test_judge_consultation_credit_and_shared_cost_allocation() -> None:
    judge_tags = (tag(0, 2, Scheme.JUDGE), tag(1, 2, Scheme.JUDGE))
    assert allocate_shared_judge_cost(0.9, judge_tags) == {
        judge_tags[0]: pytest.approx(0.45),
        judge_tags[1]: pytest.approx(0.45),
    }
    raw = {
        0: (TerminalRecord(1, 0, "A", judge_tags[0]),),
        1: (TerminalRecord(1, 1, "A", judge_tags[1]),),
    }
    credits, _, _ = conversation_credits(
        raw,
        {0: "B", 1: "B"},
        (0.5, 0.5),
        "A",
        dict.fromkeys(judge_tags, 0.45),
        lambda_conversation=0.2,
    )
    assert set(credits) == set(judge_tags)
    assert all(value == pytest.approx(0.5 - 0.09) for value in credits.values())


def _decision(
    log_probability: torch.Tensor,
    scheme: Scheme,
    *,
    free: bool,
    conversation_tag: ConversationTag | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        edge=(0, 1),
        scheme=scheme,
        log_probability=log_probability,
        q_value=log_probability,
        free=free,
        forced_zero=not free,
        tag=conversation_tag,
    )


def test_mixed_group_endpoint_real_decision_tag_and_raw_q_gradient():
    q = torch.tensor(-.4, requires_grad=True)
    decision = SampledDecision(session=1, position=0, initiator_id=0, receiver_id=1,
        scheme=Scheme.REVIEWER, legal_schemes=(Scheme.ZERO, Scheme.REVIEWER), probability=.5,
        log_probability=torch.tensor(-.7), q_value=q, free=True, forced_zero=False,
        remaining_out=(0,1), remaining_in=(1,0))
    credit_tag = ConversationTag(1,0,1,Scheme.REVIEWER)
    loss = mixed_alpha_loss([SimpleNamespace(decisions=[decision])], [{credit_tag:.35}], [.15],
        local_labels=[{}], zero_labels=[{}], conversation_costs=[{}], p_mix=1., lambda_conversation=.1)
    loss.backward()
    assert loss.item() == pytest.approx(.5 * (-.4-.5)**2)
    assert q.grad.item() == pytest.approx(-.9)


def test_realized_candidate_mass_and_direct_w_loss() -> None:
    weights = torch.tensor((0.8, 0.2), requires_grad=True)
    labels, scores = resolved_candidate_scores(weights, ("A", "B"), ("A", "B", "C"))
    assert labels == ("A", "B")
    assert scores.tolist() == pytest.approx([0.8, 0.2])
    session = SimpleNamespace(resolved_answers=("A", "B"), weights=weights)
    loss, informative = weighting_loss_from_sessions((session,), "A", ("A", "B", "C"))
    assert informative == 1
    assert loss is not None and loss.item() == pytest.approx(-math.log(0.8))


def test_w_loss_is_the_mean_over_informative_sessions() -> None:
    sessions = (
        SimpleNamespace(
            resolved_answers=("A", "B"),
            weights=torch.tensor((0.8, 0.2)),
        ),
        SimpleNamespace(
            resolved_answers=("A", "B"),
            weights=torch.tensor((0.25, 0.75)),
        ),
    )

    loss, informative = weighting_loss_from_sessions(sessions, "A", ("A", "B"))

    assert informative == 2
    assert loss is not None
    assert loss.item() == pytest.approx((-math.log(0.8) - math.log(0.25)) / 2)


def test_weighting_diagnostics_classifies_every_completed_labelled_session() -> None:
    diagnostics = WeightingDiagnostics()
    sessions = (
        SimpleNamespace(resolved_answers=("A", "A", "A")),
        SimpleNamespace(resolved_answers=("B", "B", "B")),
        SimpleNamespace(resolved_answers=("B", "C", "B")),
        SimpleNamespace(resolved_answers=("A", "B", "B")),
    )

    diagnostics.record_attempt(sessions, "A", successful=True)

    assert diagnostics.total_completed_sessions == 4
    assert diagnostics.unanimous_sessions == 2
    assert diagnostics.gold_absent_sessions == 2
    assert diagnostics.informative_sessions == 1
    assert diagnostics.unanimous_correct == 1
    assert diagnostics.unanimous_wrong == 1
    assert diagnostics.disagreement_all_wrong == 1
    assert (
        diagnostics.unanimous_correct
        + diagnostics.unanimous_wrong
        + diagnostics.disagreement_all_wrong
        + diagnostics.informative_sessions
        == diagnostics.total_completed_sessions
    )
    assert diagnostics.attempted_w_updates == 1
    assert diagnostics.successful_w_updates == 1
    assert diagnostics.skipped_w_updates == 0
    assert diagnostics.unanimous_rate == pytest.approx(0.5)
    assert diagnostics.gold_absent_rate == pytest.approx(0.5)
    assert diagnostics.informative_rate == pytest.approx(0.25)


def test_weighting_diagnostics_overlap_and_zero_denominators_are_explicit() -> None:
    empty = WeightingDiagnostics()
    assert empty.unanimous_rate == 0.0
    assert empty.gold_absent_rate == 0.0
    assert empty.informative_rate == 0.0
    assert empty.successful_update_rate == 0.0
    assert empty.skipped_update_rate == 0.0

    overlapping = WeightingDiagnostics()
    overlapping.record_attempt(
        (SimpleNamespace(resolved_answers=("B", "B")),),
        "A",
        successful=False,
    )
    assert overlapping.total_completed_sessions == 1
    assert overlapping.unanimous_sessions == 1
    assert overlapping.gold_absent_sessions == 1
    assert overlapping.unanimous_wrong == 1
    assert overlapping.unanimous_rate == 1.0
    assert overlapping.gold_absent_rate == 1.0
    assert overlapping.to_dict()["skipped_update_rate"] == 1.0


def test_w_loss_skips_unanimous_and_gold_absent_sessions() -> None:
    sessions = (
        SimpleNamespace(resolved_answers=("A", "A", "A"), weights=torch.ones(3) / 3),
        SimpleNamespace(resolved_answers=("B", "B", "C"), weights=torch.ones(3) / 3),
    )
    assert weighting_loss_from_sessions(sessions, "A") == (None, 0)


def test_w_one_step_update_changes_parameters_and_no_information_skips() -> None:
    torch.manual_seed(5)
    encoder = FixedBaseEncoders(3, configuration=offline_encoder_configuration())
    network = NetworkConfig(
        representation_dim=16, feedforward_dim=24, answer_byte_dim=8, transformer_layers=1, transformer_heads=4
    )
    model = WeightingModel(3, encoder.history_dimension, configuration=network)
    optimizer = torch.optim.SGD(model.parameters(), lr=0.2)
    query, history = encoder.encode_query("Question"), encoder.encode_history(())
    output = model(query, history)
    session = SimpleNamespace(
        resolved_answers=("A", "B", "B"),
        weights=output.weights,
        weight_logits=output.logits,
    )
    diagnostics = WeightingDiagnostics()
    correct_mass_before = float(output.weights[0].detach())
    competing_mass_before = float(output.weights[1:].sum().detach())
    before = [parameter.detach().clone() for parameter in model.parameters()]
    loss = update_weighting(
        model,
        optimizer,
        (session,),
        "A",
        ("A", "B"),
        diagnostics=diagnostics,
    )
    updated = model(query, history).weights
    assert loss is not None
    assert any(not torch.equal(old, new) for old, new in zip(before, model.parameters(), strict=True))
    assert float(updated[0].detach()) > correct_mass_before
    assert float(updated[1:].sum().detach()) < competing_mass_before

    unanimous = SimpleNamespace(resolved_answers=("A", "A", "A"), weights=model(query, history).weights)
    before_skip = [parameter.detach().clone() for parameter in model.parameters()]
    assert (
        update_weighting(
            model,
            optimizer,
            (unanimous,),
            "A",
            diagnostics=diagnostics,
        )
        is None
    )
    assert all(torch.equal(old, new) for old, new in zip(before_skip, model.parameters(), strict=True))
    assert diagnostics.total_completed_sessions == 2
    assert diagnostics.informative_sessions == 1
    assert diagnostics.unanimous_correct == 1
    assert diagnostics.attempted_w_updates == 2
    assert diagnostics.successful_w_updates == 1
    assert diagnostics.skipped_w_updates == 1
    assert diagnostics.successful_update_rate == pytest.approx(0.5)
    assert diagnostics.skipped_update_rate == pytest.approx(0.5)


@pytest.mark.parametrize("gradient_scope", ("frozen", "disconnected"))
def test_informative_w_without_permitted_gradient_records_skip(gradient_scope: str) -> None:
    model = nn.ParameterList((nn.Parameter(torch.tensor((0.2, -0.2))),))
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    model[0].square().sum().backward()
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    before_parameter = model[0].detach().clone()
    before_state = copy.deepcopy(optimizer.state_dict())
    assert before_state["state"][0]["step"] == 1
    assert before_state["state"][0]["exp_avg"].abs().sum() > 0
    if gradient_scope == "frozen":
        model[0].requires_grad_(False)
        logits = model[0]
    else:
        logits = model[0].detach().clone().requires_grad_()
    session = SimpleNamespace(
        resolved_answers=("A", "B"), weights=logits.softmax(0), weight_logits=logits
    )
    diagnostics = WeightingDiagnostics()

    assert update_weighting(model, optimizer, (session,), "A", diagnostics=diagnostics) is None

    assert torch.equal(model[0], before_parameter)
    after_state = optimizer.state_dict()
    assert after_state["param_groups"] == before_state["param_groups"]
    for key, value in before_state["state"][0].items():
        assert torch.equal(after_state["state"][0][key], value)
    assert diagnostics.informative_sessions == diagnostics.total_completed_sessions == 1
    assert diagnostics.unanimous_sessions == diagnostics.gold_absent_sessions == 0
    assert diagnostics.attempted_w_updates == diagnostics.skipped_w_updates == 1
    assert diagnostics.successful_w_updates == diagnostics.failed_w_updates == 0


def test_informative_w_numerical_failure_remains_failed_not_skipped() -> None:
    model = nn.ParameterList((nn.Parameter(torch.tensor((0.2, -0.2))),))
    optimizer = torch.optim.Adam(model.parameters())
    logits = model[0] * float("nan")
    session = SimpleNamespace(
        resolved_answers=("A", "B"), weights=logits.softmax(0), weight_logits=logits
    )
    diagnostics = WeightingDiagnostics()

    with pytest.raises(NumericalFailure, match="nonfinite numerical loss"):
        update_weighting(model, optimizer, (session,), "A", diagnostics=diagnostics)

    assert not optimizer.state
    assert diagnostics.informative_sessions == diagnostics.total_completed_sessions == 1
    assert diagnostics.attempted_w_updates == diagnostics.failed_w_updates == 1
    assert diagnostics.successful_w_updates == diagnostics.skipped_w_updates == 0


def test_frozen_restores_original_requires_grad_flags() -> None:
    module = nn.Sequential(nn.Linear(2, 2), nn.Linear(2, 1))
    module[1].bias.requires_grad_(False)
    original = [parameter.requires_grad for parameter in module.parameters()]
    with frozen(module):
        assert not any(parameter.requires_grad for parameter in module.parameters())
    assert [parameter.requires_grad for parameter in module.parameters()] == original


def test_prompt_registry_constraints_and_failed_evidence() -> None:
    collection = seed_prompt_collection()
    assert len(collection.components) == 15
    assert collection.components["judge:S_J"].required_placeholders == frozenset({"submissions"})
    key = "role:S_V"
    with pytest.raises(ValueError, match="missing placeholder"):
        collection.replace_template(key, 'Return "feedback" and "status" only.')
    relevant_failure = occurrence(key)
    assert failed_evidence(key, (relevant_failure, occurrence(key, outcome="A"), occurrence("tie"))) == (
        relevant_failure,
    )


@pytest.mark.parametrize("scheme", ("S_self", "S_D", "S_J", "S_T", "S_V"))
def test_every_named_scheme_retains_its_fixed_protocol_contract(scheme: str) -> None:
    collection = seed_prompt_collection()
    component = "judge:S_J" if scheme == "S_J" else f"role:{scheme}"
    original = protocol_instructions(component, {"scheme": scheme})
    edited = collection.replace_template(component, collection.components[component].template + "\nEditorial wording.")
    assert SCHEME_SEMANTICS[scheme] in original
    assert protocol_instructions(component, {"scheme": scheme}) == original
    assert edited.components[component].protected_scaffold == collection.components[component].protected_scaffold


@pytest.mark.parametrize("component", tuple(seed_prompt_collection().components))
def test_every_editable_prompt_component_preserves_structural_constraints(
    component: str,
) -> None:
    collection = seed_prompt_collection()
    original = collection.components[component]
    edited = collection.replace_template(component, original.template + "\nEditorial wording.")
    replacement = edited.components[component]
    assert replacement.purpose == original.purpose
    assert replacement.required_placeholders == original.required_placeholders
    assert replacement.required_literals == original.required_literals
    rendered = edited.render(
        component,
        **{name: name for name in replacement.required_placeholders},
    )
    assert rendered.startswith("Component:")
    assert "Editorial wording." in rendered
    assert all(marker in rendered for marker in replacement.required_literals)
    with pytest.raises(ValueError):
        collection.replace_template(component, "Unconstrained replacement")


def test_validation_plateau_rule() -> None:
    assert not validation_plateaued((0.1, 0.2), patience=2, epsilon=0.01)
    assert validation_plateaued((0.1, 0.2, 0.205, 0.206), patience=2, epsilon=0.01)
    assert not validation_plateaued((0.1, 0.2, 0.3), patience=1, epsilon=0.01)


class FakeProposer:
    """Explicit protocol fixture returning one proposal for one failed training batch."""

    def __init__(self, proposal=None):
        self.proposal = proposal
        self.calls = []

    def metadata(self):
        return {"backend": "offline_test_proposal", "protocol": "single_proposal"}

    async def propose(self, spec, evidence, *, iteration_id):
        self.calls.append((spec.key, tuple(evidence), iteration_id))
        if isinstance(self.proposal, Exception):
            raise self.proposal
        if self.proposal is not None:
            return self.proposal
        return PromptProposal(spec.template + "\nReusable improvement.")


def prompt_plan(ids=("validation-a", "validation-b")):
    return EvaluationPlan(
        ids,
        "promptval",
        17,
        "comp-val",
        {"temperature": 0, "engine_revision": "fixed"},
        scoring_configuration(Config(selection=SelectionConfig(1.0))),
    )


def evaluation_record(plan, returns=(0.2, 0.4), *, cost=2.0, objective="selection"):
    return EvaluationRecord(
        plan,
        tuple(
            ExampleEvaluation(identifier, "A", 1, 1.0 - value, True, 3, 24, complete_return=-99.0)
            for identifier, value in zip(plan.ordered_example_ids, returns, strict=True)
        ),
        objective,
    )


class RecordedEvaluator:
    def __init__(self, baseline, *, returns=(0.4, 0.6), fault=None):
        self.baseline = baseline.to_dict()
        self.proposed_returns = returns
        self.fault = fault
        self.calls = []

    async def __call__(self, collection, plan):
        self.calls.append((collection.to_dict(), plan))
        if collection.to_dict() == self.baseline:
            return evaluation_record(plan)
        if self.fault is not None:
            raise self.fault
        return evaluation_record(plan, self.proposed_returns, cost=4.0)


def test_prompt_single_proposal_accepts_any_strict_improvement_and_caches_full_records() -> None:
    async def exercise():
        collection = seed_prompt_collection()
        proposer = FakeProposer()
        optimizer = PromptOptimizer(proposer)
        evaluator = RecordedEvaluator(collection, returns=(0.2, 0.40000001))
        plan = prompt_plan()
        baseline = await optimizer.begin_phase(collection, plan, "fixed", evaluator)
        result = await optimizer.step(
            collection, "tie", (occurrence("tie"),), condition="fixed", iteration_id="one", evaluate=evaluator
        )
        assert result.accepted and result.reason == "accepted"
        assert result.proposed_score - baseline.score < 1e-4
        assert result.paired_improvements == pytest.approx((0.0, 1e-8))
        assert len(proposer.calls) == 1 and len(evaluator.calls) == 2
        assert all(call[1] is plan for call in evaluator.calls)
        assert baseline.examples[0].normalized_cost == 0.8
        assert result.proposed.examples[0].normalized_cost == 0.8
        assert result.proposed.examples[0].model_calls == 3
        # The accepted proposal becomes the cached baseline; no extra workflow occurs.
        next_result = await optimizer.step(
            result.collection, "tie", (), condition="fixed", iteration_id="two", evaluate=evaluator
        )
        assert next_result.baseline is result.proposed
        assert next_result.retained_score == result.proposed_score
        assert next_result.baseline.examples[0].normalized_cost == 0.8
        assert len(evaluator.calls) == 2 and len(proposer.calls) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("returns", [(0.2, 0.4), (-1.0, 0.0)])
def test_prompt_ties_and_worse_proposals_preserve_current_collection(returns) -> None:
    async def exercise():
        collection = seed_prompt_collection()
        evaluator = RecordedEvaluator(collection, returns=returns)
        optimizer = PromptOptimizer(FakeProposer())
        await optimizer.begin_phase(collection, prompt_plan(), "condition", evaluator)
        result = await optimizer.step(
            collection, "tie", (occurrence("tie"),), condition="condition", iteration_id="one", evaluate=evaluator
        )
        assert not result.accepted and result.collection is collection
        assert result.reason == "not_strictly_better"
        assert result.retained_score == result.current_score
        assert result.proposed is not None

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "case,reason",
    [
        ("no_failure", "no_failure_evidence"),
        ("unchanged", "unchanged"),
        ("invalid", "invalid_structure"),
        ("provider_error", "proposal_failed"),
        ("missing_template", "proposal_failed"),
        ("validation_error", "validation_failed"),
    ],
)
def test_prompt_failures_are_rejected_without_mutating_accepted_templates(case, reason) -> None:
    async def exercise():
        collection = seed_prompt_collection()
        proposals = {
            "unchanged": PromptProposal(collection.components["tie"].template),
            "invalid": PromptProposal("An invalid replacement"),
            "provider_error": RuntimeError("backend down"),
            "missing_template": PromptProposal(None),
        }
        proposer = FakeProposer(proposals.get(case))
        evaluator = RecordedEvaluator(
            collection, fault=RuntimeError("incomplete batch") if case == "validation_error" else None
        )
        optimizer = PromptOptimizer(proposer)
        await optimizer.begin_phase(collection, prompt_plan(), "condition", evaluator)
        evidence = (occurrence("tie", outcome="A"),) if case == "no_failure" else (occurrence("tie"),)
        result = await optimizer.step(
            collection, "tie", evidence, condition="condition", iteration_id="one", evaluate=evaluator
        )
        assert result.collection is collection and not result.accepted and result.reason == reason
        assert len(proposer.calls) == int(case != "no_failure")
        assert len(evaluator.calls) == (2 if case == "validation_error" else 1)
        assert result.proposed is None

    asyncio.run(exercise())


def test_prompt_cache_rejects_changed_conditions_and_wrong_or_incomplete_plans() -> None:
    async def exercise():
        collection = seed_prompt_collection()
        plan = prompt_plan()
        evaluator = RecordedEvaluator(collection)
        proposer = FakeProposer()
        optimizer = PromptOptimizer(proposer)
        await optimizer.begin_phase(collection, plan, "condition", evaluator)
        with pytest.raises(ValueError, match="condition changed"):
            await optimizer.step(
                collection, "tie", (occurrence("tie"),), condition="changed", iteration_id="one", evaluate=evaluator
            )
        with pytest.raises(ValueError, match="training partition"):
            await optimizer.step(
                collection,
                "tie",
                (dataclasses.replace(occurrence("tie"), split="comp-val"),),
                condition="condition",
                iteration_id="one",
                evaluate=evaluator,
            )
        assert proposer.calls == []
        for fault in ("wrong_plan", "incomplete", "wrong_objective"):

            async def invalid(collection, selected):
                if fault == "wrong_plan":
                    return evaluation_record(prompt_plan(("different-a", "different-b")))
                if fault == "incomplete":
                    return EvaluationRecord(selected, (ExampleEvaluation("validation-a", "A", 1, 0.9, True, 1, 1),))
                return evaluation_record(selected, objective="negative_weighting_nll")

            with pytest.raises(ValueError):
                await optimizer.begin_phase(collection, plan, "new", invalid)

    asyncio.run(exercise())


def test_prompt_cache_round_trip_reuses_original_cost_and_resets_at_new_phase() -> None:
    async def exercise():
        collection = seed_prompt_collection()
        evaluator = RecordedEvaluator(collection)
        optimizer = PromptOptimizer(FakeProposer())
        await optimizer.begin_phase(collection, prompt_plan(), "first", evaluator)
        state = optimizer.state_dict()
        restored = PromptOptimizer(FakeProposer())
        restored.load_state_dict(state)
        result = await restored.step(collection, "tie", (), condition="first", iteration_id="one", evaluate=evaluator)
        assert result.baseline.examples[0].normalized_cost == 0.8
        assert len(evaluator.calls) == 1
        await restored.begin_phase(collection, prompt_plan(), "second", evaluator)
        assert len(evaluator.calls) == 2
        corrupt = restored.state_dict()
        next(iter(corrupt["cache"].values()))["plan"]["ordered_example_ids"] = ("wrong-a", "wrong-b")
        with pytest.raises(ValueError):
            PromptOptimizer.validate_state(corrupt)

    asyncio.run(exercise())


def test_prompt_optimization_usage_stays_separate_from_workflow_evaluation_cost() -> None:
    async def exercise():
        collection = seed_prompt_collection()
        attempt = OptimizationAttempt(
            "backward", 1, "offline", "prompt", "feedback", UsageRecord(model_calls=7, output_tokens=90)
        )
        proposal = PromptProposal(collection.components["tie"].template + "\nRevision.", attempts=(attempt,))
        optimizer = PromptOptimizer(FakeProposer(proposal))
        evaluator = RecordedEvaluator(collection)
        await optimizer.begin_phase(collection, prompt_plan(), "fixed", evaluator)
        result = await optimizer.step(
            collection, "tie", (occurrence("tie"),), condition="fixed", iteration_id="one", evaluate=evaluator
        )
        assert result.proposal.usage.model_calls == 7
        assert sum(item.model_calls for item in result.proposed.examples) == 6
        assert tuple(item.normalized_cost for item in result.proposed.examples) == pytest.approx((0.6, 0.4))

    asyncio.run(exercise())


def test_weighting_unavailable_validation_cannot_trigger_plateau():
    assert not validation_plateaued((None, None, None), patience=2, epsilon=1e-4)
    assert not validation_plateaued((-0.2, None, -0.2), patience=2, epsilon=1e-4)
    assert not validation_plateaued((float("-inf"),) * 3, patience=2, epsilon=1e-4)


def test_nonfinite_loss_or_gradient_preserves_adam_parameters_and_moments():
    from examples.adaptive_multi_agent_collab.learning.training import NumericalFailure, _numerical_step

    class InfiniteGradient(torch.autograd.Function):
        @staticmethod
        def forward(ctx, value):
            return value.clone()

        @staticmethod
        def backward(ctx, gradient):
            return torch.full_like(gradient, float("inf"))

    parameter = torch.nn.Parameter(torch.tensor(0.2))
    optimizer = torch.optim.Adam([parameter], lr=0.01)
    _numerical_step(optimizer, parameter.square(), 1.0)
    before_parameter = parameter.detach().clone()
    before_state = {key: value.clone() for key, value in optimizer.state[parameter].items()}
    for loss in (parameter * float("nan"), InfiniteGradient.apply(parameter)):
        with pytest.raises(NumericalFailure):
            _numerical_step(optimizer, loss, 1.0)
        assert torch.equal(parameter, before_parameter)
        assert all(torch.equal(value, optimizer.state[parameter][key]) for key, value in before_state.items())


def test_routing_without_free_decisions_skips_adam_even_with_existing_momentum():
    from examples.adaptive_multi_agent_collab.learning.training import _numerical_step, update_routing

    parameter = torch.nn.Parameter(torch.tensor(0.2))
    optimizer = torch.optim.Adam([parameter], lr=0.01)
    _numerical_step(optimizer, parameter.square(), 1.0)
    before_parameter = parameter.detach().clone()
    before_state = {key: value.clone() for key, value in optimizer.state[parameter].items()}
    assert update_routing(optimizer, (), (), (), local_labels=(), zero_labels=(), conversation_costs=(), p_mix=.75, lambda_conversation=.002) is None
    assert torch.equal(parameter, before_parameter)
    assert all(torch.equal(value, optimizer.state[parameter][key]) for key, value in before_state.items())
