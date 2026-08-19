# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.rails.model_anomaly_detection_rail import (
    ModelAnomalyDetectionRail,
    ToolLoopCompactConfig,
)

# Keep Chinese literals as unicode escapes so Windows tooling cannot corrupt them.
_CN_BREAKOUT = "\u8bf7\u8df3\u51fa\u91cd\u590d\u5de5\u5177\u8c03\u7528"
_CN_TOOL_CALLS_HEADING = (
    "\u6700\u540e\u4e00\u8f6e\u5de5\u5177\u8c03\u7528\u547d\u4ee4\u5982\u4e0b\uff1a"
)
_CN_TOOL_RESULTS_HEADING = (
    "\u6700\u540e\u4e00\u8f6e\u5de5\u5177\u6267\u884c\u7ed3\u679c\u5982\u4e0b\uff1a"
)
_CN_SAME_ARGS_HINT = (
    "\u68c0\u6d4b\u5230\u8fde\u7eed\u591a\u8f6e\u8c03\u7528\u4e86\u76f8\u540c\u7684"
    "\u5de5\u5177\u96c6\uff0c\u4e14\u6bcf\u4e2a\u5de5\u5177\u7684\u5165\u53c2\u4e5f\u76f8\u540c\u3002"
)
_CN_SEED = "\u8bf7\u751f\u6210\u7814\u7a76\u60f3\u6cd5"
_CN_CREATE_TODO = "\u521b\u5efa\u4efb\u52a1\u6e05\u5355"
_CN_LIT_REVIEW = "\u6587\u732e\u7efc\u8ff0"
_CN_ACTIVE = "\u6b63\u5728\u8fdb\u884c\u6587\u732e\u7efc\u8ff0"
_CN_DESC = "\u5206\u6790Introduction"


class _FakeSession:
    def __init__(self, session_id: str = "sess-1") -> None:
        self._session_id = session_id
        self._state: dict = {}

    def get_session_id(self):
        return self._session_id

    def get_state(self, key=None):
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict) -> None:
        self._state.update(data)


class _FakeContext:
    def __init__(self, messages=None) -> None:
        self._messages = list(messages or [])

    def get_messages(self):
        return list(self._messages)

    def set_messages(self, messages, with_history: bool = True):
        self._messages = list(messages)


def _tool_call(tool_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=tool_id, name=name, type="function", arguments=arguments)


def _todo_round(index: int, *, arguments: str | None = None, result: str | None = None) -> list:
    args = arguments or (
        '{"tasks":[{"id":"literature_review","content":"'
        + _CN_LIT_REVIEW
        + '","activeForm":"'
        + _CN_ACTIVE
        + '","description":"'
        + _CN_DESC
        + '"}]}'
    )
    tool_id = f"tc-{index}"
    return [
        AssistantMessage(
            content=_CN_CREATE_TODO,
            reasoning_content=f"reasoning-{index}",
            tool_calls=[_tool_call(tool_id, "todo_create", args)],
        ),
        ToolMessage(
            content=result if result is not None else "Successfully created task(s)",
            tool_call_id=tool_id,
        ),
    ]


def _loop_summary_messages(messages) -> list[UserMessage]:
    return [
        msg for msg in messages
        if isinstance(msg, UserMessage)
        and _CN_BREAKOUT in str(msg.content)
    ]


def _extract_json_block(content: str, heading: str) -> list:
    marker = f"{heading}\n---\n"
    idx = content.find(marker)
    assert idx >= 0, f"missing JSON block under heading: {heading}"
    start = idx + len(marker)
    obj, _ = json.JSONDecoder().raw_decode(content, start)
    assert isinstance(obj, list)
    return obj


def _make_agent(*, language: str = "cn"):
    agent = Mock()
    agent.system_prompt_builder = Mock(language=language)
    return agent


