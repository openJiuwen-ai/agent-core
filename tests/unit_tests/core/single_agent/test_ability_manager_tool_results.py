# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AbilityManager.execute(on_tool_result=...): what each call would leave in the context, as it goes.

Only content whose rail processing finished is ever reported; until then a call that returned or
raised is reported as a note without its content, and a call in flight as None.
"""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent

RAW = "RAW-TOOL-OUTPUT"
RETURNED_NOTE = "[Result not included]"
RAISED_NOTE = "[Error not included]"


class Callbacks:
    """Stands in for the agent callback manager; acts like a host rail checking tool output."""

    def __init__(self, rewrite=None, hold_after=None, fail_on=None, hold_on=AgentCallbackEvent.AFTER_TOOL_CALL):
        self.events = []
        self.rewrite = rewrite
        self.hold_after = hold_after
        self.fail_on = fail_on
        self.hold_on = hold_on
        self.after_reached = asyncio.Event()

    async def execute(self, event, ctx):
        self.events.append((event, ctx.inputs.tool_call.id))
        if event == self.hold_on:
            self.after_reached.set()
            if self.hold_after is not None:
                await self.hold_after.wait()
        if event == self.fail_on:
            raise RuntimeError("output check failed")
        if event != AgentCallbackEvent.AFTER_TOOL_CALL:
            return
        if self.rewrite is not None:
            ctx.inputs.tool_msg = ToolMessage(content=self.rewrite, tool_call_id=ctx.inputs.tool_call.id)


class Agent:
    def __init__(self, callbacks):
        self.agent_callback_manager = callbacks


class Step(Tool):
    """Returns RAW at once, waits for a gate, raises, or asks for confirmation."""

    def __init__(self, name, behaviour="return", gate=None, started=None, parallel_safe=True):
        super().__init__(
            card=ToolCard(id=name, name=name, input_params={"type": "object"}, parallel_safe=parallel_safe)
        )
        self.behaviour = behaviour
        self.gate = gate
        self.started = started

    async def invoke(self, inputs, **kwargs):
        if self.started is not None:
            self.started.set()
        if self.behaviour == "wait":
            await self.gate.wait()
        if self.behaviour == "raise":
            raise RuntimeError("upload refused")
        if self.behaviour == "interrupt":
            raise ToolInterruptException(
                request=InterruptRequest(message="approve?"),
                tool_call=ToolCall(id="call-i", type="function", name=self.card.name, arguments="{}"),
            )
        return RAW

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def _call(call_id, name):
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def _manager(monkeypatch, *tools):
    manager = AbilityManager(owner_id="tool-results-test")
    by_id = {}
    for tool in tools:
        manager.add(tool.card)
        by_id[tool.card.id] = tool
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda tool_id=None, **_: by_id[tool_id])
    return manager


def _recorder():
    seen = []

    def record(call, message):
        seen.append((call.id, None if message is None else message.content))

    return seen, record


def _ctx(callbacks):
    return AgentCallbackContext(agent=Agent(callbacks))


def _no_raw(seen):
    assert all(content is None or RAW not in content for _, content in seen), "unprocessed content was reported"


@pytest.mark.asyncio
async def test_only_processed_content_is_reported(monkeypatch):
    callbacks = Callbacks(rewrite="checked by a host rail")
    manager = _manager(monkeypatch, Step("upload"))
    seen, record = _recorder()
    results = await manager.execute(_ctx(callbacks), [_call("call-a", "upload")], None, on_tool_result=record)
    assert seen[0] == ("call-a", None), "an attempt starts without a result"
    assert seen[1][1].startswith(RETURNED_NOTE) and "'upload' returned" in seen[1][1]
    assert seen[2] == ("call-a", "checked by a host rail")
    _no_raw(seen)
    assert results[0][1].content == "checked by a host rail", "the batch uses the same processed message"
    assert [event for event, _ in callbacks.events] == [
        AgentCallbackEvent.BEFORE_TOOL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
    ], "tool rails still run around the call"


@pytest.mark.asyncio
async def test_a_tool_error_is_noted_until_its_exception_rails_finish(monkeypatch):
    manager = _manager(monkeypatch, Step("upload", "raise"))
    seen, record = _recorder()
    results = await manager.execute(_ctx(Callbacks()), [_call("call-a", "upload")], None, on_tool_result=record)
    assert [content for _, content in seen][:1] == [None]
    assert seen[1][1].startswith(RAISED_NOTE) and "raised an exception" in seen[1][1]
    assert "upload refused" in seen[2][1], "the processed error message replaces the note"
    assert results[0][1].content == seen[2][1]


@pytest.mark.asyncio
async def test_interrupted_calls_report_nothing_but_their_start(monkeypatch):
    manager = _manager(monkeypatch, Step("ask", "interrupt"))
    seen, record = _recorder()
    await manager.execute(_ctx(Callbacks()), [_call("call-i", "ask")], None, on_tool_result=record)
    assert seen == [("call-i", None)], "a confirmation interrupt is no result"


@pytest.mark.asyncio
async def test_cancelled_batch_reports_notes_never_raw_content(monkeypatch):
    """Parallel-safe calls: the cancel reaches the caller, who keeps only what was reported."""
    hold = asyncio.Event()
    callbacks = Callbacks(hold_after=hold)
    gate, started = asyncio.Event(), asyncio.Event()
    manager = _manager(monkeypatch, Step("upload"), Step("slow", "wait", gate, started))
    seen, record = _recorder()
    batch = asyncio.create_task(
        manager.execute(
            _ctx(callbacks), [_call("call-a", "upload"), _call("call-b", "slow")], None, on_tool_result=record
        )
    )
    await asyncio.wait_for(callbacks.after_reached.wait(), 5)
    await asyncio.wait_for(started.wait(), 5)
    batch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await batch
    latest = dict(seen)
    assert latest["call-a"].startswith(RETURNED_NOTE), "returned, processing cut off: a note, not the result"
    assert latest["call-b"] is None, "still in flight: no result"
    _no_raw(seen)


@pytest.mark.asyncio
async def test_a_cancel_absorbed_by_an_exclusive_call_ends_the_batch_with_its_note(monkeypatch):
    """Non-parallel-safe calls absorb the cancel: the batch finishes and must not carry raw content."""
    hold = asyncio.Event()
    callbacks = Callbacks(hold_after=hold)
    manager = _manager(monkeypatch, Step("upload", parallel_safe=False))
    seen, record = _recorder()
    batch = asyncio.create_task(
        manager.execute(_ctx(callbacks), [_call("call-a", "upload")], None, on_tool_result=record)
    )
    await asyncio.wait_for(callbacks.after_reached.wait(), 5)
    batch.cancel()
    results = await asyncio.wait_for(batch, 5)
    assert results[0][1].content.startswith(RETURNED_NOTE), "not the raw result, and not 'cancelled by user'"
    _no_raw(seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel_safe", [True, False])
async def test_an_error_cut_off_by_a_cancel_is_noted_without_its_content(monkeypatch, parallel_safe):
    """The tool raised; the cancel arrived while exception rails were still running."""
    callbacks = Callbacks(hold_after=asyncio.Event(), hold_on=AgentCallbackEvent.ON_TOOL_EXCEPTION)
    manager = _manager(monkeypatch, Step("upload", "raise", parallel_safe=parallel_safe))
    seen, record = _recorder()
    batch = asyncio.create_task(
        manager.execute(_ctx(callbacks), [_call("call-a", "upload")], None, on_tool_result=record)
    )
    await asyncio.wait_for(callbacks.after_reached.wait(), 5)
    batch.cancel()
    if parallel_safe:
        with pytest.raises(asyncio.CancelledError):
            await batch
        final = dict(seen)["call-a"]
    else:
        final = (await asyncio.wait_for(batch, 5))[0][1].content
    assert final.startswith(RAISED_NOTE), "not reported as finished, and without the error content"
    assert all(content is None or "upload refused" not in content for _, content in seen)


@pytest.mark.asyncio
async def test_without_a_callback_an_absorbed_cancel_keeps_the_native_result(monkeypatch):
    hold = asyncio.Event()
    callbacks = Callbacks(hold_after=hold)
    manager = _manager(monkeypatch, Step("upload", parallel_safe=False))
    batch = asyncio.create_task(manager.execute(_ctx(callbacks), [_call("call-a", "upload")], None))
    await asyncio.wait_for(callbacks.after_reached.wait(), 5)
    batch.cancel()
    results = await asyncio.wait_for(batch, 5)
    assert results[0][1].content == "[Interrupted] Tool 'upload' execution was cancelled by user."


@pytest.mark.asyncio
@pytest.mark.parametrize("retain", [True, False])
async def test_a_rail_failing_after_the_tool_returned_keeps_raw_content_out_when_retaining(monkeypatch, retain):
    manager = _manager(monkeypatch, Step("upload"))
    seen, record = _recorder()
    options = {"on_tool_result": record} if retain else {}
    results = await manager.execute(
        _ctx(Callbacks(fail_on=AgentCallbackEvent.AFTER_TOOL_CALL)), [_call("call-a", "upload")], None, **options
    )
    if retain:
        assert results[0][1].content.startswith(RETURNED_NOTE)
        _no_raw(seen)
    else:
        assert RAW in results[0][1].content, "personal behaviour is unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [AgentCallbackEvent.ON_TOOL_EXCEPTION, AgentCallbackEvent.AFTER_TOOL_CALL])
async def test_an_error_whose_processing_failed_is_noted_without_its_content(monkeypatch, event):
    """The @rail wrapper only logs a failing chain on an error path; the error is still unprocessed."""
    manager = _manager(monkeypatch, Step("upload", "raise"))
    seen, record = _recorder()
    results = await manager.execute(
        _ctx(Callbacks(fail_on=event)), [_call("call-a", "upload")], None, on_tool_result=record
    )
    assert seen[-1][1].startswith(RAISED_NOTE) and results[0][1].content.startswith(RAISED_NOTE)
    assert all(content is None or "upload refused" not in content for _, content in seen)


@pytest.mark.asyncio
async def test_a_retry_after_a_failed_chain_reports_the_new_attempt(monkeypatch):
    """A failed exception chain of an earlier attempt does not taint the attempt that follows."""

    class RetryOnce(Callbacks):
        async def execute(self, event, ctx):
            await super().execute(event, ctx)
            if event == AgentCallbackEvent.ON_TOOL_EXCEPTION and ctx.retry_attempt == 0:
                ctx.request_retry(0)
                raise RuntimeError("exception check failed")

    class AlwaysRefused(Step):
        attempts = 0

        async def invoke(self, inputs, **kwargs):
            self.attempts += 1
            raise RuntimeError(f"upload refused on attempt {self.attempts}")

    manager = _manager(monkeypatch, AlwaysRefused("upload"))
    seen, record = _recorder()
    results = await manager.execute(_ctx(RetryOnce()), [_call("call-a", "upload")], None, on_tool_result=record)
    assert "attempt 2" in seen[-1][1] and "attempt 2" in results[0][1].content, "the second error was fully processed"


@pytest.mark.asyncio
async def test_without_a_callback_execution_is_unchanged(monkeypatch):
    callbacks = Callbacks()
    manager = _manager(monkeypatch, Step("upload"))
    results = await manager.execute(_ctx(callbacks), [_call("call-a", "upload")], None)
    assert RAW in results[0][1].content and len(callbacks.events) == 2
