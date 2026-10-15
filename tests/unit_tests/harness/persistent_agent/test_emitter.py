# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Event numbering and the shared character / whole-event byte capacity checks."""

from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module

import pytest
from pydantic import ValidationError

from openjiuwen.core.common.exception.codes import StatusCode

pytestmark = pytest.mark.level0

PKG = "openjiuwen.harness.persistent_agent"
NOW = datetime(2026, 10, 8, 1, 2, 3, 456789, tzinfo=timezone.utc)


def module(name):
    return import_module(f"{PKG}.{name}")


EVENTS = module("schema.events")
MAX_BYTES, MAX_CHARS = EVENTS.EVENT_MAX_BYTES, module("schema.protocol").TEXT_MAX_CHARS


class FixedClock:
    @staticmethod
    def now(tz=None):
        return NOW


class Commit:
    def __init__(self):
        self.events = []

    async def record(self, execution, event):
        self.events.append(event)


def execution():
    return module("schema.execution").ExecutionContext(
        namespace_id="tenant",
        agent_id="business-agent",
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
        capability_snapshot=frozenset({"construction"}),
    )


@pytest.fixture
def emitter(monkeypatch):
    emitter_module = module("execution.emitter")
    monkeypatch.setattr(emitter_module, "datetime", FixedClock)
    commit = Commit()
    return emitter_module.EventEmitter(execution(), commit, module("execution.control").ExecutionControl()), commit


def event_fits(event_type, payload, local_seq, visibility):
    """Ask the real event validator whether this exact event is within both limits."""
    if len(payload["text"]) > MAX_CHARS:
        return False
    try:
        EVENTS.AgentEvent.create(
            execution(),
            event_type=event_type,
            local_seq=local_seq,
            occurred_at=NOW,
            visibility=visibility,
            payload=payload,
        )
    except ValidationError:
        return False
    return True


def output(text):
    return {"output_id": "out_1", "target_ref": "chat-1", "share_mode": "message", "text": text}


TEXTS = {
    "ascii": "".join(chr(0x21 + index % 90) for index in range(2 * MAX_CHARS + 5)),
    "chinese": "".join(chr(0x4E00 + index % 20000) for index in range(MAX_CHARS + 1)),
    "emoji": "".join(chr(0x1F300 + index % 251) for index in range(70000)),
    # Control characters escape to six bytes each, so the byte limit binds before the character limit.
    "json_escapes": "".join(("\x01", "\x1f", '"', "\x02", "\\")[index % 5] for index in range(150000)),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(TEXTS))
async def test_text_is_split_within_both_limits_and_joins_back(emitter, kind):
    emitter, commit = emitter
    text = TEXTS[kind]
    await emitter.emit_text(EVENTS.EventType.REPLY_DELTA, text)
    events = commit.events
    assert "".join(event.payload.text for event in events) == text
    assert [event.local_seq for event in events] == list(range(1, len(events) + 1))
    assert len(events) >= 2
    for event, following in zip(events, events[1:]):
        assert event.proposal_size() <= MAX_BYTES and len(event.payload.text) <= MAX_CHARS
        longer = {"text": event.payload.text + following.payload.text[0]}
        assert not event_fits(event.event_type, longer, event.local_seq, "progress"), "each piece is maximal"
    assert events[-1].proposal_size() <= MAX_BYTES
    if kind in ("ascii", "chinese"):
        assert len(events[0].payload.text) == MAX_CHARS, "the character limit binds first"
    else:
        assert len(events[0].payload.text) < MAX_CHARS, "the whole-event byte limit binds first"


def test_widest_size_bounds_every_execution_sequence_and_time():
    """PA-08-V-09: a Request's widest size covers any of its Executions, so segments never depend on them."""
    emitter_module = module("execution.emitter")
    payload = output("x" * 1000)
    widest = emitter_module.widest_size(execution(), EVENTS.EventType.OUTPUT_PROPOSED, payload, any_execution=True)
    other = replace(execution(), execution_id="e" * 30, source_ref="s")
    assert widest == emitter_module.widest_size(other, EVENTS.EventType.OUTPUT_PROPOSED, payload, any_execution=True)
    longest = replace(execution(), execution_id="\U0001f600" * 512, source_ref='"' * 512)
    for context in (execution(), other, longest):
        for local_seq, occurred_at in ((1, NOW.replace(microsecond=0)), (10**12, NOW)):
            event = EVENTS.AgentEvent.create(
                context,
                event_type=EVENTS.EventType.OUTPUT_PROPOSED,
                local_seq=local_seq,
                occurred_at=occurred_at,
                visibility="candidate",
                payload=payload,
            )
            assert event.proposal_size() <= widest
    own = emitter_module.widest_size(execution(), EVENTS.EventType.OUTPUT_PROPOSED, payload, any_execution=False)
    assert own < widest, "without any_execution only the sequence number and time are widened"


