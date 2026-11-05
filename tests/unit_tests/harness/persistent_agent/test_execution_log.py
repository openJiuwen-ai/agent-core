# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-17 execution log snapshot: per-event checks, the lossless record and the injected preview."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.harness.persistent_agent.execution import execution_log as LOG
from openjiuwen.harness.persistent_agent.ports import PersistentPorts
from openjiuwen.harness.persistent_agent.schema.build_context import PersistentBuildContext
from openjiuwen.harness.persistent_agent.schema.events import AgentEvent, EventType
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext

pytestmark = pytest.mark.level0

WHEN = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
SECRET = "SECRET-CONTENT"
TARGET = "execution-2"


def execution(**changes):
    fields = dict(
        namespace_id="tenant",
        agent_id="agent-a",
        session_id="session-a",
        request_id="request-1",
        execution_id="execution-3",
        config_version="v1",
        config_fingerprint="0" * 64,
        source_ref="source:message-1",
        principal_ref="user",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="p1",
        capability_snapshot=frozenset({"construction"}),
        recovery_ref=TARGET,
    )
    fields.update(changes)
    return ExecutionContext(**fields)


CURRENT = execution()
OLD = replace(CURRENT, execution_id=TARGET, recovery_ref=None)


class Commit:
    def __init__(self, log=(), error=None):
        self.log = log
        self.error = error
        self.calls = []

    async def record(self, *args):
        raise AssertionError("summarizing records nothing")

    async def read_execution_log(self, current, execution_id):
        self.calls.append((current, execution_id))
        if self.error is not None:
            raise self.error
        return self.log


class Budget:
    async def admit(self, *args):
        raise AssertionError("no model call")

    async def report(self, *args):
        raise AssertionError("no model call")


def build_context(commit):
    return PersistentBuildContext(
        execution=CURRENT,
        session=create_agent_session(session_id=CURRENT.session_id),
        ports=PersistentPorts(commit=commit, budget=Budget(), window_backend=InMemoryKVStore()),
    )


async def snapshot(log=(), error=None):
    commit = Commit(log, error)
    taken = await LOG.read_snapshot(build_context(commit), TARGET)
    assert commit.calls == [(CURRENT, TARGET)], "reads the target Execution under the current invocation"
    return taken


def event(seq, kind, payload, source=OLD):
    return AgentEvent.create(source, event_type=kind, local_seq=seq, occurred_at=WHEN, payload=payload)


def intent(seq, op, name="submit_reminder", args="单号=BX-0930", call=None, target=None):
    payload = {
        "operation_id": op,
        "tool_call_id": call or f"call-{op}",
        "tool_name": name,
        "target_ref": target,
        "params_digest": "a" * 64,
        "args_summary": args,
    }
    return event(seq, EventType.TOOL_INTENT, payload)


def completed(seq, name, status="succeeded", op=None, result="完成", reason=None, args="x", call=None, refs=()):
    payload = {
        "tool_call_id": call or (f"call-{op}" if op else f"call-{seq}"),
        "tool_name": name,
        "args_summary": args,
        "status": status,
        "result_summary": result,
        "result_refs": refs,
        "operation_id": op,
    }
    if status != "succeeded":
        payload["reason"] = reason or "timed out"
    return event(seq, EventType.TOOL_COMPLETED, payload)


def progress(seq, text):
    return event(seq, EventType.PROGRESS_UPDATED, {"text": text})


def answer(seq, output_id, text="回答正文"):
    payload = {"output_id": output_id, "target_ref": "chat-1", "share_mode": "message", "text": text}
    return event(seq, EventType.OUTPUT_PROPOSED, payload)


def shared_file(seq, output_id, path="reports/q3.md", mode="upload", caption=None):
    payload = {
        "output_id": output_id,
        "target_ref": "chat-1",
        "share_mode": mode,
        "file_ref": "sha256:" + "b" * 64,
        "file_path": path,
        "file_size": 13,
    }
    if caption:
        payload["text"] = caption
    return event(seq, EventType.OUTPUT_PROPOSED, payload)


