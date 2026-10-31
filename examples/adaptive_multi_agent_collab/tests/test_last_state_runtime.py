"""Latest protocol: exact repetition, compact provenance, and fatal partial records."""

from __future__ import annotations

import asyncio
import json

from examples.adaptive_multi_agent_collab.config import SafetyConfig
from examples.adaptive_multi_agent_collab.providers.resources import AccountingFailure
from examples.adaptive_multi_agent_collab.schemas import (
    AgentCallKind,
    ConversationStatus,
    ConversationTag,
    ConversationTrace,
    FeedbackState,
    MessageFormat,
    RoleOutput,
    SafeguardTrigger,
    Scheme,
    StructuredMessage,
    TerminalRecord,
    UsageRecord,
)
from examples.adaptive_multi_agent_collab.tests.test_runtime import _example, _matrix, _runtime
from examples.adaptive_multi_agent_collab.workflows.tie_view import event_reference, tie_view


def _message(tag, kind, round_index, answer, explanation="", **kwargs):
    sender, receiver = tag.initiator_id, tag.receiver_id
    if kind in {MessageFormat.ROLE_RESPONSE, MessageFormat.JUDGE_RETURN}:
        sender, receiver = receiver, sender
    return StructuredMessage(
        kind, sender, receiver, tag.session, round_index, tag, answer=answer, explanation=explanation, **kwargs
    )


def test_feedback_counter_uses_all_seen_keys_and_only_whitespace_normalization():
    counter = FeedbackState()
    assert [counter.observe("yes", feedback) for feedback in ["  Fix \n this ", "Fix this", "Fix\tthis"]] == [0, 1, 2]
    assert counter.seen == [("yes", "Fix this")]
    assert counter.observe("yes", "fix this") == 0  # case is significant
    assert counter.observe("no", "Fix this") == 0  # answer is part of the key
    assert counter.observe("yes", "Fix this") == 1
    assert counter.observe("no", "Fix this") == 2  # not merely consecutive identical replies
    assert counter.observe("no", "new correction") == 0
    assert FeedbackState().observe("yes", "Fix this") == 0


def test_repeated_feedback_stops_before_third_revision_without_rewriting_model_status():
    runtime, agents = _runtime(
        2,
        {1: {"role": [{"status": "continue", "feedback": "same issue"}] * 3}},
        {1: _matrix(2, {(0, 1): Scheme.DEBATE})},
    )
    trajectory = asyncio.run(runtime.run(_example()))
    trace = trajectory.sessions[0].traces[0]
    assert trace.safeguard_trigger is SafeguardTrigger.REPEATED_FEEDBACK
    assert trace.safeguard_reason == "repeated-feedback"
    assert trace.feedback_state.count == 2 and trace.terminal.answer == "A"
    assert [x.kind for x in agents[0].calls].count("revision") == 2
    assert [x.kind for x in agents[1].calls].count("role") == 3
    assert trace.messages[-1].round_index == 5
    assert trace.messages[-1].status is ConversationStatus.CONTINUE
    final_output = trace.outputs[-1]
    assert isinstance(final_output, RoleOutput)
    assert final_output.status is ConversationStatus.CONTINUE
    assert trajectory.usage.model_calls == 7
    assert trajectory.execution_counters["safeguards"]["repeated-feedback"] == 1
    assert trajectory.execution_counters["call_types"]["role"]["valid"] == 3


def test_unchanged_answer_and_new_feedback_does_not_trigger_repetition():
    runtime, _ = _runtime(
        2,
        {1: {"role": [{"status": "continue", "feedback": f"correction {i}"} for i in range(4)]}},
        {1: _matrix(2, {(0, 1): Scheme.TEACHER})},
    )
    trajectory = asyncio.run(runtime.run(_example()))
    trace = trajectory.sessions[0].traces[0]
    assert trace.completion_status is ConversationStatus.SAFEGUARD_COMPLETE
    assert trace.safeguard_trigger is SafeguardTrigger.ANSWER_STABILITY
    assert trace.feedback_state.count == 0 and trace.terminal.answer == "A"


def test_complete_role_is_ordinary_completion_even_on_repeated_key():
    runtime, _ = _runtime(
        2,
        {1: {"role": [{"status": "continue", "feedback": "same"}] * 2 + [{"status": "complete", "feedback": "same"}]}},
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
    )
    trace = asyncio.run(runtime.run(_example())).sessions[0].traces[0]
    assert trace.feedback_state.count == 2
    assert trace.completion_status is ConversationStatus.COMPLETE and trace.safeguard_reason is None


