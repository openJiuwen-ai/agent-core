# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Event numbering and the shared character / whole-event byte capacity checks."""

from datetime import datetime, timezone
from importlib import import_module

import pytest
from pydantic import ValidationError

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


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["\U0001f300", "\x01", '"', "a", "中"])
async def test_emit_if_fits_stops_exactly_at_the_limits(emitter, monkeypatch, unit):
    first, commit = emitter
    longest = 1
    while event_fits(EVENTS.EventType.OUTPUT_PROPOSED, output(unit * (longest * 2)), 1, "candidate"):
        longest *= 2
    low, high = longest, longest * 2
    while high - low > 1:
        middle = (low + high) // 2
        if event_fits(EVENTS.EventType.OUTPUT_PROPOSED, output(unit * middle), 1, "candidate"):
            low = middle
        else:
            high = middle
    assert await first.emit_if_fits(EVENTS.EventType.OUTPUT_PROPOSED, output(unit * high)) is None
    assert commit.events == [], "a rejected proposal is neither recorded nor numbered"
    recorded = await first.emit_if_fits(EVENTS.EventType.OUTPUT_PROPOSED, output(unit * low))
    assert recorded is not None and recorded.local_seq == 1 and commit.events == [recorded]
    assert recorded.proposal_size() <= MAX_BYTES and len(recorded.payload.text) <= MAX_CHARS


@pytest.mark.asyncio
async def test_field_errors_are_not_reported_as_capacity(emitter):
    emitter, commit = emitter
    with pytest.raises(ValidationError):
        await emitter.emit_if_fits(EVENTS.EventType.OUTPUT_PROPOSED, output("bad \ud800 surrogate"))
    with pytest.raises(ValidationError):
        await emitter.emit_text(EVENTS.EventType.REPLY_DELTA, "bad \ud800 surrogate")
    with pytest.raises(ValidationError):
        await emitter.emit_if_fits(EVENTS.EventType.OUTPUT_PROPOSED, {**output("ok"), "share_mode": "fax"})
    assert commit.events == []
    assert (await emitter.emit(EVENTS.EventType.PROGRESS_UPDATED, {"text": "next"})).local_seq == 1