def final(seq, refs=(), status="completed"):
    payload = {"status": status, "output_refs": refs}
    if status != "completed":
        payload["reason"] = "cancelled by request"
    return event(seq, EventType.EXECUTION_FINAL, payload)


def paused(seq, reason="error", refs=(), detail="model failed"):
    return event(seq, EventType.EXECUTION_PAUSED, {"reason": reason, "detail": detail, "output_refs": refs})


def capture(monkeypatch):
    """Diagnostics as (level, text); reading a log never logs anything else."""
    logged = []
    for level in ("warning", "error"):
        monkeypatch.setattr(
            LOG.logger, level, lambda message, *args, level=level: logged.append((level, message % args))
        )
    return logged


def noise(seq):
    usage = {"status": "known", "input_tokens": 1, "output_tokens": 2}
    kinds = [
        (EventType.EXECUTION_STARTED, {"goal": "整理报销"}),
        (EventType.REPLY_DELTA, {"text": "正在核对"}),
        (EventType.MODEL_COMPLETED, {"model_call_id": f"mc-{seq}", "config_version": "v1", "usage": usage}),
    ]
    kind, payload = kinds[seq % 3]
    return event(seq, kind, payload)


def rebuilt(head, line):
    """The saved event back from the header and one line: what the header states is put back in."""
    data = json.loads(line)
    shared = {name: head[name] for name in ("namespace_id", "agent_id", "session_id", "request_id", "source_ref")}
    shared.update(execution_id=head["execution_id"], schema_version=head["event_schema_version"])
    return AgentEvent.model_validate({**shared, **data})


def preview_of(taken):
    head, *lines = LOG.preview(taken).split("\n")
    return json.loads(head), [json.loads(line) for line in lines]


def mixed_log():
    decided = event(
        1, EventType.PARTICIPATION_DECIDED, {"decision": "respond", "reason": "被 @", "model_call_id": "mc-0"}
    )
    started = event(2, EventType.EXECUTION_STARTED, {"goal": "整理报销", "decision_event_id": decided.event_id})
    confirm = {
        "reason": "awaiting_confirmation",
        "intercepted": [
            {"tool_call_id": "call-a", "tool_name": "delete_file", "arguments": "path=a.xlsx"},
            {"tool_call_id": "call-b", "tool_name": "send_mail", "arguments": "x", "arguments_truncated": True},
        ],
    }
    return (
        decided,
        started,
        noise(3),
        completed(4, "read_file", args="reports/0930.xlsx", result="已读取，共 12 行"),
        intent(5, "op_12", target="reminder:BX-0930"),
        completed(6, "submit_reminder", op="op_12", args="单号=BX-0930", result="已提醒", refs=("receipt:77",)),
        shared_file(7, "out_f", caption="季度报告"),
        event(8, EventType.WINDOW_SWITCHED, {"archive_ref": "archive#7", "window_seq": 2}),
        event(9, EventType.EXECUTION_PAUSED, confirm),
    )


@pytest.mark.asyncio
async def test_the_record_keeps_every_event_and_rebuilds_it_exactly():
    """PA-17-V-01: header plus line give back the saved event; nothing is merged, translated or dropped."""
    log = mixed_log()
    taken = await snapshot(log)
    head, lines = LOG.header(taken), LOG.event_lines(taken)
    assert taken.unusable is None and head["usable"] is True and head["events"] == len(log)
    assert [seq for seq, _ in lines] == list(range(1, 10))
    for original, (_, line) in zip(log, lines):
        again = rebuilt(head, line)
        assert again.event_id == original.event_id and again.proposal_digest() == original.proposal_digest()
    assert head["ending_seqs"] == [9] and head["source_mismatch"] is False
    assert head["missing_seq_ranges"] == [] and head["skipped"] == {} and head["duplicates_removed"] == 0


