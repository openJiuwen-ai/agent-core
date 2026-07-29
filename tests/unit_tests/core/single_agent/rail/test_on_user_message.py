# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the ON_USER_MESSAGE rail hook.

The hook is the one point at which a rail sees a consumed input *as an input*:
after it is written, the message is ordinary history that compaction may move,
rewrite or drop, so it can no longer be located by position.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.rail.base import (
    EVENT_METHOD_MAP,
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    UserMessageInputs,
)


class _PrefixingRail(AgentRail):
    """Rail that prefixes every admitted input, recording what it saw."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, Any]] = []

    async def on_user_message(self, ctx: AgentCallbackContext) -> None:
        """Prefix the pending message and record its source."""
        message = ctx.inputs.message
        self.seen.append((ctx.inputs.source, message.content))
        message.content = f"[CTX] {message.content}"


class _InertRail(AgentRail):
    """Rail that overrides nothing, to prove the hook stays opt-in."""


@pytest.mark.level0
def test_event_is_mapped_to_its_method() -> None:
    assert EVENT_METHOD_MAP[AgentCallbackEvent.ON_USER_MESSAGE] == "on_user_message"


@pytest.mark.level0
def test_overriding_rail_registers_the_callback() -> None:
    callbacks = _PrefixingRail().get_callbacks()
    assert AgentCallbackEvent.ON_USER_MESSAGE in callbacks


@pytest.mark.level0
def test_inert_rail_does_not_register_the_callback() -> None:
    callbacks = _InertRail().get_callbacks()
    assert AgentCallbackEvent.ON_USER_MESSAGE not in callbacks


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rail_rewrites_the_message_in_place() -> None:
    """Mutating the message is how a rail contributes; there is no return value."""
    rail = _PrefixingRail()
    message = UserMessage(content="ship it")
    ctx = AgentCallbackContext(agent=MagicMock())
    ctx.inputs = UserMessageInputs(message=message, source="query")

    await rail.on_user_message(ctx)

    assert message.content == "[CTX] ship it"
    assert rail.seen == [("query", "ship it")]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_source_distinguishes_the_input_paths() -> None:
    rail = _PrefixingRail()
    ctx = AgentCallbackContext(agent=MagicMock())

    for source in ("query", "steering", "resume"):
        ctx.inputs = UserMessageInputs(message=UserMessage(content=source), source=source)
        await rail.on_user_message(ctx)

    assert [source for source, _ in rail.seen] == ["query", "steering", "resume"]


@pytest.mark.level0
def test_inputs_default_to_an_empty_message() -> None:
    inputs = UserMessageInputs()
    assert inputs.message is None
    assert inputs.source == "query"
