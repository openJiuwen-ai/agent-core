# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""retain_tool_facts_on_abort: an aborted turn keeps the tool calls issued and the results obtained.

The default (off) keeps the existing cleanup; test_react_agent_cancel_context.py covers it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

MARKER = "[Request cancelled by user]"


def _call(call_id):
    return ToolCall(id=call_id, type="function", name="upload", arguments="{}")


def _agent(retain):
    agent = ReActAgent(card=AgentCard(name="retain_agent", description="tool fact retention"))
    config = ReActAgentConfig().configure_model("gpt-4")
    config.retain_tool_facts_on_abort = retain
    agent.configure(config)
    return agent


def _session(session_id="session-1"):
    session = MagicMock()
    session.get_session_id.return_value = session_id
    return session


def test_switch_is_off_by_default():
    assert ReActAgentConfig().retain_tool_facts_on_abort is False


def test_retain_keeps_the_group_and_adds_results_from_the_journal_in_call_order():
    group = AssistantMessage(content="", tool_calls=[_call("a"), _call("b"), _call("c")])
    in_context = ToolMessage(content="a done", tool_call_id="a")
    journaled = ToolMessage(content="c done", tool_call_id="c")
    stray = ToolMessage(content="other call", tool_call_id="z")
    messages = [UserMessage(content="upload both"), group, in_context, stray]
    journal = {"c": journaled, "x": ToolMessage(content="?", tool_call_id="x")}
    kept = ReActAgent._retain_cancelled_turn_messages(messages, journal)
    assert kept == [messages[0], group, in_context, journaled], "b stays without a result; the stray result is dropped"


def test_retain_keeps_an_incomplete_group_that_cleanup_would_drop():
    group = AssistantMessage(content="", tool_calls=[_call("a")])
    messages = [UserMessage(content="upload"), group]
    assert ReActAgent._retain_cancelled_turn_messages(messages, {}) == messages
    dropped = ReActAgent._sanitize_cancelled_turn_messages(messages)
    assert [type(message) for message in dropped] == [UserMessage, AssistantMessage] and not dropped[1].tool_calls


def test_retain_drops_orphan_results_and_marks_a_bare_user_turn():
    messages = [UserMessage(content="hello"), ToolMessage(content="orphan", tool_call_id="z")]
    kept = ReActAgent._retain_cancelled_turn_messages(messages, {}, marker=MARKER)
    assert kept == [messages[0], AssistantMessage(content=MARKER)]


def _context(messages):
    context = MagicMock()
    context.get_messages = MagicMock(return_value=messages)
    context.set_messages = MagicMock()
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("retain", [False, True])
async def test_abort_cleanup_uses_the_switch_and_always_drops_the_journal(retain):
    agent = _agent(retain)
    group = AssistantMessage(content="", tool_calls=[_call("a"), _call("b")])
    context = _context([UserMessage(content="upload"), group])
    agent.context_engine = MagicMock(get_context=MagicMock(return_value=context))
    agent._tool_fact_journals["session-1"] = {"a": ToolMessage(content="a done", tool_call_id="a")}
    await agent._cleanup_context_after_abort(_session(), marker=MARKER)
    kept = context.set_messages.call_args.args[0]
    if retain:
        assert kept == [UserMessage(content="upload"), group, ToolMessage(content="a done", tool_call_id="a")]
    else:
        assert all(not getattr(message, "tool_calls", None) for message in kept), "personal cleanup is unchanged"
    assert "session-1" not in agent._tool_fact_journals


@pytest.mark.asyncio
async def test_journal_is_kept_until_the_whole_batch_is_written():
    """A write interrupted midway leaves the journal for the abort cleanup."""
    agent = _agent(retain=True)
    calls = [_call("a"), _call("b")]
    messages = [ToolMessage(content="a done", tool_call_id="a"), ToolMessage(content="b done", tool_call_id="b")]

    async def execute(**kwargs):
        for call, message in zip(calls, messages):
            kwargs["on_tool_result"](call, None)
            kwargs["on_tool_result"](call, ToolMessage(content="note", tool_call_id=call.id))
            kwargs["on_tool_result"](call, message)
        kwargs["on_tool_result"](_call("retry"), ToolMessage(content="note", tool_call_id="retry"))
        kwargs["on_tool_result"](_call("retry"), None)
        return [(None, message) for message in messages]

    agent._ability_manager.execute = execute
    context = MagicMock()
    context.add_messages = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await agent._execute_tool_call(MagicMock(), calls, _session(), context)
    assert agent._tool_fact_journals["session-1"] == {"a": messages[0], "b": messages[1]}, "None drops an entry"

    context.add_messages = AsyncMock()
    await agent._execute_tool_call(MagicMock(), calls, _session(), context)
    assert "session-1" not in agent._tool_fact_journals, "cleared once every result is in the context"


@pytest.mark.asyncio
async def test_no_callback_is_passed_when_the_switch_is_off():
    agent = _agent(retain=False)
    seen = {}

    async def execute(**kwargs):
        seen.update(kwargs)
        return []

    agent._ability_manager.execute = execute
    await agent._execute_tool_call(MagicMock(), [_call("a")], _session(), MagicMock(add_messages=AsyncMock()))
    assert "on_tool_result" not in seen, "the call shape is unchanged for wrappers of execute"
    assert agent._tool_fact_journals == {}
