# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the ON_USER_MESSAGE rail hook.

The hook is the one point at which a rail sees consumed inputs *as inputs*:
it fires on the queued batch before it is joined into a single message, and
after that message is written it is ordinary history that compaction may move,
rewrite or drop, so it can no longer be located by position.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.rail.base import (
    EVENT_METHOD_MAP,
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    UserMessageInputs,
)


class _EditingRail(AgentRail):
    """Rail that prepends context and drops blank inputs, recording what it saw."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, Any]] = []

    async def on_user_message(self, ctx: AgentCallbackContext) -> None:
        """Edit the batch in place and record what arrived."""
        parts = ctx.inputs.parts
        self.seen.append((ctx.inputs.source, list(parts)))
        parts[:] = [part for part in parts if part.strip()]
        parts.insert(0, "[CTX]")


class _InertRail(AgentRail):
    """Rail that overrides nothing, to prove the hook stays opt-in."""


@pytest.mark.level0
def test_event_is_mapped_to_its_method() -> None:
    assert EVENT_METHOD_MAP[AgentCallbackEvent.ON_USER_MESSAGE] == "on_user_message"


@pytest.mark.level0
def test_overriding_rail_registers_the_callback() -> None:
    callbacks = _EditingRail().get_callbacks()
    assert AgentCallbackEvent.ON_USER_MESSAGE in callbacks


@pytest.mark.level0
def test_inert_rail_does_not_register_the_callback() -> None:
    callbacks = _InertRail().get_callbacks()
    assert AgentCallbackEvent.ON_USER_MESSAGE not in callbacks


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rail_edits_the_batch_in_place() -> None:
    """Mutating the list is how a rail contributes; there is no return value.

    Entries are whole inputs, so a rail drops or prepends them rather than
    parsing a joined body.
    """
    rail = _EditingRail()
    parts = ["ship it", "   ", "and tell me"]
    ctx = AgentCallbackContext(agent=MagicMock())
    ctx.inputs = UserMessageInputs(parts=parts, source="query")

    await rail.on_user_message(ctx)

    assert parts == ["[CTX]", "ship it", "and tell me"]
    assert rail.seen == [("query", ["ship it", "   ", "and tell me"])]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_source_distinguishes_the_input_paths() -> None:
    rail = _EditingRail()
    ctx = AgentCallbackContext(agent=MagicMock())

    for source in ("query", "steering", "resume"):
        ctx.inputs = UserMessageInputs(parts=[source], source=source)
        await rail.on_user_message(ctx)

    assert [source for source, _ in rail.seen] == ["query", "steering", "resume"]


@pytest.mark.level0
def test_inputs_default_to_an_empty_batch() -> None:
    inputs = UserMessageInputs()
    assert inputs.parts == []
    assert inputs.source == "query"


@pytest.mark.level0
def test_each_inputs_object_gets_its_own_part_list() -> None:
    """A shared default list would leak one batch's edits into the next."""
    first = UserMessageInputs()
    first.parts.append("leaked")
    assert UserMessageInputs().parts == []
