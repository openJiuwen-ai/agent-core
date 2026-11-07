# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ops registration and explicit trigger tests for ``prompt.compose``."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.builtins.event_types.prompt_compose import (
    PROMPT_COMPOSE,
    PromptComposeInput,
    PromptComposeOutput,
    PromptSectionSpec,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, HandlerLevel, SessionContext


def _output() -> PromptComposeOutput:
    return PromptComposeOutput(
        sections=(
            PromptSectionSpec(
                id="identity",
                text="identity",
                priority=10,
                category="system_prompt",
                carrier="system_message",
                detail={},
            ),
        )
    )


@pytest.mark.asyncio
async def test_ops_build_registers_prompt_compose_without_prompt_section() -> None:
    bus = ExtensionEventBus()
    result = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    assert isinstance(result, EventHandlerOpsBuilder)
    ops = result.build(session_context=SessionContext("ops-prompt-session"))
    assert callable(ops.prompt.compose)
    with pytest.raises(BaseError):
        _ = ops.section


@pytest.mark.asyncio
async def test_ops_prompt_compose_triggers_a_new_explicit_event_chain() -> None:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    observed = Mock()

    async def compose_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptComposeOutput:
        del ops, event_next
        observed(event.event_input)
        return _output()

    bus.register_event_handler(PROMPT_COMPOSE, compose_handler, {}, HandlerLevel.USER)

    async def outer_core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        assert ops is not None
        return await ops.prompt.compose(
            PromptComposeInput(
                model="mock-model",
                prompt_model=None,
                surfaces=(),
                tools=(),
                output_style=None,
                traits=(),
                detail={},
            )
        )

    bus.register_event_type("test.prompt.ops", lambda value, pattern: not pattern and value is None, outer_core)
    result = await bus.trigger(
        Event("test.prompt.ops", None),
        SessionContext("ops-prompt-session"),
    )

    assert result == _output()
    observed.assert_called_once()