@pytest.mark.asyncio
async def test_only_referenced_event_ids_stay_and_false_or_zero_values_are_kept():
    """PA-17-V-20: null and empty values go; 0, false and an event ID another event names stay."""
    taken = await snapshot(
        (
            *mixed_log(),
            shared_file(10, "out_z", path="empty.md"),
        )
    )
    rows = {seq: json.loads(line) for seq, line in LOG.event_lines(taken)}
    assert rows[1]["event_id"] == taken.events[0].event_id, "named by execution.started.decision_event_id"
    assert all("event_id" not in row for seq, row in rows.items() if seq != 1)
    assert rows[9]["payload"]["intercepted"][0]["arguments_truncated"] is False
    assert rows[4]["visibility"] == "private"
    assert "target_ref" not in rows[4]["payload"] and "result_refs" not in rows[4]["payload"]
    assert all(name not in rows[4] for name in ("namespace_id", "request_id", "execution_id", "source_ref"))


@pytest.mark.asyncio
async def test_an_empty_log_is_unusable(monkeypatch):
    """PA-17-V-02."""
    logged = capture(monkeypatch)
    taken = await snapshot(())
    [(level, text)] = logged
    assert taken.unusable == "empty" and level == "warning" and "log is empty" in text
    head = LOG.header(taken)
    assert head["usable"] is False and head["unusable_reason"] == "empty" and head["events"] == 0
    assert LOG.event_lines(taken) == ()


@pytest.mark.asyncio
async def test_a_failed_read_is_unusable_without_host_detail(monkeypatch):
    """PA-17-V-03."""
    logged = capture(monkeypatch)
    taken = await snapshot(error=PermissionError(SECRET))
    assert taken.unusable == "read_failed" and SECRET not in LOG.preview(taken)
    [(level, text)] = logged
    assert level == "warning" and SECRET not in text and "PermissionError" in text and TARGET in text


@pytest.mark.asyncio
async def test_cancellation_propagates():
    with pytest.raises(asyncio.CancelledError):
        await snapshot(error=asyncio.CancelledError())


@pytest.mark.parametrize("bad", ["", "has space", "a\x00b", "a\nb", "x" * 513, None, 123, b"execution-2"])
@pytest.mark.asyncio
async def test_an_invalid_execution_id_is_rejected_before_the_port(bad):
    """PA-17-V-15: the Identifier constraint applies; an argument error is not a missing record."""
    commit = Commit()
    with pytest.raises(BaseError) as exc_info:
        await LOG.read_snapshot(build_context(commit), bad)
    assert exc_info.value.status == StatusCode.DEEPAGENT_INPUT_PARAM_ERROR
    assert commit.calls == [], "the port is not called"


@pytest.mark.asyncio
async def test_a_wrong_context_is_rejected_before_the_port():
    with pytest.raises(BaseError) as exc_info:
        await LOG.read_snapshot(object(), TARGET)
    assert exc_info.value.status == StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR


OTHERS = {
    "namespace": {"namespace_id": "other"},
    "agent": {"agent_id": "agent-b"},
    "session": {"session_id": "session-b"},
    "request": {"request_id": "request-9"},
    "execution": {"execution_id": "e-1"},
}


@pytest.mark.parametrize("bad", [*OTHERS, "not_an_event"])
@pytest.mark.asyncio
async def test_an_item_that_cannot_be_used_is_skipped_and_the_rest_stays(bad, monkeypatch):
    """PA-17-V-04: the pending write is still there; the skipped item is named by its place, not its content."""
    logged = capture(monkeypatch)
    if bad in OTHERS:
        stray = event(3, EventType.PROGRESS_UPDATED, {"text": SECRET}, source=replace(OLD, **OTHERS[bad]))
    else:
        stray = {"event_type": "progress.updated", "local_seq": 3, "payload": {"text": SECRET}}
    taken = await snapshot((progress(1, "已核对"), stray, intent(2, "op_a", "send_mail")))
    category = "invalid_event" if bad == "not_an_event" else "foreign_event"
    assert taken.unusable is None and [event.local_seq for event in taken.events] == [1, 2]
    head, lines = preview_of(taken)
    assert head["skipped"] == {category: {"count": 1, "indexes": [1]}}, "its own local_seq is not trusted"
    assert head["missing_seq_ranges"] == [] and lines[1]["payload"]["operation_id"] == "op_a"
    assert SECRET not in LOG.preview(taken) and SECRET not in json.dumps(LOG.header(taken))
    [(level, text)] = logged
    assert level == "error" and f"{category}=1" in text and SECRET not in text