def test_invalid_role_generations_do_not_enter_repetition_state():
    runtime, _ = _runtime(
        2,
        {1: {"role": [{"status": "invalid", "feedback": "same"}] * 2}},
        {1: _matrix(2, {(0, 1): Scheme.REVIEWER})},
    )
    trace = asyncio.run(runtime.run(_example())).sessions[0].traces[0]
    assert trace.feedback_state.seen == [] and trace.feedback_state.count == 0
    assert trace.safeguard_trigger is SafeguardTrigger.VALIDATION_EXHAUSTED


def test_repetition_state_resets_between_conversations_in_successive_sessions():
    matrix = _matrix(2, {(0, 1): Scheme.TEACHER})
    runtime, agents = _runtime(
        2,
        {1: {"role": [{"status": "continue", "feedback": "same"}] * 6}},
        {1: matrix, 2: matrix},
    )
    trajectory = asyncio.run(runtime.run(_example(), fixed_horizon=2))
    assert [session.traces[0].feedback_state.count for session in trajectory.sessions] == [2, 2]
    assert [len(session.traces[0].outputs) for session in trajectory.sessions] == [5, 5]
    assert sum(call.kind == "role" for call in agents[1].calls) == 6


def test_tie_view_binds_latest_feedback_to_evaluated_pair_not_later_terminal():
    tag = ConversationTag(1, 0, 1, Scheme.REVIEWER)
    initial = _message(tag, MessageFormat.INITIATION, 0, "A", "old evidence", payload={"secret": "BAD"})
    role = _message(
        tag,
        MessageFormat.ROLE_RESPONSE,
        1,
        "A",
        "old evidence",
        feedback="supported correction",
        status=ConversationStatus.CONTINUE,
    )
    revision = _message(tag, MessageFormat.REVISION_RESPONSE, 2, "B", "updated evidence")
    trace = ConversationTrace(
        tag, messages=[initial, role, revision], usage=UsageRecord(raw_metadata={"raw_gateway_frames": "BAD" * 10000})
    )
    trace.finish(
        TerminalRecord(1, 0, "B", tag),
        status=ConversationStatus.SAFEGUARD_COMPLETE,
        safeguard_reason="verbose BAD failure",
        safeguard_trigger=SafeguardTrigger.ROUND_LIMIT,
    )
    view = tie_view(("B",), (trace.terminal,), {tag: trace})
    contribution = view["answers"][0]["contributions"][0]
    assert view["pairs"][contribution["terminal_pair_ref"]]["answer"] == "B"
    feedback_pair = contribution["role_feedback"]["evaluated_pair_ref"]
    assert feedback_pair == event_reference(initial)
    assert view["pairs"][feedback_pair]["answer"] == "A"
    assert contribution["reason"] == "round-limit"
    serialized = json.dumps(view)
    assert "BAD" not in serialized and "raw_gateway" not in serialized and "usage" not in serialized
    assert len(serialized) < 1500


def test_tie_view_reuses_terminal_pair_by_reference_for_its_feedback():
    tag = ConversationTag(1, 0, 1, Scheme.DEBATE)
    first = _message(tag, MessageFormat.INITIATION, 0, "A", "evidence")
    last = _message(
        tag, MessageFormat.ROLE_RESPONSE, 1, "A", "evidence", feedback="accept", status=ConversationStatus.COMPLETE
    )
    trace = ConversationTrace(tag, messages=[first, last])
    trace.finish(TerminalRecord(1, 0, "A", tag))
    view = tie_view(("A",), (trace.terminal,), {tag: trace})
    assert len(view["pairs"]) == 1
    item = view["answers"][0]["contributions"][0]
    assert item["terminal_pair_ref"] == item["role_feedback"]["evaluated_pair_ref"]


def test_shared_judge_tie_decision_is_stored_once_with_every_tagged_return():
    runtime, _ = _runtime(
        3,
        {2: {"judge": {"answer": "B", "explanation": "shared decision"}}},
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
    )
    session = asyncio.run(runtime.run(_example())).sessions[0]
    records = tuple(trace.terminal for trace in session.traces)
    view = tie_view(("B",), records, {trace.tag: trace for trace in session.traces})
    assert len(view["judge_decisions"]) == 1
    contributions = view["answers"][0]["contributions"]
    assert len(contributions) == 2
    assert contributions[0]["judge_decision_ref"] == contributions[1]["judge_decision_ref"]
    assert list(view["judge_decisions"].values())[0]["explanation"] == "shared decision"
    assert all(trace.messages[-1].explanation == "" for trace in session.traces)


