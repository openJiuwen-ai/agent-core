# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A tool-call veto is scoped to its call; invoke-level extra stays shared.

Two guarantees are pinned here, because the obvious fix for the first one
breaks the second:

1. A veto belongs to the call that raised it. Rails used to record it as
   ctx.extra["_skip_tool"] and the executor popped it once, before the tool
   body ran. With one shared extra dict for a whole model turn, a call that
   was still inside its BEFORE hook could lose the flag to a sibling that
   reached the body first: the sibling was skipped with its own empty result and
   the vetoed call then ran for real. Both halves are silent, so the model saw a
   successful call that did nothing. The marker now travels on the per-call
   context (mark_tool_skipped) instead.

2. extra keeps its invoke-level meaning. Rails store state there that must
   survive one call and be visible to the next (repeated-failure counters,
   skill shortlist caches). Copying the dict per call would silently freeze that
   state at its first value, so it is shared and the write-back path is
   asserted below.
"""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    tool_call_skipped,
)

VETO_TEXT = "[POLICY] calendar writes are vetoed for this call"
REPORT = "REPORT-READY"
WAIT_SECONDS = 5


class VetoRail:
    """Vetoes fire_reminder and keeps it inside its BEFORE hook until released."""

    def __init__(self):
        self.veto_set = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, event, ctx):
        if event != AgentCallbackEvent.BEFORE_TOOL_CALL:
            return
        if ctx.inputs.tool_name == "fire_reminder":
            ctx.mark_tool_skipped()
            ctx.inputs.tool_result = VETO_TEXT
            ctx.inputs.tool_msg = ToolMessage(
                content=VETO_TEXT, tool_call_id=ctx.inputs.tool_call.id
            )
            self.veto_set.set()
            # Stay in the hook: the flag is set but not yet consumed by this call.
            await self.release.wait()
        elif ctx.inputs.tool_name == "write_report":
            # Enter the body only once the sibling's veto is in place.
            await asyncio.wait_for(self.veto_set.wait(), WAIT_SECONDS)


class FailureStreakRail:
    """Counts consecutive failures in extra, like the host circuit breaker."""

    def __init__(self, threshold=3):
        self.threshold = threshold
        self.counts = []
        self.force_finished = False

    async def execute(self, event, ctx):
        if event != AgentCallbackEvent.AFTER_TOOL_CALL:
            return
        streak = int(ctx.extra.get("fail_streak", 0)) + 1
        ctx.extra["fail_streak"] = streak
        self.counts.append(streak)
        if streak >= self.threshold:
            ctx.request_force_finish({"error": "repeated failure"})
            self.force_finished = True


class LegacyExtraVetoRail:
    """Out-of-tree rails still write the shared ``extra["_skip_tool"]`` key.

    Covered for the single-call case only: the key lives in the invoke-shared
    dict, so in a concurrent batch a sibling call can still consume it first and
    re-open the #2413 swap. The path is kept for compatibility, documented as
    deprecated, and excluded from the per-call guarantee.
    """

    async def execute(self, event, ctx):
        if event != AgentCallbackEvent.BEFORE_TOOL_CALL:
            return
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = VETO_TEXT
        ctx.inputs.tool_msg = ToolMessage(
            content=VETO_TEXT, tool_call_id=ctx.inputs.tool_call.id
        )


class Agent:
    def __init__(self, callbacks):
        self.agent_callback_manager = callbacks


class Payload(Tool):
    """Records that it ran, then returns a fixed payload."""

    def __init__(self, name, payload, started=None):
        super().__init__(
            card=ToolCard(
                id=name, name=name, input_params={"type": "object"}, parallel_safe=True
            )
        )
        self.payload = payload
        self.started = started

    async def invoke(self, inputs, **kwargs):
        if self.started is not None:
            self.started.set()
        return self.payload

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def _call(call_id, name):
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def _manager(monkeypatch, *tools):
    manager = AbilityManager(owner_id="skip-tool-isolation-test")
    by_id = {}
    for tool in tools:
        manager.add(tool.card)
        by_id[tool.card.id] = tool
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda tool_id=None, **_: by_id[tool_id])
    return manager


@pytest.mark.asyncio
async def test_a_veto_does_not_skip_its_parallel_sibling(monkeypatch):
    rail = VetoRail()
    reminder_started, report_started = asyncio.Event(), asyncio.Event()
    manager = _manager(
        monkeypatch,
        Payload("fire_reminder", "REMINDER-SENT", reminder_started),
        Payload("write_report", REPORT, report_started),
    )

    batch = asyncio.create_task(
        manager.execute(
            AgentCallbackContext(agent=Agent(rail)),
            [_call("call-fire", "fire_reminder"), _call("call-report", "write_report")],
            None,
        )
    )
    try:
        await asyncio.wait_for(report_started.wait(), WAIT_SECONDS)
    finally:
        rail.release.set()
    results = await asyncio.wait_for(batch, WAIT_SECONDS)

    assert report_started.is_set(), "the sibling ran: only the vetoed call may be skipped"
    assert results[1][0] == REPORT, "the sibling's own result survives the veto next to it"
    assert results[0][0] == VETO_TEXT, "the vetoed call is answered with its veto message"
    assert results[0][1].content == VETO_TEXT
    assert not reminder_started.is_set(), "a vetoed call does not run its tool"


@pytest.mark.asyncio
async def test_extra_stays_shared_across_the_calls_of_one_turn(monkeypatch):
    rail = FailureStreakRail(threshold=3)
    parent = AgentCallbackContext(agent=Agent(rail))
    manager = _manager(
        monkeypatch,
        Payload("step_one", "ONE"),
        Payload("step_two", "TWO"),
        Payload("step_three", "THREE"),
    )

    results = await asyncio.wait_for(
        manager.execute(
            parent,
            [
                _call("call-one", "step_one"),
                _call("call-two", "step_two"),
                _call("call-three", "step_three"),
            ],
            None,
        ),
        WAIT_SECONDS,
    )

    assert [result for result, _ in results] == ["ONE", "TWO", "THREE"]
    assert sorted(rail.counts) == [1, 2, 3], (
        "each AFTER_TOOL_CALL must see the streak left by the previous calls; "
        "a per-call extra copy would pin every call to 1"
    )
    assert parent.extra["fail_streak"] == 3, "writes from tool hooks land in the invoke dict"
    assert rail.force_finished, "the third consecutive failure reaches the breaker"


@pytest.mark.asyncio
async def test_legacy_extra_marker_answers_a_single_call(monkeypatch):
    started = asyncio.Event()
    manager = _manager(monkeypatch, Payload("write_report", REPORT, started))
    parent = AgentCallbackContext(agent=Agent(LegacyExtraVetoRail()))

    results = await asyncio.wait_for(
        manager.execute(parent, [_call("call-report", "write_report")], None),
        WAIT_SECONDS,
    )

    assert results[0][0] == VETO_TEXT
    assert not started.is_set(), "out-of-tree rails keep the veto they always had"
    assert "_skip_tool" not in parent.extra, "the legacy key is consumed once, not left behind"


class VetoObserverRail:
    """Vetoes write_report and records the marker in every tool hook."""

    def __init__(self):
        self.phases = []

    async def execute(self, event, ctx):
        if event == AgentCallbackEvent.BEFORE_TOOL_CALL:
            ctx.mark_tool_skipped()
            ctx.inputs.tool_result = VETO_TEXT
            ctx.inputs.tool_msg = ToolMessage(
                content=VETO_TEXT, tool_call_id=ctx.inputs.tool_call.id
            )
            self.phases.append(("before", ctx.tool_skipped))
        elif event == AgentCallbackEvent.AFTER_TOOL_CALL:
            self.phases.append(("after", ctx.tool_skipped))


@pytest.mark.asyncio
async def test_a_consumed_veto_is_invisible_to_the_after_hooks(monkeypatch):
    """The marker is one-shot, exactly like the legacy extra key it replaces.

    The executor popped ``extra["_skip_tool"]`` before the tool body ran, so
    AFTER_TOOL_CALL hooks never saw it. Answering a vetoed call must leave the
    same view behind, or rails that branch on it would change behaviour.
    """
    rail = VetoObserverRail()
    manager = _manager(monkeypatch, Payload("write_report", REPORT))

    await asyncio.wait_for(
        manager.execute(
            AgentCallbackContext(agent=Agent(rail)),
            [_call("call-report", "write_report")],
            None,
        ),
        WAIT_SECONDS,
    )

    assert rail.phases == [("before", True), ("after", False)]


def test_legacy_write_stays_visible_while_marker_write_is_not_mirrored():
    """Mixed chains: in-repo rails must still see a host legacy veto.

    The reverse direction is deliberately one-way -- mirroring the marker into
    the shared ``extra`` dict would let a sibling consume it again (#2413) --
    so out-of-tree readers that do ``extra.get("_skip_tool")`` must migrate to
    ``tool_call_skipped(ctx)``.
    """
    ctx = AgentCallbackContext(agent=object())

    ctx.extra["_skip_tool"] = True
    assert tool_call_skipped(ctx) is True, "legacy veto stays visible to in-repo rails"
    ctx.extra.pop("_skip_tool", None)

    ctx.mark_tool_skipped()
    assert ctx.tool_skipped is True
    assert "_skip_tool" not in ctx.extra, "marker veto is not mirrored into the shared dict"
    assert tool_call_skipped(ctx) is True