def _make_ctx(messages, *, session=None, rail: ModelAnomalyDetectionRail | None = None):
    rail = rail or ModelAnomalyDetectionRail(
        tool_loop_compact=ToolLoopCompactConfig(enabled=True, consecutive_threshold=3),
    )
    return AgentCallbackContext(
        agent=_make_agent(language="cn"),
        inputs=ModelCallInputs(messages=[]),
        session=session or _FakeSession(),
        context=_FakeContext(messages),
    ), rail


@pytest.mark.asyncio
async def test_compacts_identical_tool_args():
    history = [UserMessage(content=_CN_SEED)]
    for index in range(1, 4):
        history.extend(_todo_round(index))

    ctx, rail = _make_ctx(history)
    await rail.before_model_call(ctx)

    messages = ctx.context.get_messages()
    summaries = _loop_summary_messages(messages)
    assert len(summaries) == 1
    content = summaries[0].content
    assert _CN_SAME_ARGS_HINT in content
    tool_calls = _extract_json_block(content, _CN_TOOL_CALLS_HEADING)
    tool_results = _extract_json_block(content, _CN_TOOL_RESULTS_HEADING)
    assert tool_calls[0]["name"] == "todo_create"
    assert tool_results[0]["content"] == "Successfully created task(s)"
    assert rail._get_tool_loop_compact_count(ctx) == 1


@pytest.mark.asyncio
async def test_order_independent_within_round():
    args_a = '{"path":"a.txt"}'
    args_b = '{"path":"b.txt"}'
    round_msgs = [
        AssistantMessage(
            content="read both",
            tool_calls=[
                _tool_call("id-1", "read_file", args_a),
                _tool_call("id-2", "read_file", args_b),
            ],
        ),
        ToolMessage(content="A", tool_call_id="id-1"),
        ToolMessage(content="B", tool_call_id="id-2"),
        AssistantMessage(
            content="read both again",
            tool_calls=[
                _tool_call("id-3", "read_file", args_b),
                _tool_call("id-4", "read_file", args_a),
            ],
        ),
        ToolMessage(content="B", tool_call_id="id-3"),
        ToolMessage(content="A", tool_call_id="id-4"),
        AssistantMessage(
            content="read both third",
            tool_calls=[
                _tool_call("id-5", "read_file", args_a),
                _tool_call("id-6", "read_file", args_b),
            ],
        ),
        ToolMessage(content="A", tool_call_id="id-5"),
        ToolMessage(content="B", tool_call_id="id-6"),
    ]
    ctx, rail = _make_ctx(
        [UserMessage(content="go")] + round_msgs,
        rail=ModelAnomalyDetectionRail(
            tool_loop_compact=ToolLoopCompactConfig(enabled=True, consecutive_threshold=3),
        ),
    )
    await rail.before_model_call(ctx)
    assert len(_loop_summary_messages(ctx.context.get_messages())) == 1


@pytest.mark.asyncio
async def test_same_args_different_results_still_compact():
    """Matching ignores return values; warning still shows the latest result."""
    history = [UserMessage(content="go")]
    for index in range(1, 4):
        history.extend(_todo_round(index, result=f"result-{index}"))

    ctx, rail = _make_ctx(history)
    await rail.before_model_call(ctx)

    summaries = _loop_summary_messages(ctx.context.get_messages())
    assert len(summaries) == 1
    content = summaries[0].content
    tool_results = _extract_json_block(content, _CN_TOOL_RESULTS_HEADING)
    assert tool_results[0]["content"] == "result-3"
    assert rail._get_tool_loop_compact_count(ctx) == 1


@pytest.mark.asyncio
async def test_different_args_do_not_compact():
    history = [UserMessage(content="go")]
    for index in range(1, 4):
        history.extend(_todo_round(index, arguments=f'{{"n":{index}}}'))

    ctx, rail = _make_ctx(history)
    await rail.before_model_call(ctx)
    assert _loop_summary_messages(ctx.context.get_messages()) == []


@pytest.mark.asyncio
async def test_disabled_by_default():
    history = [UserMessage(content="go")]
    for index in range(1, 4):
        history.extend(_todo_round(index))

    ctx, rail = _make_ctx(
        history,
        rail=ModelAnomalyDetectionRail(),
    )
    await rail.before_model_call(ctx)
    assert _loop_summary_messages(ctx.context.get_messages()) == []


