# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Message caps must discard complete tool-call groups, including in saved state."""

from unittest.mock import patch

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.context.message_buffer import ContextMessageBuffer
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage


def _tool_group(*ids):
    return [
        AssistantMessage(tool_calls=[ToolCall(id=key, type="function", name="search", arguments="{}") for key in ids]),
        *(ToolMessage(tool_call_id=key, content=f"result {key}") for key in ids),
    ]


def _history():
    return [
        UserMessage(content="question"),
        *_tool_group("first"),
        *_tool_group("second", "third"),
        AssistantMessage(content="answer"),
        UserMessage(content="continue"),
    ]


@pytest.mark.parametrize("limit,expected_start", [(1, 7), (2, 6), (3, 6), (4, 6), (5, 3), (6, 3), (7, 1), (8, 0)])
def test_rebuild_keeps_complete_groups_within_hard_limit(limit, expected_start):
    history = _history()
    buffer = ContextMessageBuffer(history, limit)
    assert buffer.get_back() == history[expected_start:]
    assert buffer.size() == len(history[expected_start:]) <= limit
    assert buffer.get_back(with_history=False) == []
    assert len(history) == 8


@pytest.mark.parametrize("batch", [False, True])
def test_append_across_tool_group_boundary(batch):
    history = _history()
    buffer = ContextMessageBuffer([], 4)
    if batch:
        buffer.add_back(history)
    else:
        for message in history:
            buffer.add_back(message)
    assert buffer.get_back() == history[-2:]
    assert buffer.size() == 2
    assert buffer.get_back(with_history=False) == history[-2:]


def test_cap_exactly_fits_tool_group():
    group = _tool_group("one", "two")
    buffer = ContextMessageBuffer([UserMessage(content="old"), *group], 3)
    assert buffer.get_back() == group


def test_group_larger_than_cap_is_discarded_whole():
    buffer = ContextMessageBuffer([], 2)
    for message in _tool_group("one", "two", "three"):
        buffer.add_back(message)
    assert buffer.get_back() == []
    assert buffer.size() == 0
    final = AssistantMessage(content="done")
    buffer.add_back(final)
    assert buffer.get_back() == [final]


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_streaming_oversized_group_stays_discarded_after_physical_resize(limit):
    group = _tool_group(*(str(index) for index in range(limit * 3 + 2)))
    incremental = ContextMessageBuffer([], limit)
    for index, message in enumerate(group):
        incremental.add_back(message)
        if index + 1 > limit:
            assert incremental.get_back() == []
            assert incremental.size() == 0
    batched = ContextMessageBuffer([], limit)
    batched.add_back(group)
    assert incremental.get_back() == batched.get_back() == []

    final = AssistantMessage(content="done")
    incremental.add_back(final)
    assert incremental.get_back() == [final]


def test_rebuild_of_oversized_group_drops_later_results():
    group = _tool_group("one", "two", "three")
    buffer = ContextMessageBuffer(group[:-1], 1)
    assert buffer.get_back() == []
    buffer.add_back(group[-1])
    assert buffer.get_back() == []


@pytest.mark.parametrize("replacement", ["rebuild", "set"])
def test_explicit_replacement_removes_orphan_results_with_a_cap(replacement):
    buffer = ContextMessageBuffer([], 1)
    buffer.add_back(_tool_group("one", "two"))
    assert buffer.get_back() == []
    # A bounded checkpoint can contain partial history produced by the old
    # implementation. Match the context window's leading-tool normalization.
    partial_history = [ToolMessage(tool_call_id="external", content="provided")]
    if replacement == "rebuild":
        buffer.rebulid(partial_history)
    else:
        buffer.set_messages(partial_history)
    assert buffer.get_back() == []


@pytest.mark.parametrize("limit,expected_size", [(None, 1), (1, 0), (2, 0)])
def test_partial_history_is_normalized_only_with_a_cap(limit, expected_size):
    partial_history = [ToolMessage(tool_call_id="external", content="provided")]
    buffer = ContextMessageBuffer(partial_history, limit)
    assert len(buffer.get_back()) == expected_size


def test_physical_resize_does_not_leave_orphan_tool_results():
    history = [UserMessage(content="old") for _ in range(3)]
    history.extend(_tool_group("one", "two"))
    history.extend([AssistantMessage(content="done"), UserMessage(content="next"), AssistantMessage(content="end")])
    buffer = ContextMessageBuffer([], 4)
    buffer.add_back(history)
    assert buffer.get_back() == history[-3:]
    assert not isinstance(buffer._context_messages[0], ToolMessage)


def test_new_messages_remain_visible_after_history_is_trimmed():
    history = [UserMessage(content="old"), *_tool_group("one", "two")]
    buffer = ContextMessageBuffer(history, 4)
    added = [AssistantMessage(content="done"), UserMessage(content="next")]
    buffer.add_back(added)
    assert buffer.get_back(with_history=False) == added
    assert buffer.get_back(size=1, with_history=False) == added[-1:]


def test_history_boundary_after_physical_resize():
    history = [UserMessage(content="old") for _ in range(4)]
    buffer = ContextMessageBuffer(history, 4)
    added = [*_tool_group("one", "two"), AssistantMessage(content="done"), UserMessage(content="next")]
    buffer.add_back(added)
    assert buffer.get_back(with_history=False) == added[-2:]