def test_judge_fallback_uses_existing_source_pair_not_invalid_adjudication():
    runtime, _ = _runtime(
        3,
        {2: {"judge": {"answer": "C", "explanation": "INVALID ADJUDICATION"}}},
        {1: _matrix(3, {(0, 2): Scheme.JUDGE, (1, 2): Scheme.JUDGE})},
        safety=SafetyConfig(parse_retries=0),
    )
    session = asyncio.run(runtime.run(_example())).sessions[0]
    view = tie_view(
        ("A",), tuple(trace.terminal for trace in session.traces), {trace.tag: trace for trace in session.traces}
    )
    assert view["judge_decisions"] == {}
    assert "INVALID ADJUDICATION" not in json.dumps(view)
    contributions = view["answers"][0]["contributions"]
    assert len(contributions) == 2
    assert all(view["pairs"][x["fallback"]["source_pair_ref"]]["answer"] == "A" for x in contributions)
    assert len(view["pairs"]) == 1


def test_missing_judge_fallback_source_records_empty_explanation_without_adjudication():
    tag = ConversationTag(1, 0, 1, Scheme.JUDGE)
    trace = ConversationTrace(tag, messages=[_message(tag, MessageFormat.INITIATION, 0, "A", "unrelated")])
    trace.error = "interrupted"
    trace.finish(
        TerminalRecord(1, 0, "B", tag),
        status=ConversationStatus.SAFEGUARD_COMPLETE,
        safeguard_reason="interrupted",
        safeguard_trigger=SafeguardTrigger.CALL_TIMEOUT,
    )
    view = tie_view(("B",), (trace.terminal,), {tag: trace})
    assert view["judge_decisions"] == {} and view["pairs"] == {}
    fallback = view["answers"][0]["contributions"][0]["fallback"]
    assert fallback["source_pair_ref"] is None and fallback["explanation"] == ""


def test_tie_projection_preserves_fixed_modal_order_and_tag_multiplicity():
    tags = [ConversationTag(1, 0, i, Scheme.TEACHER) for i in (2, 1)]
    traces = {}
    records = []
    for tag in tags:
        trace = ConversationTrace(tag, messages=[_message(tag, MessageFormat.INITIATION, 0, "A", "pair")])
        trace.finish(TerminalRecord(1, 0, "A", tag))
        traces[tag] = trace
        records.append(trace.terminal)
    records.append(records[0])
    view = tie_view(("B", "A"), records, traces)
    assert [group["answer"] for group in view["answers"]] == ["B", "A"]
    assert view["answers"][0]["contributions"] == []
    assert [item["tag"] for item in view["answers"][1]["contributions"]] == [str(tags[1]), str(tags[0]), str(tags[0])]


def test_single_presentation_keeps_whole_initiation_once_and_later_feedback_once():
    runtime, agents = _runtime(
        2,
        {1: {"role": {"status": "continue", "feedback": "UNIQUE_FEEDBACK"}}},
        {1: _matrix(2, {(0, 1): Scheme.TEACHER})},
    )
    trajectory = asyncio.run(runtime.run(_example()))
    initiation = trajectory.sessions[0].traces[0].messages[0]
    role = next(call for call in agents[1].calls if call.kind == "role")
    revision = next(call for call in agents[0].calls if call.kind == "revision")
    assert role.prompt.count(initiation.text) == 1
    assert role.prompt.count(_example().query) == 1
    assert revision.prompt.count("UNIQUE_FEEDBACK") == 1
    assert "admissible" in role.context["protocol_instructions"]
    assert role.context["admissible_answers"] == _example().candidates


def test_fatal_accounting_stops_queued_work_and_retains_partial_output_and_events(monkeypatch):
    runtime, agents = _runtime(3, {}, {1: _matrix(3, {(0, 2): Scheme.REVIEWER, (1, 2): Scheme.TEACHER})})
    original = agents[2].invoke
    reached = []

    async def fail(request):
        if request.kind is AgentCallKind.ROLE:
            reached.append(str(request.message.tag))
            raise AccountingFailure(
                "synthetic unresolved charge",
                UsageRecord(
                    input_tokens=None, output_tokens=None, monetary_cost=None, paid_monetary_cost=None, model_calls=1
                ),
            )
        return await original(request)

    monkeypatch.setattr(agents[2], "invoke", fail)
    trajectory = asyncio.run(runtime.run(_example()))
    assert len(reached) == 1 and not trajectory.completed
    assert trajectory.status == "accounting-failed" and trajectory.operational_halt["fatal"]
    assert len(trajectory.failed_session_traces) == 2
    assert trajectory.failed_session_traces[0].outputs
    assert trajectory.failed_history and len(trajectory.failed_history) == 2
    assert all(not trace.completed and trace.terminal is None for trace in trajectory.failed_session_traces)
    assert trajectory.execution_counters["call_types"]["role"]["requested"] == 1
    assert trajectory.execution_counters["call_types"]["role"]["dispatched"] == 1
    assert trajectory.fallback_records == ()