@pytest.mark.asyncio
async def test_field_errors_are_not_reported_as_capacity(emitter):
    emitter, commit = emitter
    with pytest.raises(ValidationError):
        await emitter.emit_text(EVENTS.EventType.REPLY_DELTA, "bad \ud800 surrogate")
    with pytest.raises(ValidationError):
        await emitter.emit(EVENTS.EventType.OUTPUT_PROPOSED, {**output("ok"), "share_mode": "fax"})
    assert commit.events == []
    assert (await emitter.emit(EVENTS.EventType.PROGRESS_UPDATED, {"text": "next"})).local_seq == 1


class FlakyCommit(Commit):
    """Fails the first ``failures`` attempts at each listed event type, then records."""

    def __init__(self, failures):
        super().__init__()
        self.failures = dict(failures)
        self.attempts = []

    async def record(self, execution, event):
        self.attempts.append(event)
        if self.failures.get(event.event_type, 0) > 0:
            self.failures[event.event_type] -= 1
            raise RuntimeError("host could not keep the event")
        self.events.append(event)


def flaky_emitter(monkeypatch, failures):
    emitter_module = module("execution.emitter")
    monkeypatch.setattr(emitter_module, "datetime", FixedClock)
    commit, control = FlakyCommit(failures), module("execution.control").ExecutionControl()
    return emitter_module.EventEmitter(execution(), commit, control), commit, control


FINAL = {"status": "completed", "output_refs": ("out_1",)}


@pytest.mark.asyncio
async def test_a_failed_ending_hands_its_outputs_in_again_and_retries_once(monkeypatch):
    """PA-08-V-07: the same event objects are recorded again, so the host deduplicates them by event_id."""
    emitter, commit, control = flaky_emitter(monkeypatch, {EVENTS.EventType.EXECUTION_FINAL: 1})
    proposal = await emitter.emit(EVENTS.EventType.OUTPUT_PROPOSED, output("answer"))
    final = await emitter.emit_ending(EVENTS.EventType.EXECUTION_FINAL, FINAL, (proposal,))
    assert [event.event_type for event in commit.attempts] == ["output.proposed", "execution.final"] * 2
    assert commit.attempts[2] is proposal and commit.attempts[3] is final, "retried as the original events"
    assert commit.attempts[1] is final and final.local_seq == 2
    assert control.fatal is None
    shown = [emitter.outbox.get_nowait() for _ in range(emitter.outbox.qsize())]
    assert shown == [proposal, final], "each event is shown once"


@pytest.mark.asyncio
async def test_a_second_ending_failure_stops_the_execution(monkeypatch):
    emitter, commit, control = flaky_emitter(
        monkeypatch, {EVENTS.EventType.EXECUTION_PAUSED: 2, EVENTS.EventType.OUTPUT_PROPOSED: 5}
    )
    proposal = await emitter.emit(EVENTS.EventType.OUTPUT_PROPOSED, output("answer"))
    with pytest.raises(Exception) as exc_info:
        await emitter.emit_ending(EVENTS.EventType.EXECUTION_PAUSED, {"reason": "stopped"}, (proposal,))
    assert getattr(exc_info.value, "status", None) == StatusCode.DEEPAGENT_RUNTIME_ERROR
    assert control.fatal is exc_info.value
    assert [event.event_type for event in commit.attempts] == [
        "output.proposed",
        "execution.paused",
        "output.proposed",
        "execution.paused",
    ], "one hand-in again and one retry only"
    shown = [emitter.outbox.get_nowait() for _ in range(emitter.outbox.qsize())]
    assert shown == [proposal], "an unrecorded ending is not shown"


@pytest.mark.asyncio
async def test_an_ending_recorded_first_time_is_recorded_once(monkeypatch):
    emitter, commit, _ = flaky_emitter(monkeypatch, {})
    final = await emitter.emit_ending(EVENTS.EventType.EXECUTION_FINAL, {"status": "completed"})
    assert commit.attempts == [final]
