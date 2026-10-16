# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-08 handoff: candidate identity, answer segments, listing rebuild, file reading and batch preparation.

Entry-level behaviour with a real DeepAgent lives in test_entries.py.
"""

import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.persistent_agent.execution import handoff as HANDOFF
from openjiuwen.harness.persistent_agent.execution.control import ExecutionControl
from openjiuwen.harness.persistent_agent.execution.emitter import EventEmitter, widest_size
from openjiuwen.harness.persistent_agent.schema.events import EVENT_MAX_BYTES, AgentEvent, EventType
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.schema.integration import EXECUTION_END, EXECUTION_HEADER, WINDOW_PART_KEY
from openjiuwen.harness.persistent_agent.schema.protocol import TEXT_MAX_CHARS
from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceReadScope
from openjiuwen.harness.persistent_agent.workspace.path_policy import ResolvedWorkspacePath

pytestmark = pytest.mark.level0

Registration = HANDOFF.Registration
MAX_BYTES = 32 * 1024 * 1024


def execution(**changes):
    context = ExecutionContext(
        namespace_id="tenant",
        agent_id="agent-a",
        session_id="session-a",
        request_id="request-1",
        execution_id="execution-1",
        config_version="v1",
        config_fingerprint="0" * 64,
        source_ref="source:message-1",
        principal_ref="user",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="p1",
        capability_snapshot=frozenset({"construction", "workspace_read"}),
    )
    return replace(context, **changes)


def answer_room(context=None, target_ref="chat-1"):
    probe = {"output_id": "out_" + "0" * 32, "target_ref": target_ref, "share_mode": "message", "text": "x"}
    return EVENT_MAX_BYTES - (
        widest_size(context or execution(), EventType.OUTPUT_PROPOSED, probe, any_execution=True) - 1
    )


def assert_fits_any_event(payload, context):
    for local_seq, occurred_at in (
        (1, datetime(2026, 10, 14, tzinfo=timezone.utc)),
        (10**9, datetime.now(timezone.utc)),
    ):
        event = AgentEvent.create(
            context,
            event_type=EventType.OUTPUT_PROPOSED,
            local_seq=local_seq,
            occurred_at=occurred_at,
            visibility="candidate",
            payload=payload,
        )
        assert event.proposal_size() <= EVENT_MAX_BYTES and len(event.payload.text) <= TEXT_MAX_CHARS


class Access:
    """Host read-scope double over a temporary workspace."""

    def __init__(self, root: Path, status="allowed", **changes):
        self.root = root
        self.status = status
        self.changes = changes
        self.error = None

    async def resolve_read_scope(self, context):
        if self.error is not None:
            raise self.error
        fields = dict(
            status=self.status,
            agent_id=context.agent_id,
            session_id=context.session_id,
            workspace_root=self.root,
            allowed_roots=(self.root,),
            private_sessions_root=self.root / "sessions",
            current_session_root=self.root / "sessions" / context.session_id,
            source_ref="workspace://agent-a",
            policy_revision=context.policy_revision,
        )
        fields.update(self.changes)
        return WorkspaceReadScope(**fields)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "q3.md").write_bytes(b"third quarter")
    (root / "notes.txt").write_bytes(b"notes")
    return root


# PA-08-V-08 identity


def test_identity_is_stable_within_a_request_and_changes_with_what_is_sent():
    content = {"file_sha256": "sha256:" + "a" * 64, "file_size": 3, "file_path": "a.md", "caption": None}
    base = HANDOFF.candidate_output_id(execution(), "chat-1", "upload", content)
    assert base.startswith("out_") and len(base) == 36
    later = execution(execution_id="execution-9", source_ref="source:other")
    assert HANDOFF.candidate_output_id(later, "chat-1", "upload", dict(content)) == base, "any Execution of it"
    variants = [
        (execution(request_id="request-2"), "chat-1", "upload", content),
        (execution(session_id="session-b"), "chat-1", "upload", content),
        (execution(agent_id="agent-b"), "chat-1", "upload", content),
        (execution(namespace_id="other"), "chat-1", "upload", content),
        (execution(), "chat-2", "upload", content),
        (execution(), "chat-1", "attachment", content),
        (execution(), "chat-1", "upload", {**content, "file_path": "b.md"}),
        (execution(), "chat-1", "upload", {**content, "caption": "说明"}),
        (execution(), "chat-1", "upload", {**content, "file_sha256": "sha256:" + "b" * 64}),
        (execution(), "chat-1", "upload", {**content, "file_size": 4}),
    ]
    ids = {HANDOFF.candidate_output_id(*variant) for variant in variants}
    assert base not in ids and len(ids) == len(variants)


@pytest.mark.asyncio
async def test_a_shared_segment_of_two_answers_gets_two_identities():
    """Two answers with an identical first segment must not merge into one delivery."""
    first = "x" * (TEXT_MAX_CHARS - 10) + "\n"
    one = await HANDOFF.prepare_batch(execution(), None, "chat-1", first + "ending one", [], MAX_BYTES)
    two = await HANDOFF.prepare_batch(execution(), None, "chat-1", first + "ending two", [], MAX_BYTES)
    assert one.payloads[0]["text"] == two.payloads[0]["text"] == first
    assert one.payloads[0]["output_id"] != two.payloads[0]["output_id"]


# PA-08-V-09 segments

ANSWERS = {
    "ascii": "".join(chr(0x21 + index % 90) for index in range(2 * TEXT_MAX_CHARS + 5)),
    "emoji": "".join(chr(0x1F300 + index % 251) for index in range(70000)),
    "escapes": "".join(("\x01", "\x1f", '"', "\x02", "\\", "a")[index % 6] for index in range(150000)),
    "lines": "".join(f"line {index:06d} 中文\n" for index in range(30000)),
}


@pytest.mark.parametrize("kind", list(ANSWERS))
def test_segments_join_back_and_fit_any_execution(kind):
    text = ANSWERS[kind]
    segments = HANDOFF.split_answer(text, answer_room())
    assert segments is not None and len(segments) >= 2
    assert "".join(segments) == text
    for context in (execution(), execution(execution_id="\U0001f600" * 512, source_ref='"' * 512)):
        for segment in segments:
            payload = {"output_id": "out_" + "f" * 32, "target_ref": "chat-1", "share_mode": "message"}
            assert_fits_any_event({**payload, "text": segment}, context)
    if kind == "lines":
        assert all(segment.endswith("\n") for segment in segments), "segments end at a line break"


def test_segments_do_not_depend_on_the_execution():
    text = ANSWERS["emoji"]
    widest = execution(execution_id="\U0001f600" * 512)
    assert HANDOFF.split_answer(text, answer_room(execution())) == HANDOFF.split_answer(text, answer_room(widest))


def test_a_segment_never_leaves_only_whitespace_behind():
    room = 100
    text = "a" * 95 + "\n" + " " * 10
    segments = HANDOFF.split_answer(text, room)
    assert "".join(segments) == text and all(segment.strip() for segment in segments)
    assert HANDOFF.split_answer("x" + " " * 300, room) is None, "a whitespace run longer than an event"
    assert HANDOFF.split_answer(" " * 300 + "x", room) is None
    assert HANDOFF.split_answer("short answer", room) == ["short answer"]


@pytest.mark.asyncio
async def test_an_unsplittable_answer_is_a_preparation_failure(monkeypatch):
    monkeypatch.setattr(HANDOFF, "split_answer", lambda text, room: None)
    batch = await HANDOFF.prepare_batch(execution(), None, "chat-1", "answer", [], MAX_BYTES)
    assert batch.failures == (("", "answer_unsplittable"),) and batch.payloads == ()


@pytest.mark.asyncio
async def test_answer_segments_are_identified_by_the_whole_answer():
    text = ANSWERS["ascii"]
    batch = await HANDOFF.prepare_batch(execution(), None, "chat-1", text, [], MAX_BYTES)
    again = await HANDOFF.prepare_batch(execution(execution_id="execution-2"), None, "chat-1", text, [], MAX_BYTES)
    assert batch.output_refs == again.output_refs and batch.answer_segments == len(batch.payloads) >= 2
    digest = hashlib.sha256(text.encode()).hexdigest()
    first = {"answer_sha256": digest, "segment_index": 0, "segment_count": len(batch.payloads)}
    assert batch.payloads[0]["output_id"] == HANDOFF.candidate_output_id(execution(), "chat-1", "message", first)


# PA-08-V-11 rebuild


def share_call(call_id, name=HANDOFF.SHARE_FILE):
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def listed(call_id, path, mode="upload", caption=None):
    return ToolMessage(content=HANDOFF.registration_result(Registration(path, mode, caption)), tool_call_id=call_id)


def end_marker():
    return UserMessage(content="【执行结束】", metadata={WINDOW_PART_KEY: EXECUTION_END})


def test_rebuild_reads_only_share_file_results_of_the_current_chain():
    good = HANDOFF.registration_result(Registration("a.md", "upload", None))
    edited = json.loads(good)
    messages = [
        AssistantMessage(content="", tool_calls=[share_call("old")]),
        listed("old", "old-request.md"),
        end_marker(),
        UserMessage(content="task"),
        AssistantMessage(content="", tool_calls=[share_call("c1"), share_call("c2"), share_call("r1", "remember")]),
        listed("c1", "a.md", caption="first"),
        listed("c2", "b.md", "attachment"),
        listed("r1", "not-share-file.md"),
        AssistantMessage(content="", tool_calls=[share_call(f"x{index}") for index in range(6)] + [share_call("c3")]),
        ToolMessage(content="结果未取得，实际效果需核实", tool_call_id="x0"),
        ToolMessage(content="[Interrupted]", tool_call_id="x1"),
        ToolMessage(content="not json", tool_call_id="x2"),
        ToolMessage(content=json.dumps({**edited, "file_path": "extra.md", "extra": 1}), tool_call_id="x3"),
        ToolMessage(content=json.dumps({**edited, "file_path": "noted.md", "note": "rewritten"}), tool_call_id="x4"),
        ToolMessage(content=json.dumps({**edited, "file_path": "../escape.md"}), tool_call_id="x5"),
        listed("c3", "a.md", caption="latest"),
        UserMessage(content="later"),
        listed("c1", "stray.md"),
        AssistantMessage(content="", tool_calls=[share_call("c4")]),
        listed("c4", "a.md", "attachment"),
    ]
    assert HANDOFF.rebuild_registrations(messages) == [
        Registration("a.md", "upload", "latest"),
        Registration("b.md", "attachment", None),
        Registration("a.md", "attachment", None),
    ], "latest caption, first position; a result outside its call group does not count"
    assert HANDOFF.rebuild_registrations(messages[:3]) == [], "another Request's listings never return"


def header(kind="新任务"):
    return UserMessage(content=f"【本次执行】类型：{kind}", metadata={WINDOW_PART_KEY: EXECUTION_HEADER})


def test_an_execution_header_starts_a_new_listing_scope():
    """F_20 D-07: a new task or a continuation lists afresh; a confirmation resume keeps its chain."""
    before = [AssistantMessage(content="", tool_calls=[share_call("e1")]), listed("e1", "before-crash.md")]
    waiting = AssistantMessage(content="", tool_calls=[share_call("w1", "remember")])
    new_task = [end_marker(), header(), UserMessage(content="task"), *before]
    assert HANDOFF.rebuild_registrations(new_task) == [Registration("before-crash.md", "upload", None)]
    assert HANDOFF.rebuild_registrations([*new_task, waiting]) == [Registration("before-crash.md", "upload", None)], (
        "a confirmation resume adds no header and keeps the listings made before it waited"
    )
    resumed = [*new_task, waiting, ToolMessage(content=HANDOFF.SHARE_FILE, tool_call_id="w1"), header("续做")]
    assert HANDOFF.rebuild_registrations(resumed) == [], "listings before a continuation are evidence only"
    again = [*resumed, AssistantMessage(content="", tool_calls=[share_call("e3")]), listed("e3", "after.md")]
    assert HANDOFF.rebuild_registrations(again) == [Registration("after.md", "upload", None)]


@pytest.mark.parametrize(
    "change",
    [
        {"caption": "redacted"},
        {"caption": None},
        {"file_path": "notes.txt"},
        {"share_mode": "attachment"},
        {"receipt": "0" * 32},
        {"note": "rewritten by a rail"},
        {"share_file": "pending"},
    ],
)
def test_a_result_changed_in_the_same_form_is_not_a_listing(change):
    """HI-8: a rail that keeps the success form but changes what it says leaves no listing behind."""
    original = json.loads(HANDOFF.registration_result(Registration("reports/q3.md", "upload", "第三季度")))
    messages = [
        AssistantMessage(content="", tool_calls=[share_call("c1"), share_call("c2")]),
        ToolMessage(content=json.dumps(original, ensure_ascii=False), tool_call_id="c1"),
        ToolMessage(content=json.dumps({**original, **change}, ensure_ascii=False), tool_call_id="c2"),
    ]
    assert HANDOFF.rebuild_registrations(messages[:2]) == [Registration("reports/q3.md", "upload", "第三季度")]
    assert HANDOFF.rebuild_registrations([messages[0], messages[2]]) == []


def test_merge_keeps_the_first_position_and_the_limit():
    table = {}
    for index in range(HANDOFF.MAX_REGISTRATIONS):
        assert HANDOFF.merge_registration(table, Registration(f"f{index}.md", "upload"))
    assert not HANDOFF.merge_registration(table, Registration("new.md", "upload"))
    assert HANDOFF.merge_registration(table, Registration("f0.md", "upload", "updated")), "updates stay allowed"
    assert list(table)[0] == ("f0.md", "upload") and table[("f0.md", "upload")].caption == "updated"
    assert len(table) == HANDOFF.MAX_REGISTRATIONS


# PA-08-V-16 file reading


def resolved(path: Path, relative="file"):
    return ResolvedWorkspacePath(path=path, relative_path=relative, source_ref="workspace://agent-a/" + relative)


def test_reading_in_chunks_gives_the_whole_file_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(HANDOFF, "_READ_CHUNK", 7)
    data = os.urandom(1000)
    target = tmp_path / "data.bin"
    target.write_bytes(data)
    assert HANDOFF.read_for_handoff(resolved(target), 1000) == ("sha256:" + hashlib.sha256(data).hexdigest(), 1000)


def test_the_limit_counts_bytes_read_and_stops_one_byte_past_it(tmp_path, monkeypatch):
    target = tmp_path / "data.bin"
    target.write_bytes(b"x" * 300)
    read = []
    real_read = os.read

    def counting_read(descriptor, size):
        chunk = real_read(descriptor, size)
        read.append(len(chunk))
        return chunk

    monkeypatch.setattr(HANDOFF.os, "read", counting_read)
    assert HANDOFF.read_for_handoff(resolved(target), 300)[1] == 300
    read.clear()
    with pytest.raises(HANDOFF.HandoffFileError) as exc_info:
        HANDOFF.read_for_handoff(resolved(target), 100)
    assert exc_info.value.status == "too_large" and sum(read) == 101, "nothing past one byte over the limit"


def test_a_file_growing_while_read_is_judged_by_the_bytes_read(tmp_path, monkeypatch):
    target = tmp_path / "growing.bin"
    target.write_bytes(b"a" * 10)
    real_read = os.read
    grown = []

    def growing_read(descriptor, size):
        if not grown:
            grown.append(True)
            with open(target, "ab") as handle:
                handle.write(b"b" * 10)
        return real_read(descriptor, size)

    monkeypatch.setattr(HANDOFF.os, "read", growing_read)
    assert HANDOFF.read_for_handoff(resolved(target), 20)[1] == 20
    grown.clear()
    target.write_bytes(b"a" * 10)
    with pytest.raises(HANDOFF.HandoffFileError) as exc_info:
        HANDOFF.read_for_handoff(resolved(target), 15)
    assert exc_info.value.status == "too_large"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks and FIFOs need POSIX")
@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "missing"])
def test_only_regular_files_are_read(tmp_path, monkeypatch, kind):
    target = tmp_path / "target"
    (tmp_path / "real.txt").write_bytes(b"real")
    if kind == "symlink":
        target.symlink_to(tmp_path / "real.txt")
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    opened = []
    real_open = os.open
    monkeypatch.setattr(HANDOFF.os, "open", lambda *args: opened.append(args) or real_open(*args))
    with pytest.raises(HANDOFF.HandoffFileError) as exc_info:
        HANDOFF.read_for_handoff(resolved(target), MAX_BYTES)
    assert exc_info.value.status == ("not_found" if kind == "missing" else "not_a_file")
    assert opened == [], "a target that is not a regular file is never opened"


def test_a_file_swapped_after_the_check_is_refused(tmp_path, monkeypatch):
    target, other = tmp_path / "target.txt", tmp_path / "other.txt"
    target.write_bytes(b"target")
    other.write_bytes(b"other")
    real_lstat = os.lstat
    monkeypatch.setattr(HANDOFF.os, "lstat", lambda path: real_lstat(other))
    with pytest.raises(HANDOFF.HandoffFileError) as exc_info:
        HANDOFF.read_for_handoff(resolved(target), MAX_BYTES)
    assert exc_info.value.status == "not_a_file"


def test_the_listing_check_reads_no_content_and_checks_the_size(tmp_path, monkeypatch):
    target = tmp_path / "data.bin"
    target.write_bytes(b"x" * 10)
    monkeypatch.setattr(HANDOFF.os, "read", lambda *args: pytest.fail("listing reads no content"))
    HANDOFF.check_shared_file(resolved(target), 10)
    with pytest.raises(HANDOFF.HandoffFileError) as exc_info:
        HANDOFF.check_shared_file(resolved(target), 9)
    assert exc_info.value.status == "too_large"


# PA-08-V-06 batch preparation


@pytest.mark.asyncio
async def test_a_batch_carries_the_answer_then_files_in_listed_order(workspace):
    registrations = [Registration("reports/q3.md", "upload", "第三季度"), Registration("notes.txt", "attachment")]
    batch = await HANDOFF.prepare_batch(execution(), Access(workspace), "chat-1", "answer", registrations, MAX_BYTES)
    assert batch.failures == () and batch.answer_segments == 1
    answer, report, notes = batch.payloads
    assert answer["text"] == "answer" and answer["share_mode"] == "message"
    assert report == {
        "output_id": report["output_id"],
        "target_ref": "chat-1",
        "share_mode": "upload",
        "file_ref": "sha256:" + hashlib.sha256(b"third quarter").hexdigest(),
        "file_path": "reports/q3.md",
        "file_size": len(b"third quarter"),
        "text": "第三季度",
    }
    assert "text" not in notes and notes["share_mode"] == "attachment"
    assert batch.files == (
        ("reports/q3.md", "upload", report["output_id"]),
        ("notes.txt", "attachment", notes["output_id"]),
    )
    assert batch.output_refs == (answer["output_id"], report["output_id"], notes["output_id"])


@pytest.mark.asyncio
async def test_one_failed_file_leaves_the_whole_batch_unprepared(workspace):
    registrations = [Registration("reports/q3.md", "upload"), Registration("gone.md", "upload")]
    batch = await HANDOFF.prepare_batch(execution(), Access(workspace), "chat-1", "answer", registrations, MAX_BYTES)
    assert batch.payloads == () and batch.output_refs == () and batch.answer_segments == 0
    assert batch.failures == (("gone.md", "not_found"),)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access", "status"),
    [
        ("unknown", "unknown"),
        ("forbidden", "forbidden"),
        ("raises", "unknown"),
        ("other-session", "unknown"),
        ("missing", "unknown"),
    ],
)
async def test_authorization_is_taken_again_at_the_end(workspace, access, status):
    port = {
        "unknown": Access(workspace, status="unknown"),
        "forbidden": Access(workspace, status="forbidden"),
        "raises": Access(workspace),
        "other-session": Access(workspace, session_id="session-b"),
        "missing": None,
    }[access]
    if access == "raises":
        port.error = RuntimeError("host lookup failed at /secret/host/path")
    registrations = [Registration("reports/q3.md", "upload")]
    batch = await HANDOFF.prepare_batch(execution(), port, "chat-1", None, registrations, MAX_BYTES)
    assert batch.failures == (("reports/q3.md", status),)


@pytest.mark.asyncio
async def test_too_many_outputs_is_a_batch_failure(workspace, monkeypatch):
    monkeypatch.setattr(HANDOFF, "MAX_OUTPUTS", 2)
    registrations = [Registration("reports/q3.md", "upload"), Registration("notes.txt", "upload")]
    batch = await HANDOFF.prepare_batch(execution(), Access(workspace), "chat-1", "answer", registrations, MAX_BYTES)
    assert batch.failures == (("", "too_many_outputs"),) and batch.payloads == ()


def test_failures_are_described_by_logical_path_and_category():
    failures = tuple((f"dir/f{index}.md", "not_found") for index in range(7)) + (("", "too_many_outputs"),)
    text = HANDOFF.describe_failures(failures)
    assert text.startswith("dir/f0.md: not_found; dir/f1.md: not_found")
    assert text.endswith("(8 in all)") and "f5.md" not in text
    assert HANDOFF.describe_failures((("", "answer_unsplittable"),)) == "batch: answer_unsplittable"
    assert len(HANDOFF.describe_failures(tuple(("p" * 1024, "not_found") for _ in range(9)))) == 4096


class Commit:
    def __init__(self):
        self.events = []

    async def record(self, context, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_hand_off_records_the_batch_in_delivery_order(workspace):
    registrations = [Registration("reports/q3.md", "upload")]
    batch = await HANDOFF.prepare_batch(execution(), Access(workspace), "chat-1", "answer", registrations, MAX_BYTES)
    commit = Commit()
    emitter = EventEmitter(execution(), commit, ExecutionControl())
    events = await HANDOFF.hand_off(emitter, batch)
    assert events == tuple(commit.events)
    assert [event.payload.output_id for event in events] == list(batch.output_refs)
    assert all(event.visibility == "candidate" for event in events)
    assert await HANDOFF.hand_off(emitter, HANDOFF.PreparedBatch()) == ()


def test_file_reading_runs_off_the_event_loop(workspace, monkeypatch):
    threads = []
    real = asyncio.to_thread

    async def to_thread(function, *args):
        threads.append(function.__name__)
        return await real(function, *args)

    monkeypatch.setattr(HANDOFF.asyncio, "to_thread", to_thread)
    asyncio.run(
        HANDOFF.prepare_batch(
            execution(), Access(workspace), "chat-1", None, [Registration("notes.txt", "upload")], MAX_BYTES
        )
    )
    assert threads == ["read_for_handoff"]
