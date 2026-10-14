"""Read-only historical request replay, using current production projections.

This utility never constructs a model client or a trainer. Legacy prompt parsing
is deliberately narrow: ambiguous or missing source evidence is unavailable,
not guessed. Re-rendering recorded outputs is not a new model trajectory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import ResourceConfig
from .prompts import (
    compose_backend_input,
    initiation_request,
    protocol_instructions,
    seed_prompt_collection,
)
from .repair import repair_variants, render_repair, select_repair_diagnostic
from .output_contract import response_format_for
from .schemas import (
    ConversationStatus,
    ConversationTag,
    ConversationTrace,
    ExplanationReference,
    JudgeOutput,
    MessageFormat,
    QueryExample,
    Scheme,
    StrategyQAAnswerCanonicalizer,
    StructuredMessage,
    TerminalRecord,
)
from .tie_view import tie_view


REPLAY_ID = "recorded_input_reconciliation_v1"


def recorded_repair_diagnostic(prompt: str, *, kind: str | None = None) -> str:
    marker = "\nYour previous response failed validation: "
    ending = ". Return only the requested JSON object and satisfy every schema and allowed-answer constraint."
    if prompt.count(marker) != 1 or not prompt.endswith(ending):
        raise ValueError("unrecognized or ambiguous historical repair diagnostic")
    error = prompt.split(marker, 1)[1][: -len(ending)]
    return select_repair_diagnostic(
        error, context_invalid="context-invalid" in error or "outside the allowed" in error, kind=kind
    )


def parse_tag(value: str | dict[str, Any]) -> ConversationTag:
    if isinstance(value, dict):
        return ConversationTag(**{**value, "scheme": Scheme(value["scheme"])})
    match = re.fullmatch(r"s(\d+):(\d+)->(\d+):(S_\w+)", value)
    if not match:
        raise ValueError("unrecognized recorded conversation tag")
    session, initiator, receiver, scheme = match.groups()
    return ConversationTag(int(session), int(initiator), int(receiver), Scheme(scheme))


def _field(text: str, name: str, next_name: str) -> str:
    marker, end = f"\n{name}: ", f"\n{next_name}: "
    if text.count(marker) != 1 or text.count(end) != 1:
        raise ValueError(f"ambiguous legacy field boundaries: {name}")
    return text.split(marker, 1)[1].split(end, 1)[0]


def _init_text(text: str) -> tuple[str, str]:
    """Render recorded initiation fields with the current seed scaffold/request."""
    names = (
        "tag",
        "scheme",
        "initiator",
        "receiver",
        "query",
        "answer",
        "explanation",
        "request",
    )
    values = {name: _field(text, name, following) for name, following in zip(names, names[1:])}
    if not text.startswith(f"Component: init:{values['scheme']}\n"):
        raise ValueError("unrecognized legacy initiation scaffold")
    values["request"] = initiation_request(values["scheme"])
    return (
        seed_prompt_collection().render(f"init:{values['scheme']}", **values),
        values["answer"],
    )


def incoming_text(prompt: str) -> tuple[str, str]:
    """Read complete legacy Text(m), not its duplicate enclosing fields."""
    if prompt.count("\nmessage_text: ") != 1:
        raise ValueError("missing or ambiguous complete incoming message")
    tail = prompt.split("\nmessage_text: ", 1)[1]
    if tail.startswith("Component: init:"):
        return _init_text(tail.split("\nrole_instruction: ", 1)[0])
    value, _ = json.JSONDecoder().raw_decode(tail)
    if value.get("serialization") != "collaboration_message_2":
        raise ValueError("unsupported historical message serialization")
    message = StructuredMessage(
        format=MessageFormat(value["format"]),
        sender_id=value["sender"],
        receiver_id=value["receiver"],
        session=value["session"],
        round_index=value["round"],
        tag=parse_tag(value["tag"]),
        query=value["query"],
        answer=value["answer"],
        explanation=value["explanation"],
        feedback=value["feedback"],
        status=ConversationStatus(value["status"]) if value["status"] else None,
        scheme_request=value["scheme_request"],
    )
    return message.text, str(message.answer)


def _trace(value: dict[str, Any]) -> ConversationTrace:
    tag = parse_tag(value["tag"])
    trace = ConversationTrace(tag)
    for raw in value["messages"]:
        source = raw.get("explanation_source")
        trace.messages.append(
            StructuredMessage(
                format=MessageFormat(raw["format"]),
                sender_id=raw["sender_id"],
                receiver_id=raw["receiver_id"],
                session=raw["session"],
                round_index=raw["round_index"],
                tag=tag,
                query=raw["query"],
                answer=raw["answer"],
                explanation=raw["explanation"],
                feedback=raw["feedback"],
                status=ConversationStatus(raw["status"]) if raw["status"] else None,
                explanation_source=ExplanationReference(**source) if source else None,
            )
        )
    if tag.scheme is Scheme.JUDGE:
        for output in value["outputs"]:
            if "answer" in output:
                trace.outputs.append(
                    JudgeOutput(
                        output["answer"],
                        output["explanation"],
                        error=output.get("error"),
                        validation_exhausted=output.get("validation_exhausted", False),
                    )
                )
        trace.judge_event_ref = f"s{tag.session}:judge:{tag.receiver_id}"
    trace.terminal = TerminalRecord(tag.session, tag.initiator_id, value["terminal_answer"], tag)
    trace.completed = True
    trace.completion_status = ConversationStatus(value["status"])
    trace.error = value.get("error")
    return trace


def render_recorded_request(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Reconstruct only recorded role/revision/judge/tie base inputs."""
    component, old = record["component"], record["prompt"]
    kind = record["logical_call"]["call_type"]
    prompts = seed_prompt_collection()
    fields: dict[str, Any] = {"admissible_answers": ("yes", "no")}
    if kind in {"role", "revision"}:
        text, answer = incoming_text(old)
        fields["incoming_answer"] = answer
        return prompts.render(component, message_text=text), fields
    if kind == "judge":
        submissions, _ = json.JSONDecoder().raw_decode(old.split("\nsubmissions: ", 1)[1])
        texts = [_init_text(item["text"])[0] for item in submissions]
        fields["allowed_submitted_answers"] = tuple(dict.fromkeys(item["answer"] for item in submissions))
        return (
            prompts.render(component, submissions=json.dumps(texts, ensure_ascii=False)),
            fields,
        )
    if kind == "tie":
        modal, _ = json.JSONDecoder().raw_decode(old.split("\nmodal_answers: ", 1)[1])
        evidence, _ = json.JSONDecoder().raw_decode(old.split("\nevidence: ", 1)[1])
        traces = [_trace(value) for answer in modal for value in evidence[answer]]
        projected = tie_view(
            modal,
            [trace.terminal for trace in traces],
            {trace.tag: trace for trace in traces},
        )
        fields["tied_answers"] = tuple(modal)
        return (
            prompts.render(
                component,
                agent=record["logical_call"]["agent_id"],
                query=_field(old, "query", "modal_answers"),
                modal_answers=json.dumps(modal, ensure_ascii=False),
                evidence=json.dumps(projected, ensure_ascii=False, sort_keys=True),
            ),
            fields,
        )
    raise ValueError("call type is outside the historical collaboration replay")