@pytest.mark.asyncio
async def test_a_local_seq_with_different_events_skips_the_whole_group():
    """PA-17-V-16: which one was saved for real cannot be told, so neither is used; identical copies count once."""
    again = progress(2, "b")
    log = (progress(1, "a"), again, progress(2, "b changed"), again, intent(3, "op_a"), intent(3, "op_a"))
    taken = await snapshot(log)
    assert [event.local_seq for event in taken.events] == [1, 3]
    head = LOG.header(taken)
    assert head["skipped"] == {"conflicting_events": {"count": 3, "local_seqs": [2]}}
    assert head["duplicates_removed"] == 1 and head["missing_seq_ranges"] == [], "a conflicting seq is not a gap"


@pytest.mark.asyncio
async def test_a_different_source_is_kept_on_its_event_and_flagged():
    """PA-17-V-17."""
    other = replace(OLD, source_ref="source:message-2")
    taken = await snapshot((progress(1, "a"), event(2, EventType.PROGRESS_UPDATED, {"text": "b"}, source=other)))
    head, lines = LOG.header(taken), LOG.event_lines(taken)
    assert head["source_ref"] == OLD.source_ref and head["source_mismatch"] is True
    assert "source_ref" not in json.loads(lines[0][1]) and json.loads(lines[1][1])["source_ref"] == "source:message-2"


@pytest.mark.asyncio
async def test_the_header_source_comes_from_the_log_not_the_current_execution():
    own = replace(OLD, source_ref="source:message-0")
    taken = await snapshot((event(1, EventType.PROGRESS_UPDATED, {"text": "a"}, source=own),))
    assert LOG.header(taken)["source_ref"] == "source:message-0" != CURRENT.source_ref


@pytest.mark.parametrize("returned", [None, SECRET, {"events": [SECRET]}])
@pytest.mark.asyncio
async def test_a_return_value_that_is_not_a_sequence_is_unusable(returned, monkeypatch):
    """PA-17-V-18."""
    logged = capture(monkeypatch)
    taken = await snapshot(returned)
    assert taken.unusable == "not_a_sequence" and SECRET not in LOG.preview(taken)
    [(level, text)] = logged
    assert level == "error" and "not a sequence" in text


@pytest.mark.asyncio
async def test_a_list_is_accepted_as_a_tolerance():
    taken = await snapshot([progress(1, "a")])
    assert taken.unusable is None and len(taken.events) == 1


@pytest.mark.asyncio
async def test_a_log_without_one_usable_event_is_unusable_and_says_what_was_skipped():
    """PA-17-V-19."""
    taken = await snapshot(
        ({"local_seq": 1}, event(2, EventType.PROGRESS_UPDATED, {"text": "x"}, source=replace(OLD, request_id="r-9")))
    )
    head = LOG.header(taken)
    assert taken.unusable == "no_usable_event" and head["usable"] is False
    assert head["skipped"] == {
        "invalid_event": {"count": 1, "indexes": [0]},
        "foreign_event": {"count": 1, "indexes": [1]},
    }


@pytest.mark.asyncio
async def test_a_long_skip_list_keeps_the_exact_count():
    """PA-17-V-23."""
    strays = tuple({"local_seq": index} for index in range(30))
    head = LOG.header(await snapshot((progress(1, "a"), *strays)))
    assert head["skipped"]["invalid_event"]["count"] == 30 and len(head["skipped"]["invalid_event"]["indexes"]) == 20