def test_state_rebuild_retains_the_same_complete_groups():
    buffer = ContextMessageBuffer([], 4)
    buffer.add_back(_history())
    saved_messages = buffer.get_back()
    restored = ContextMessageBuffer(saved_messages, 4)
    assert restored.get_back() == _history()[-2:]
    assert restored.get_back(with_history=False) == []


@pytest.mark.parametrize("with_history", [False, True])
def test_set_messages_respects_group_boundary(with_history):
    buffer = ContextMessageBuffer([UserMessage(content="old")], 4)
    buffer.set_messages(_history(), with_history=with_history)
    assert buffer.get_back() == _history()[-2:]
    assert buffer.get_back(with_history=False) == _history()[-2:]


def test_explicit_single_message_get_and_pop_keep_their_semantics():
    group = _tool_group("one", "two")
    buffer = ContextMessageBuffer(group, 4)
    assert buffer.get_back(size=1) == group[-1:]
    assert buffer.pop_back(size=1) == group[-1:]
    assert buffer.get_back() == group[:-1]


def test_unlimited_buffer_is_unchanged():
    history = _history()
    buffer = ContextMessageBuffer(history)
    added = UserMessage(content="new")
    buffer.add_back(added)
    assert buffer.get_back() == [*history, added]
    assert buffer.get_back(with_history=False) == [added]
    assert buffer.size() == 9


def test_discard_log_contains_counts_but_no_message_contents():
    with patch("openjiuwen.core.context_engine.context.message_buffer.logger") as logger:
        buffer = ContextMessageBuffer(_history(), 4)
        logger.info.assert_called_once_with(
            "Context message limit discarded %s messages, including %s tool results",
            6,
            3,
        )
        logger.reset_mock()
        buffer.get_back()
        buffer.get_back()
        logger.info.assert_not_called()


def test_append_reports_newly_discarded_messages_once():
    history = _history()
    with patch("openjiuwen.core.context_engine.context.message_buffer.logger") as logger:
        buffer = ContextMessageBuffer(history[:4], 4)
        logger.reset_mock()
        buffer.add_back(history[4:])
        logger.info.assert_called_once_with(
            "Context message limit discarded %s messages, including %s tool results",
            6,
            3,
        )


@pytest.mark.asyncio
async def test_context_save_load_and_window_agree_after_group_trim():
    engine = ContextEngine(ContextEngineConfig(max_context_message_num=4))
    context = await engine.create_context("limited", None, history_messages=_history()[:-1])
    await context.add_messages(UserMessage(content="continue"))

    messages = context.get_messages()
    assert [message.role for message in messages] == ["assistant", "user"]
    assert context.save_state()["messages"] == messages
    assert (await context.get_context_window()).context_messages == messages

    restored = await engine.create_context("restored", None)
    restored.load_state({"restored": context.save_state()})
    assert restored.get_messages() == messages
    assert restored.get_messages(with_history=False) == []
    assert (await restored.get_context_window()).context_messages == messages


@pytest.mark.asyncio
async def test_context_retains_a_complete_group_that_fits_the_cap():
    engine = ContextEngine(ContextEngineConfig(max_context_message_num=5))
    context = await engine.create_context("limited", None, history_messages=_history())
    messages = context.get_messages()
    assert [message.role for message in messages] == ["assistant", "tool", "tool", "assistant", "user"]
    assert len(context) == 5
    assert (await context.get_context_window()).context_messages == messages


@pytest.mark.asyncio
async def test_context_streaming_oversized_group_does_not_save_orphan_results():
    engine = ContextEngine(ContextEngineConfig(max_context_message_num=1))
    context = await engine.create_context("limited", None)
    group = _tool_group("one", "two", "three", "four")
    for index, message in enumerate(group):
        await context.add_messages(message)
        if index:
            assert context.get_messages() == []
            assert context.save_state()["messages"] == []
    final = AssistantMessage(content="done")
    await context.add_messages(final)
    assert context.get_messages() == [final]
    assert (await context.get_context_window()).context_messages == [final]


@pytest.mark.asyncio
async def test_context_restored_mid_group_keeps_later_results_discarded():
    engine = ContextEngine(ContextEngineConfig(max_context_message_num=1))
    context = await engine.create_context("limited", None)
    group = _tool_group("one", "two", "three")
    for message in group[:-1]:
        await context.add_messages(message)
    saved = context.save_state()
    assert saved["messages"] == []

    restored = await engine.create_context("restored", None)
    restored.load_state({"restored": saved})
    await restored.add_messages(group[-1])
    assert restored.get_messages() == []
    assert restored.save_state()["messages"] == []
    assert (await restored.get_context_window()).context_messages == []


@pytest.mark.asyncio
async def test_context_loads_legacy_orphan_results_consistently_with_window():
    engine = ContextEngine(ContextEngineConfig(max_context_message_num=4))
    context = await engine.create_context("limited", None)
    legacy = [ToolMessage(tool_call_id="missing", content="orphan"), AssistantMessage(content="done")]
    context.load_state({"limited": {"messages": legacy}})
    assert context.get_messages() == legacy[-1:]
    assert context.save_state()["messages"] == legacy[-1:]
    assert (await context.get_context_window()).context_messages == legacy[-1:]
