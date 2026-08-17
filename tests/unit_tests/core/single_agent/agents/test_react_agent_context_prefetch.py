# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ReActAgent._consume_context_prefetch.

Verifies that untrusted recalled context injected via ctx.extra["context_prefetch"]
(e.g. daily_memory) is placed BEFORE the last user message (the current query),
keeping the query last per the RAG query-last best practice, and that existing
message objects are never mutated.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


def _ctx(messages, extra=None):
    return SimpleNamespace(
        extra=dict(extra or {}),
        inputs=SimpleNamespace(messages=messages),
    )


def test_inserts_before_last_user_message():
    msgs = [
        UserMessage(content="old"),
        AssistantMessage(content="a"),
        UserMessage(content="current query"),
    ]
    ctx = _ctx(msgs, extra={"context_prefetch": [{"content": "DAILY", "source": "daily_memory"}]})

    ReActAgent._consume_context_prefetch(ctx)

    assert len(msgs) == 4
    # fence inserted right before the last user (query), query stays last
    assert msgs[2].role == "user"
    assert msgs[2].content == "DAILY"
    assert msgs[3].content == "current query"
    # channel is consumed (popped)
    assert "context_prefetch" not in ctx.extra


def test_does_not_mutate_existing_query_object():
    query = UserMessage(content="current query")
    msgs = [AssistantMessage(content="a"), query]
    ctx = _ctx(msgs, extra={"context_prefetch": [{"content": "FENCE"}]})

    ReActAgent._consume_context_prefetch(ctx)

    # the original query object is untouched and still the last message
    assert msgs[-1] is query
    assert query.content == "current query"
    assert msgs[-2].role == "user"
    assert msgs[-2].content == "FENCE"


def test_no_user_message_falls_back_to_append():
    msgs = [AssistantMessage(content="a")]
    ctx = _ctx(msgs, extra={"context_prefetch": [{"content": "D"}]})

    ReActAgent._consume_context_prefetch(ctx)

    assert len(msgs) == 2
    assert msgs[-1].role == "user"
    assert msgs[-1].content == "D"


def test_missing_or_empty_is_noop():
    msgs = [UserMessage(content="q")]

    ReActAgent._consume_context_prefetch(_ctx(msgs))  # no key at all
    assert len(msgs) == 1

    ReActAgent._consume_context_prefetch(
        _ctx(msgs, extra={"context_prefetch": []})
    )  # empty list
    assert len(msgs) == 1

    ReActAgent._consume_context_prefetch(
        _ctx(msgs, extra={"context_prefetch": [{"content": "   "}]})  # blank content
    )
    assert len(msgs) == 1


def test_multiple_entries_are_joined_with_blank_line():
    msgs = [UserMessage(content="q")]
    ctx = _ctx(
        msgs,
        extra={"context_prefetch": [{"content": "A"}, {"content": "B"}, {"content": ""}]},
    )

    ReActAgent._consume_context_prefetch(ctx)

    # empty-content entry skipped; A and B joined
    assert msgs[0].role == "user"
    assert msgs[0].content == "A\n\nB"
    assert msgs[1].content == "q"