@pytest.mark.asyncio
async def test_gaps_are_computed_before_the_preview_leaves_events_out():
    """PA-17-V-05: counted events are present in the log, so their seqs are not reported missing."""
    taken = await snapshot((progress(1, "a"), noise(2), progress(3, "b"), progress(6, "c")))
    head, lines = preview_of(taken)
    assert head["missing_seq_ranges"] == [[4, 5]] and head["ending_seqs"] == []
    assert head["counted_only"]["model.completed"] == {"events": 1}
    assert [line["local_seq"] for line in lines] == [1, 3, 6]


@pytest.mark.asyncio
async def test_the_preview_counts_usage_and_deltas_and_lists_every_other_event():
    """PA-17-V-21: reply deltas are streamed pieces, not proof of delivery; counted with their length."""
    deltas = (noise(1), event(2, EventType.REPLY_DELTA, {"text": "第二段回复"}), noise(5), noise(8))
    taken = await snapshot((*deltas, intent(3, "op_a", "send_mail"), progress(4, "已核对")))
    head, lines = preview_of(taken)
    assert head["preview"] is True and head["usable"] is True
    assert head["counted_only"] == {"model.completed": {"events": 2}, "reply.delta": {"events": 2, "chars": 9}}
    assert [line["event_type"] for line in lines] == ["tool.intent", "progress.updated"]
    assert len(LOG.event_lines(taken)) == 6, "the record keeps them"


@pytest.mark.asyncio
async def test_long_strings_are_cut_in_the_preview_only_and_each_cut_has_its_path():
    """PA-17-V-22."""
    long_text = "长" * 6500
    confirm = {
        "reason": "awaiting_confirmation",
        "intercepted": [
            {"tool_call_id": "call-a", "tool_name": "delete_file", "arguments": "path=a.xlsx"},
            {"tool_call_id": "call-b", "tool_name": "send_mail", "arguments": "m" * 3000},
        ],
    }
    log = (answer(1, "out_a", text=long_text), event(2, EventType.EXECUTION_PAUSED, confirm))
    taken = await snapshot(log)
    head, lines = preview_of(taken)
    assert head["truncated"] == [
        {"local_seq": 1, "path": "/payload/text", "original_chars": 6500, "included_chars": 2000},
        {"local_seq": 2, "path": "/payload/intercepted/1/arguments", "original_chars": 3000, "included_chars": 2000},
    ]
    assert lines[0]["payload"]["text"] == long_text[:2000]
    assert lines[1]["payload"]["intercepted"][0]["arguments"] == "path=a.xlsx", "short fields are untouched"
    assert taken.events[0].payload.text == long_text, "the snapshot is not changed"
    assert json.loads(LOG.event_lines(taken)[0][1])["payload"]["text"] == long_text, "nor the record"


def test_a_cut_path_escapes_its_keys():
    truncated: list = []
    LOG._cut({"a/b~c": "x" * 2001}, "", 7, truncated)
    assert truncated[0]["path"] == "/a~1b~0c"


@pytest.mark.asyncio
async def test_a_long_log_keeps_every_write_in_the_preview():
    """PA-17-V-08: nothing is merged; every intent and result stays, in order."""
    log, seq = [], 0
    for index in range(60):
        seq += 1
        log.append(completed(seq, "read_file", args=f"file-{index}.md", result="x" * 400))
        seq += 1
        log.append(intent(seq, f"op_{index}", "send_mail", "m" * 480))
    _, lines = preview_of(await snapshot(tuple(log)))
    assert [line["event_type"] for line in lines].count("tool.intent") == 60
    assert [line["local_seq"] for line in lines] == list(range(1, 121))


def test_the_declared_event_schema_version_is_the_events_own():
    """PA-17-V-24."""
    assert LOG.EVENT_SCHEMA_VERSION == AgentEvent.model_fields["schema_version"].default