@pytest.mark.asyncio
async def test_bailout_on_third_trigger():
    rail = ModelAnomalyDetectionRail(
        tool_loop_compact=ToolLoopCompactConfig(
            enabled=True,
            consecutive_threshold=2,
            bailout_threshold=3,
        ),
    )
    session = _FakeSession()
    agent = _make_agent(language="cn")

    for hit in range(1, 3):
        history = [UserMessage(content="go")]
        history.extend(_todo_round(1, result="same"))
        history.extend(_todo_round(2, result="same"))
        ctx = AgentCallbackContext(
            agent=agent,
            inputs=ModelCallInputs(messages=[]),
            session=session,
            context=_FakeContext(history),
        )
        await rail.before_model_call(ctx)
        assert rail._get_tool_loop_compact_count(ctx) == hit
        assert len(_loop_summary_messages(ctx.context.get_messages())) == 1

    history = [UserMessage(content="go")]
    history.extend(_todo_round(1, result="same"))
    history.extend(_todo_round(2, result="same"))
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=session,
        context=_FakeContext(history),
    )
    with pytest.raises(AbortError) as exc_info:
        await rail.before_model_call(ctx)
    assert "repeated tool calls" in str(exc_info.value.cause)
    assert rail._get_tool_loop_compact_count(ctx) == 0


@pytest.mark.asyncio
async def test_before_and_after_invoke_reset_counter():
    rail = ModelAnomalyDetectionRail(
        tool_loop_compact=ToolLoopCompactConfig(enabled=True),
    )
    ctx = AgentCallbackContext(
        agent=_make_agent(),
        inputs=ModelCallInputs(messages=[]),
        session=_FakeSession(),
        context=_FakeContext([]),
    )
    rail._set_tool_loop_compact_count(ctx, 2)
    await rail.before_invoke(ctx)
    assert rail._get_tool_loop_compact_count(ctx) == 0

    rail._set_tool_loop_compact_count(ctx, 3)
    await rail.after_invoke(ctx)
    assert rail._get_tool_loop_compact_count(ctx) == 0


@pytest.mark.asyncio
async def test_counters_isolated_across_sessions():
    rail = ModelAnomalyDetectionRail(
        tool_loop_compact=ToolLoopCompactConfig(
            enabled=True,
            consecutive_threshold=2,
            bailout_threshold=0,
        ),
    )
    agent = _make_agent(language="cn")

    history = [UserMessage(content="go")]
    history.extend(_todo_round(1, result="same"))
    history.extend(_todo_round(2, result="same"))

    ctx_a = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=_FakeSession("sess-a"),
        context=_FakeContext(history),
    )
    ctx_b = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(messages=[]),
        session=_FakeSession("sess-b"),
        context=_FakeContext(list(history)),
    )

    await rail.before_model_call(ctx_a)
    await rail.before_model_call(ctx_b)

    assert rail._get_tool_loop_compact_count(ctx_a) == 1
    assert rail._get_tool_loop_compact_count(ctx_b) == 1


@pytest.mark.asyncio
async def test_bailout_disabled_keeps_compacting():
    rail = ModelAnomalyDetectionRail(
        tool_loop_compact=ToolLoopCompactConfig(
            enabled=True,
            consecutive_threshold=2,
            bailout_threshold=0,
        ),
    )
    session = _FakeSession()
    agent = _make_agent(language="cn")

    for expected in range(1, 4):
        history = [UserMessage(content="go")]
        history.extend(_todo_round(1, result="same"))
        history.extend(_todo_round(2, result="same"))
        ctx = AgentCallbackContext(
            agent=agent,
            inputs=ModelCallInputs(messages=[]),
            session=session,
            context=_FakeContext(history),
        )
        await rail.before_model_call(ctx)
        assert rail._get_tool_loop_compact_count(ctx) == expected
