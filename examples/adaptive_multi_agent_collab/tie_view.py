"""Step 9's explicit last-state whitelist; full execution traces stay separate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .schemas import ConversationTrace, MessageFormat, Scheme, StructuredMessage, TerminalRecord, JudgeOutput


TIE_VIEW_ID = "last_state_tie_evidence_v1"


def event_reference(message: StructuredMessage) -> str:
    return f"{message.tag}:r{message.round_index}:{message.format.value}"


def _reason(trace: ConversationTrace) -> str | None:
    if trace.safeguard_trigger is not None:
        return trace.safeguard_trigger.value
    if trace.error:
        return "adjudication-failure" if trace.tag.scheme is Scheme.JUDGE else "generation-failure"
    return None


def tie_view(
    modal_answers: Sequence[str],
    records: Sequence[TerminalRecord],
    traces: Mapping[Any, ConversationTrace],
) -> dict[str, Any]:
    """Preserve every terminal contribution, deduplicating only referenced evidence.

    Event references identify recorded tagged message events; shared adjudications
    identify the one receiver/session judge event. Neither is a generated summary.
    """

    pairs: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}

    def pair(message: StructuredMessage) -> str:
        reference = event_reference(message)
        if reference not in pairs:
            source = message.explanation_source
            pairs[reference] = {
                "answer": message.answer,
                "explanation": message.explanation,
                "source_event": reference,
                "explanation_source": (
                    None
                    if source is None
                    else {
                        "kind": source.kind,
                        "agent": source.agent_id,
                        "session": source.session,
                        "tag": source.tag,
                        "round": source.round_index,
                    }
                ),
            }
        return reference

    def valid_pairs(trace: ConversationTrace) -> list[StructuredMessage]:
        return [
            message
            for message in trace.messages
            if message.format in {MessageFormat.INITIATION, MessageFormat.REVISION_RESPONSE}
            and message.sender_id == trace.tag.initiator_id
            and message.answer is not None
        ]

    def project(trace: ConversationTrace, answer: str) -> dict[str, Any]:
        view: dict[str, Any] = {
            "tag": str(trace.tag),
            "terminal_answer": answer,
            "final_round": max((message.round_index for message in trace.messages), default=0),
            "completion_status": trace.completion_status.value if trace.completion_status else None,
            "reason": _reason(trace),
        }
        if trace.tag.scheme is Scheme.JUDGE:
            output = next((item for item in reversed(trace.outputs) if isinstance(item, JudgeOutput)), None)
            event = trace.judge_event_ref or f"s{trace.tag.session}:judge:{trace.tag.receiver_id}"
            if output is not None and not output.error and not output.validation_exhausted:
                if output.answer != answer:
                    raise ValueError("successful shared adjudication differs from its tagged terminal answer")
                decisions.setdefault(
                    event,
                    {"answer": output.answer, "explanation": output.explanation, "source_event": event},
                )
                view["judge_decision_ref"] = event
            else:
                source = next(
                    (
                        message
                        for candidate in sorted(traces.values(), key=lambda item: str(item.tag))
                        if candidate.tag.scheme is Scheme.JUDGE
                        and candidate.tag.session == trace.tag.session
                        and candidate.tag.receiver_id == trace.tag.receiver_id
                        for message in valid_pairs(candidate)
                        if message.answer == answer
                    ),
                    None,
                )
                view["fallback"] = {"judge_event_ref": event}
                if source is not None:
                    view["fallback"]["source_pair_ref"] = pair(source)
                else:
                    view["fallback"]["source_pair_ref"] = None
                    view["fallback"]["explanation"] = ""
            return view

        matching = [message for message in valid_pairs(trace) if message.answer == answer]
        view["terminal_pair_ref"] = pair(matching[-1]) if matching else None
        if not matching:
            view["explanation"] = ""
        for index in range(len(trace.messages) - 1, -1, -1):
            feedback = trace.messages[index]
            if feedback.format is not MessageFormat.ROLE_RESPONSE:
                continue
            evaluated = next(
                (
                    message
                    for message in reversed(trace.messages[:index])
                    if message.format in {MessageFormat.INITIATION, MessageFormat.REVISION_RESPONSE}
                    and message.answer == feedback.answer
                    and message.explanation == feedback.explanation
                ),
                None,
            )
            if evaluated is None:
                raise ValueError("role feedback lacks its recorded evaluated answer/explanation event")
            view["role_feedback"] = {
                "feedback": feedback.feedback,
                "status": feedback.status.value if feedback.status else None,
                "source_event": event_reference(feedback),
                "evaluated_pair_ref": pair(evaluated),
            }
            break
        return view

    groups = []
    for answer in modal_answers:
        contributions = []
        for record in sorted((item for item in records if item.answer == answer), key=lambda item: str(item.tag)):
            if record.tag is None or record.tag not in traces:
                raise ValueError("tie evidence requires every tagged terminal contribution")
            trace = traces[record.tag]
            if not trace.completed or trace.terminal is None or trace.terminal.answer != answer:
                raise ValueError("tie evidence requires a completed matching terminal trace")
            contributions.append(project(trace, answer))
        groups.append({"answer": answer, "contributions": contributions})
    return {"protocol": TIE_VIEW_ID, "answers": groups, "pairs": pairs, "judge_decisions": decisions}
