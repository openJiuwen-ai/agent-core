# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for interrupt-envelope handling in ``AbilityManager``.

A tool that wraps an agent hands the caller the interrupt envelope verbatim
when that agent pauses: a ``dict`` carrying ``result_type == "interrupt"`` and
``interrupt_ids``. The envelope is a pending question, not an answer, so it
must not become a ``ToolMessage`` — otherwise the question text is written into
the caller's context under the tool_call_id that the real answer will later
claim, and two messages end up sharing one id.

Two neighbouring paths already behave this way: an interrupted workflow returns
``(workflow_output, None)`` from ``_run_workflow``, and a
``ToolInterruptException`` yields ``(exception, None)`` in ``execute``.

An ordinary ``dict`` result is unaffected and still yields a ``ToolMessage``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.interrupt.handler import ToolInterruptHandler
from openjiuwen.core.single_agent.interrupt.state import is_interrupt_envelope
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

INTERRUPT_ENVELOPE: Dict[str, Any] = {
    "result_type": "interrupt",
    "state": [],
    "interrupt_ids": ["inner-1"],
}

PLAIN_DICT_RESULT: Dict[str, Any] = {"output": "42", "status": "ok"}


def _dict_tool(name: str, payload: Dict[str, Any]) -> LocalFunction:
    """A tool whose invoke returns ``payload`` as a bare dict."""
    card = ToolCard(id=name, name=name, description=f"{name} desc", properties={})

    async def _func(**_):  # noqa: ANN202
        return payload

    return LocalFunction(card=card, func=_func)


def _tool_call(name: str) -> ToolCall:
    return ToolCall(id=f"tc-{name}", type="function", name=name, arguments="{}")


class _NoRails:
    """Callback manager that registers no rails, so BEFORE/AFTER fire cheaply."""

    async def execute(self, *_args, **_kwargs) -> None:
        return None


class _StubAgent:
    """Minimal stand-in for the agent that owns the callback context."""

    agent_callback_manager = _NoRails()


async def _execute(tools: List[LocalFunction]) -> List[Tuple[Any, ToolMessage]]:
    """Register ``tools`` and run one ``execute`` round over all of them."""
    await Runner.start()
    am = AbilityManager(owner_id="interrupt-envelope-test")
    try:
        for tool in tools:
            am.add_ability(tool.card, tool)
        ctx = AgentCallbackContext(agent=_StubAgent(), inputs=None, config=None, session=None)
        return await am.execute(
            ctx=ctx,
            tool_call=[_tool_call(tool.card.name) for tool in tools],
            session=None,
            parallel_tool_calls=False,
        )
    finally:
        for tool in tools:
            am.remove_ability(tool.card.name)
        await Runner.stop()


def test_predicate_matches_the_handler_spelling() -> None:
    """The shared predicate and the handler agree on one envelope shape."""
    assert is_interrupt_envelope(INTERRUPT_ENVELOPE) is True
    assert ToolInterruptHandler.is_sub_agent_interrupt(INTERRUPT_ENVELOPE) is True

    # Non-envelopes: plain dict, wrong result_type, and no interrupt_ids.
    assert is_interrupt_envelope(PLAIN_DICT_RESULT) is False
    assert is_interrupt_envelope({"result_type": "answer", "interrupt_ids": []}) is False
    assert is_interrupt_envelope({"result_type": "interrupt"}) is False
    assert is_interrupt_envelope(None) is False
    assert is_interrupt_envelope("result_type=interrupt") is False

    # A (tool_result, tool_message) tuple is inspected by its first element,
    # which is how ``_collect_interrupts`` feeds it.
    assert is_interrupt_envelope((INTERRUPT_ENVELOPE, None)) is True
    assert is_interrupt_envelope((PLAIN_DICT_RESULT, None)) is False


def test_interrupt_envelope_produces_no_tool_message() -> None:
    """An interrupt envelope yields ``(envelope, None)``.

    This mirrors the ``ToolInterruptException`` branch, which also appends no
    ToolMessage. The envelope itself must still reach the caller as the tool
    result so ``ToolInterruptHandler`` can collect the pending interrupt.
    """
    results = asyncio.run(_execute([_dict_tool("pauses", INTERRUPT_ENVELOPE)]))

    assert len(results) == 1
    tool_result, tool_message = results[0]
    assert tool_message is None, (
        f"interrupt envelope produced a ToolMessage: {tool_message!r}"
    )
    assert tool_result == INTERRUPT_ENVELOPE
    # The handler must still recognise it, or the interrupt is silently lost.
    assert ToolInterruptHandler.is_sub_agent_interrupt(tool_result) is True


def test_plain_dict_result_still_produces_a_tool_message() -> None:
    """Regression guard: only the envelope is exempt, not every dict."""
    results = asyncio.run(_execute([_dict_tool("answers", PLAIN_DICT_RESULT)]))

    assert len(results) == 1
    tool_result, tool_message = results[0]
    assert tool_message is not None
    assert tool_message.tool_call_id == "tc-answers"
    assert str(tool_message.content) == str(PLAIN_DICT_RESULT)
    assert tool_result == PLAIN_DICT_RESULT


def test_envelope_and_plain_dict_in_one_round() -> None:
    """A mixed round exempts only the envelope's call, by tool_call_id."""
    results = asyncio.run(
        _execute(
            [
                _dict_tool("mixed_pauses", INTERRUPT_ENVELOPE),
                _dict_tool("mixed_answers", PLAIN_DICT_RESULT),
            ]
        )
    )

    assert len(results) == 2
    assert results[0][1] is None
    assert results[1][1] is not None
    assert results[1][1].tool_call_id == "tc-mixed_answers"

    # What the ReAct loop does with these results: it appends only the
    # non-None messages, so the envelope leaves no trace in the context.
    appended = [msg for _, msg in results if msg is not None]
    assert len(appended) == 1
    assert "interrupt" not in str(appended[0].content)


def test_agent_ability_envelope_produces_no_tool_message() -> None:
    """An agent registered directly as an ability follows the same contract."""
    child_card = AgentCard(id="child_agent", name="child_agent", description="Child agent")
    child_agent = MagicMock()
    child_agent.card = child_card
    am = AbilityManager()
    am.add(child_card)
    session = create_agent_session(
        session_id="parent-session", card=AgentCard(id="parent_agent", name="parent_agent"),
    )

    async def _run() -> Tuple[Any, ToolMessage]:
        with patch.object(Runner.resource_mgr, "get_agent", new=AsyncMock(return_value=child_agent)), \
                patch.object(Runner, "run_agent", new=AsyncMock(return_value=dict(INTERRUPT_ENVELOPE))):
            return await am._execute_single_tool_call(  # pylint: disable=protected-access
                _tool_call("child_agent"), session,
            )

    tool_result, tool_message = asyncio.run(_run())

    assert tool_message is None
    assert tool_result == INTERRUPT_ENVELOPE
