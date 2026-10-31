"""Bounded initialization, receiver-visible gamma and shared-prefix stopping."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from examples.adaptive_multi_agent_collab.config import CostConfig, SafetyConfig, TerminationConfig
from examples.adaptive_multi_agent_collab.workflows.prompts import (
    compose_backend_input,
    protocol_instructions,
    seed_prompt_collection,
)
from examples.adaptive_multi_agent_collab.infrastructure.randomness import GenerationContext
from examples.adaptive_multi_agent_collab.providers.resources import BudgetExhausted
from examples.adaptive_multi_agent_collab.schemas import (
    AgentCallKind,
    ConversationTag,
    ConversationTrace,
    ExplanationReference,
    MessageFormat,
    QueryExample,
    Scheme,
    SessionStartState,
    StrategyQAAnswerCanonicalizer,
    StructuredMessage,
    TerminalRecord,
    TieOutput,
)
from examples.adaptive_multi_agent_collab.tests.test_runtime import _runtime, _matrix, _example


@pytest.mark.parametrize("answer,expected", [(" YES . ", "yes"), ("No.", "no"), (" yes ", "yes")])
def test_strategyqa_explicit_string_canonicalization(answer: str, expected: str) -> None:
    example = QueryExample("qa", "Synthetic question?", ("yes", "no"), "yes")
    assert StrategyQAAnswerCanonicalizer().canonicalize(answer, example) == expected


@pytest.mark.parametrize("answer", [True, 1, "yes..", "Answer: yes", "yes, because", "no!", ""])
def test_strategyqa_rejects_noncanonical_prose_and_nonstring(answer: Any) -> None:
    example = QueryExample("qa", "Synthetic question?", ("yes", "no"), "yes")
    with pytest.raises(ValueError):
        StrategyQAAnswerCanonicalizer().canonicalize(answer, example)


def test_initialization_repairs_only_failed_agent_and_accepts_empty_explanation() -> None:
    runtime, agents = _runtime(
        2,
        {
            0: {"initial": [{"answer": "perhaps", "explanation": "x"}, {"answer": " YES. ", "explanation": ""}]},
            1: {"initial": {"answer": "no", "explanation": ""}},
        },
        {1: _matrix(2)},
    )
    example = QueryExample("qa", "Unique query text", ("yes", "no"), "yes", {"secret": "HIDDEN"})
    trajectory = asyncio.run(runtime.run(example))
    assert trajectory.completed and trajectory.initial_answers == ("yes", "no")
    assert [len(agents[index].calls) for index in range(2)] == [2, 1]
    assert len(trajectory.initial_records[0].attempts) == 2
    assert trajectory.initial_usage.model_calls == 3
    repair = agents[0].calls[1].prompt
    assert "perhaps" not in repair and "Unique query text" in repair and "HIDDEN" not in repair
    assert "not an admissible selection" in repair
    assert trajectory.sessions[0].start_state.history == ()


def test_failed_initialization_does_not_invent_answers_or_dispatch_later_agents() -> None:
    runtime, agents = _runtime(3, {0: {"initial": [{"answer": "bad", "explanation": ""}] * 2}}, {1: _matrix(3)})
    trajectory = asyncio.run(runtime.run(_example()))
    assert not trajectory.completed and trajectory.status == "initialization-failed"
    assert trajectory.initial_answers == () and trajectory.final_answer is None
    assert not trajectory.sessions and len(trajectory.call_records) == 2
    assert len(trajectory.initial_records[0].attempts) == 2
    assert trajectory.usage.model_calls == 2
    assert not agents[1].calls and not agents[2].calls


def test_init_guidance_reaches_receiver_and_components_share_generation_evidence() -> None:
    runtime, agents = _runtime(2, {}, {1: _matrix(2, {(0, 1): Scheme.REVIEWER})})
    key = "init:S_V"
    runtime.prompts = runtime.prompts.replace_template(
        key, runtime.prompts.components[key].template + "\nMARKER guidance"
    )
    trajectory = asyncio.run(runtime.run(_example(), partition="train"))
    role = next(call for call in agents[1].calls if call.kind == "role")
    assert "MARKER guidance" in role.prompt
    init_occurrence = next(item for item in trajectory.prompt_occurrences if item.component == key)
    role_occurrence = next(item for item in trajectory.prompt_occurrences if item.component == "role:S_V")
    assert init_occurrence.generation_id == role_occurrence.generation_id
    assert init_occurrence.response == role_occurrence.response
    assert "MARKER guidance" in init_occurrence.effective_input[1]["content"]
    assert trajectory.usage.model_calls == 3
    assert "MARKER guidance" in trajectory.sessions[0].history[0].content
    assert trajectory.sessions[0].traces[0].messages[1].payload == {}
    assert "MARKER guidance" not in trajectory.sessions[0].traces[0].messages[1].text


def test_shared_judge_sees_all_init_guidance_without_duplicate_physical_usage() -> None:
    runtime, agents = _runtime(
        3,
        {2: {"judge": {"answer": "A", "explanation": "selected"}}},
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
    )
    key = "init:S_J"
    runtime.prompts = runtime.prompts.replace_template(key, runtime.prompts.components[key].template + "\nJUDGE MARKER")
    trajectory = asyncio.run(runtime.run(_example(), partition="train"))
    judge = next(call for call in agents[2].calls if call.kind == "judge")
    assert judge.prompt.count("JUDGE MARKER") == 2
    occurrences = [item for item in trajectory.prompt_occurrences if item.component in {key, "judge:S_J"}]
    assert len(occurrences) == 3 and len({item.generation_id for item in occurrences}) == 1
    assert trajectory.usage.model_calls == 4


def test_fixed_protocol_survives_guidance_edit_and_scaffold_cannot_be_changed() -> None:
    prompts = seed_prompt_collection()
    key = "role:S_V"
    spec = prompts.components[key]
    changed = prompts.replace_template(key, spec.protected_scaffold + "Give concise feedback.")
    fields = {name: "data" for name in spec.required_placeholders}
    body = changed.render(key, **fields)
    effective = compose_backend_input("Agent identity", protocol_instructions(key, {}), body)
    assert '"continue" or "complete"' in effective[0]["content"]
    assert effective[1]["content"] == body
    with pytest.raises(ValueError, match="scaffold"):
        prompts.replace_template(key, spec.template.replace("message_text:", "received_message:"))
    with pytest.raises(ValueError, match="repeat field"):
        prompts.replace_template(key, spec.template + " {message_text}")


def test_explanation_selection_uses_tag_order_and_only_valid_answer_sources() -> None:
    runtime, _ = _runtime(3, {}, {1: _matrix(3)})
    state = SessionStartState(1, _example(), (), ("A", "B", "C"))
    runtime._explanation_sources = {0: ExplanationReference("initial", 0)}
    traces = []
    for receiver, explanation in ((2, "later tag"), (1, "first tag")):
        tag = ConversationTag(1, 0, receiver, Scheme.REVIEWER)
        initiation = StructuredMessage(MessageFormat.INITIATION, 0, receiver, 1, 0, tag, answer="A", explanation="old")
        revision = StructuredMessage(
            MessageFormat.REVISION_RESPONSE, 0, receiver, 1, 2, tag, answer="B", explanation=explanation
        )
        trace = ConversationTrace(tag, messages=[initiation, revision])
        trace.finish(TerminalRecord(1, 0, "B", tag))
        traces.append(trace)
    selected = runtime._resolved_explanations(
        state, ("B", "B", "C"), tuple(traces), {0: TieOutput("B", "", ("A", "B"))}, {0: "old", 1: "b", 2: "c"}
    )
    assert selected[0] == "first tag"
    assert runtime._explanation_sources[0].tag == str(traces[1].tag)
    selected = runtime._resolved_explanations(
        state, ("B", "B", "C"), tuple(traces), {0: TieOutput("B", "tie selected", ("A", "B"))}, {}
    )
    assert selected[0] == "tie selected" and runtime._explanation_sources[0].kind == "tie"


def test_shared_prefix_stops_generation_after_last_candidate_and_preserves_own_boundary() -> None:
    candidates = (TerminationConfig(min_sessions=1, max_sessions=1), TerminationConfig(min_sessions=3, max_sessions=3))
    matrices = {index: _matrix(2, {(0, 1): Scheme.REVIEWER}) for index in range(1, 4)}
    runtime, agents = _runtime(2, {}, matrices)
    example = _example()
    result = asyncio.run(
        runtime.run_shared_prefix(example, candidates, generation_context=GenerationContext(12, "sys-val", example.id))
    )
    early, late = result.prefixes
    assert result.configurations == candidates
    assert early.completed and late.completed and early.realized_sessions == 1 and late.realized_sessions == 3
    assert result.shared.usage.model_calls == late.usage.model_calls == 5
    assert early.usage.model_calls == 3
    assert len(agents[0].calls) == 1 and len(agents[1].calls) == 4
    assert early.complete_returns == (early.sessions[0].immediate_reward,)
    assert all(item["logical_call"]["session_id"] <= 1 for item in early.call_records)
    assert all(item.trace["session"] <= 1 for item in early.prompt_occurrences)
    assert all(record.session <= 1 for record in early.sessions[-1].history)
    assert all(item.complete_return == early.complete_returns[0] for item in early.prompt_occurrences)


def test_fixed_pilot_horizon_disables_scientific_stopping() -> None:
    runtime, _ = _runtime(2, {}, {index: _matrix(2) for index in range(1, 4)})
    trajectory = asyncio.run(runtime.run(_example(), fixed_horizon=3))
    assert trajectory.completed and trajectory.realized_sessions == 3
    assert [item.termination.stop for item in trajectory.sessions] == [False, False, True]
    assert trajectory.sessions[-1].termination.reason == "pilot-horizon"


def test_unknown_usage_failure_is_incomplete_and_retains_attempt_evidence() -> None:
    runtime, agents = _runtime(2, {}, {1: _matrix(2, {(0, 1): Scheme.REVIEWER})})
    original = agents[1].invoke

    async def fail(request):
        if request.kind is AgentCallKind.ROLE:
            raise RuntimeError("provider connection became ambiguous")
        return await original(request)

    setattr(agents[1], "invoke", fail)
    trajectory = asyncio.run(runtime.run(_example()))
    assert not trajectory.completed and trajectory.status == "accounting-failed"
    assert trajectory.usage.input_tokens is None and trajectory.final_answer is None
    assert trajectory.failed_session_traces
    assert trajectory.call_records[-1]["usage"]["input_tokens"] is None


def test_budget_failure_consumes_no_fabricated_generation_and_is_reported() -> None:
    runtime, agents = _runtime(2, {}, {1: _matrix(2)})

    async def fail(request):
        raise BudgetExhausted("reservation refused")

    setattr(agents[0], "invoke", fail)
    trajectory = asyncio.run(runtime.run(_example()))
    assert not trajectory.completed and trajectory.status == "budget-exhausted"
    assert trajectory.usage.model_calls == 0 and not agents[1].calls


def test_logical_deadline_covers_repair_budget_without_reset() -> None:
    runtime, agents = _runtime(
        2,
        {1: {"role": [{"status": "invalid", "feedback": "bad"}] * 3}},
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
        safety=SafetyConfig(parse_retries=2, call_timeout_seconds=0.04),
        costs=CostConfig(input_token_weight=0, output_token_weight=0, model_call_weight=1),
    )
    original = agents[1].invoke
    deadlines = []

    async def slow(request):
        if request.kind is AgentCallKind.ROLE:
            deadlines.append(request.context["logical_deadline_monotonic"])
            await asyncio.sleep(0.025)
        return await original(request)

    setattr(agents[1], "invoke", slow)
    trajectory = asyncio.run(runtime.run(_example()))
    assert trajectory.completed
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]
    assert trajectory.sessions[0].usage.model_calls == 2
    assert trajectory.sessions[0].traces[0].safeguard_trigger.value == "call-timeout"


def test_expired_session_resolves_pending_tie_without_dispatch() -> None:
    runtime, agents = _runtime(2, {}, {1: _matrix(2)})
    state = SessionStartState(1, _example(), (), ("A", "B"))
    traces = []
    for receiver, scheme, answer in ((0, Scheme.SELF, "A"), (1, Scheme.REVIEWER, "B")):
        tag = ConversationTag(1, 0, receiver, scheme)
        trace = ConversationTrace(tag)
        trace.finish(TerminalRecord(1, 0, answer, tag))
        traces.append(trace)

    async def run():
        runtime._session_deadline = asyncio.get_running_loop().time() - 1
        return await runtime._resolve_answers(
            state, {0: tuple(trace.terminal for trace in traces), 1: ()}, tuple(traces), ()
        )

    _, resolved, _, outputs, history, usage = asyncio.run(run())
    assert resolved[0] == "A" and usage.model_calls == 0
    assert outputs[0].error and history[-1].status == "safeguard-complete"
    assert not agents[0].calls and not agents[1].calls


def test_shared_prefix_early_boundary_does_not_inherit_later_failure_records() -> None:
    candidates = (TerminationConfig(min_sessions=1, max_sessions=1), TerminationConfig(min_sessions=3, max_sessions=3))
    runtime, agents = _runtime(2, {}, {index: _matrix(2, {(0, 1): Scheme.REVIEWER}) for index in range(1, 4)})
    original = agents[1].invoke

    async def limited(request):
        if request.message is not None and request.message.session == 2:
            raise BudgetExhausted("physical budget stops second session")
        return await original(request)

    setattr(agents[1], "invoke", limited)
    example = _example()
    result = asyncio.run(
        runtime.run_shared_prefix(example, candidates, generation_context=GenerationContext(3, "sys-val", example.id))
    )
    early, late = result.prefixes
    assert not result.shared.completed and result.shared.status == "budget-exhausted"
    assert early.completed and early.error is None and early.realized_sessions == 1
    assert early.complete_returns == (early.sessions[0].immediate_reward,)
    assert not early.failed_session_traces and not early.failed_history
    assert all(record["logical_call"]["session_id"] <= 1 for record in early.call_records)
    assert not late.completed and late.final_answer is None