def repetition_replay(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Independent Step 33 calculation from unique accepted role replies only."""
    accepted: dict[tuple[Any, ...], tuple[dict[str, Any], str, dict[str, Any]]] = {}
    ambiguous: set[tuple[Any, ...]] = set()
    unavailable = []
    canonicalizer = StrategyQAAnswerCanonicalizer()
    example = QueryExample("recorded", "Recorded StrategyQA input", ("yes", "no"))
    for record in records:
        identity = record["logical_call"]
        output = record.get("structured_output")
        if identity["call_type"] != "role" or not isinstance(output, dict) or record.get("error"):
            continue
        if set(output) != {"status", "feedback"} or output["status"] not in {
            "continue",
            "complete",
        }:
            continue
        if not isinstance(output["feedback"], str) or not output["feedback"].strip():
            continue
        try:
            _, answer = incoming_text(record["prompt"])
            answer = canonicalizer.canonicalize(answer, example)
            key = (
                record["example_id"],
                identity["session_id"],
                identity["conversation_id"],
                identity["round_index"],
            )
            # Audit copies and repairs cannot advance the same logical role turn twice.
            if key in ambiguous:
                continue
            if key in accepted and accepted[key][1:] != (answer, output):
                del accepted[key]
                ambiguous.add(key)
                raise ValueError("conflicting accepted copies for one role turn")
            accepted[key] = (record, answer, output)
        except (ValueError, KeyError, TypeError) as error:
            unavailable.append({"logical_call": identity, "reason": str(error)})
    grouped: dict[tuple[Any, ...], list[tuple[Any, ...]]] = defaultdict(list)
    for key, value in accepted.items():
        grouped[key[:-1]].append((key[-1], *value))
    triggers = []
    for conversation, turns in grouped.items():
        seen: set[tuple[str, str]] = set()
        count = 0
        for round_index, record, answer, output in sorted(turns, key=lambda item: item[0]):
            feedback_key = answer, " ".join(output["feedback"].split())
            count = count + 1 if feedback_key in seen else 0
            seen.add(feedback_key)
            if output["status"] == "complete":
                break
            if count >= 2:
                triggers.append(
                    {
                        "example_id": conversation[0],
                        "session": conversation[1],
                        "tag": conversation[2],
                        "round": round_index,
                        "answer": answer,
                        "counter": count,
                        "feedback": output["feedback"],
                        "later_recorded_role_turns": sum(turn[0] > round_index for turn in turns),
                    }
                )
                break
    return {
        "unique_accepted_role_turns": len(accepted),
        "conversations": len(grouped),
        "triggers": triggers,
        "unavailable": unavailable,
    }


def replay(manifest_path: Path, pilot_path: Path) -> dict[str, Any]:
    from .cli import configure_logging

    configure_logging()
    from .resources import (
        BackendResourceProfile,
        ExpenditureLedger,
        LocalTokenCounter,
        ResourceController,
        ResourceFailure,
    )

    manifest = json.loads(manifest_path.read_text())
    pilot = json.loads(pilot_path.read_text())
    training_ids = set(manifest["active_data"]["plan"]["train_ids"])
    if any(observation["example_id"] not in training_ids for observation in pilot["observations"]):
        raise ValueError("historical pilot replay requires only registered active-training queries")
    records = [
        {**record, "example_id": observation["example_id"]}
        for observation in pilot["observations"]
        for record in observation["call_records"]
    ]
    limits = ResourceConfig(**manifest["config"]["resources"])
    controllers = {}
    for index, agent in enumerate(manifest["config"]["agent_pool"]):
        profile = dict(manifest["backends"][agent["backend_key"]]["resources"])
        cache = profile.get("tokenizer_cache_directory")
        if cache:
            profile["tokenizer_cache_directory"] = str((manifest_path.parent / cache).resolve())
        parsed = BackendResourceProfile.from_dict(profile)
        controllers[index] = ResourceController(
            parsed, LocalTokenCounter(parsed), ExpenditureLedger(limits), limits=limits
        )
    rows = []
    bases: dict[tuple[Any, ...], tuple[str, dict[str, Any]]] = {}
    for record in records:
        identity = record["logical_call"]
        kind, agent = identity["call_type"], identity["agent_id"]
        if kind == "initial":
            continue
        key = (
            record["example_id"],
            agent,
            identity["session_id"],
            identity["conversation_id"],
            kind,
            identity["round_index"],
        )
        controller = controllers[agent]
        row: dict[str, Any] = {
            "example_id": record["example_id"],
            "logical_call": identity,
            "kind": kind,
            "repair": bool(identity["repair_attempt"]),
        }
        try:
            effective = record["effective_input"]
            row["before_input"] = controller.counter.count_input(effective)
            if identity["repair_attempt"]:
                base, fields = bases[key]
                diagnostic = recorded_repair_diagnostic(record["prompt"], kind=kind)
                body = render_repair(base, diagnostic)
            else:
                base, fields = render_recorded_request(record)
                bases[key] = base, fields
                body = base
            agent_config = manifest["config"]["agent_pool"][agent]
            instructions = "\n".join(
                value
                for value in (
                    agent_config["system_configuration"],
                    agent_config.get("role_configuration", ""),
                )
                if value
            )
            protocol = protocol_instructions(record["component"], fields)
            compose = lambda text: compose_backend_input(instructions, protocol, text)
            backend = manifest["backends"][agent_config["backend_key"]]
            schema = response_format_for(kind, backend.get("role_output"))
            row["response_format"] = schema
            row["after_input"] = controller.counter.count_input(compose(body), schema=schema)
            variants = [compose(text) for text in repair_variants(base, kind=kind)]
            row["after_repair_envelope"] = max(
                controller.counter.count_input(value, schema=schema) for value in variants
            )
            row["input_cap"] = limits.caps(kind)[0]
            try:
                controller.preflight_envelope(variants, kind, schema=schema)
                row["after_admitted"] = True
            except ResourceFailure as error:
                row["after_admitted"] = False
                row["admission_reason"] = str(error)
            row["before_admitted_by_current_base_check"] = row["before_input"] <= row["input_cap"]
        except (ValueError, KeyError, TypeError, IndexError) as error:
            row["unavailable"] = str(error)
        rows.append(row)
    return {
        "protocol": REPLAY_ID,
        "source": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (manifest_path, pilot_path)},
        "method": "Recorded pilot outputs re-rendered through current production prompts, TieView and unchanged local counters; independent exact repetition replay. No model generation or expenditure.",
        "limitations": [
            "Counterfactual sizes retain historical model answers/feedback; new responses, behavior, accuracy and costs are unobserved.",
            "Legacy explicit field boundaries and stored trace messages are required; ambiguity is reported unavailable.",
            "Historical passive-fallback totals and incomplete-session terminals were not fully persisted and remain unavailable.",
            "Old continuation requests after a replayed trigger are measured for audit, not asserted reachable under the revised workflow.",
        ],
        "repetition": repetition_replay(records),
        "requests": rows,
        "summary": {
            kind: {
                "records": len(items),
                "reconstructed": sum("after_input" in item for item in items),
                "admitted": sum(item.get("after_admitted", False) for item in items),
                "before_range": [
                    min((item["before_input"] for item in items if "before_input" in item), default=None),
                    max((item["before_input"] for item in items if "before_input" in item), default=None),
                ],
                "after_range": [
                    min(
                        (item["after_input"] for item in items if "after_input" in item),
                        default=None,
                    ),
                    max(
                        (item["after_input"] for item in items if "after_input" in item),
                        default=None,
                    ),
                ],
                "envelope_range": [
                    min(
                        (item["after_repair_envelope"] for item in items if "after_repair_envelope" in item),
                        default=None,
                    ),
                    max(
                        (item["after_repair_envelope"] for item in items if "after_repair_envelope" in item),
                        default=None,
                    ),
                ],
            }
            for kind in sorted({row["kind"] for row in rows})
            for items in [[row for row in rows if row["kind"] == kind]]
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("pilot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == args.manifest.resolve() or output.is_relative_to(args.pilot.resolve().parent):
        raise ValueError("replay output must be separate from the historical campaign and manifest")
    result = replay(args.manifest.resolve(), args.pilot.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
