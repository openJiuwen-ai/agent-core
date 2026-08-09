# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.context_engine.schema.config import (
    CompressionRecallConfig,
    ContextEngineConfig,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness import Workspace
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.llm_stability_rail import (
    EXECUTE,
    FORCE_SKIP_ALL_KEY,
    RETRIES_KEY,
    SKIP_INVALID,
    SKIP_KEY,
    SKIP_TRUNCATED,
    LLMStabilityRail,
    classify_tool_call,
    ensure_json_arguments,
    sanitize_tool_pairing,
)


def _make_sys_operation(tmp_path: Path):
    card = SysOperationCard(
        id=f"test_stability_rail_sysop_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(card)
    return Runner.resource_mgr.get_sys_operation(card.id)


def _make_agent(tmp_path: Path):
    model = init_model(
        provider="OpenAI",
        model_name="dummy-model",
        api_key="dummy-key",
        api_base="https://example.com/v1",
        verify_ssl=False,
    )
    sys_operation = _make_sys_operation(tmp_path)
    workspace = Workspace(root_path=str(tmp_path))
    agent = create_deep_agent(
        model=model,
        card=AgentCard(name="test", description="test"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=workspace,
        sys_operation=sys_operation,
        context_engine_config=ContextEngineConfig(
            compression_recall_config=CompressionRecallConfig(),
        ),
    )
    return agent


def _tc(tc_id: str, arguments: str = "{}", name: str = "t") -> ToolCall:
    return ToolCall(id=tc_id, type="function", name=name, arguments=arguments)


# =============================================================================
# classify_tool_call
# =============================================================================


def test_classify_length_is_truncated():
    assert classify_tool_call("{}", finish_reason="length") == SKIP_TRUNCATED


def test_classify_valid_object_executes():
    assert classify_tool_call('{"a": 1}', finish_reason="stop") == EXECUTE
    assert classify_tool_call({"a": 1}, finish_reason="stop") == EXECUTE


def test_classify_truncated():
    assert classify_tool_call('{"a": 1', finish_reason="stop") == SKIP_TRUNCATED
    assert classify_tool_call('{"unterminated": "string', finish_reason="stop") == SKIP_TRUNCATED
    assert classify_tool_call("[1, 2,", finish_reason="stop") == SKIP_TRUNCATED


def test_classify_invalid():
    assert classify_tool_call("{bad json}", finish_reason="stop") == SKIP_INVALID
    assert classify_tool_call("null", finish_reason="stop") == SKIP_INVALID
    assert classify_tool_call("", finish_reason="stop") == SKIP_INVALID
    assert classify_tool_call(None, finish_reason="stop") == SKIP_INVALID
    assert classify_tool_call("123", finish_reason="stop") == SKIP_INVALID


# =============================================================================
# ensure_json_arguments
# =============================================================================


def test_ensure_json_arguments_valid_and_invalid():
    assert ensure_json_arguments('{"a": 1}') == '{"a": 1}'
    assert ensure_json_arguments("{}") == "{}"
    assert ensure_json_arguments('{"incomplete": ') == "{}"
    assert ensure_json_arguments("{bad json}") == "{}"
    assert ensure_json_arguments(None) == "{}"
    assert ensure_json_arguments(123) == "{}"


def test_ensure_json_arguments_dict():
    assert ensure_json_arguments({}) == "{}"
    assert ensure_json_arguments({"key": "value"}) == '{"key": "value"}'


# =============================================================================
# sanitize_tool_pairing
# =============================================================================


def test_pairing_orphan_tool_gets_placeholder():
    msgs = [AssistantMessage(content="call", tool_calls=[_tc("tc1")]), UserMessage(content="u")]
    out = sanitize_tool_pairing(msgs)
    tool_msgs = [m for m in out if isinstance(m, ToolMessage)]
    assert any(m.tool_call_id == "tc1" and "[Tool execution interrupted]" in m.content for m in tool_msgs)


def test_pairing_matching_response_no_placeholder():
    msgs = [
        AssistantMessage(content="call", tool_calls=[_tc("tc1")]),
        ToolMessage(content="res", tool_call_id="tc1"),
    ]
    out = sanitize_tool_pairing(msgs)
    tool_msgs = [m for m in out if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert "[Tool execution interrupted]" not in tool_msgs[0].content


def test_pairing_unordered_tool_responses():
    msgs = [
        AssistantMessage(
            content="call",
            tool_calls=[_tc("t1"), _tc("t2")],
        ),
        ToolMessage(content="r2", tool_call_id="t2"),
        ToolMessage(content="r1", tool_call_id="t1"),
    ]
    out = sanitize_tool_pairing(msgs)
    tool_msgs = [m for m in out if isinstance(m, ToolMessage)]
    # both tool results kept, no placeholder
    assert len(tool_msgs) == 2
    assert all("[Tool execution interrupted]" not in m.content for m in tool_msgs)


def test_pairing_orphan_tool_message_dropped():
    msgs = [ToolMessage(content="res", tool_call_id="orphan"), UserMessage(content="u")]
    out = sanitize_tool_pairing(msgs)
    tool_msgs = [m for m in out if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 0


def test_pairing_empty_and_plain():
    assert sanitize_tool_pairing([]) == []
    out = sanitize_tool_pairing([SystemMessage(content="s"), UserMessage(content="u")])
    assert len(out) == 2


# =============================================================================
# LLMStabilityRail.after_model_call
# =============================================================================


@pytest.mark.asyncio
async def test_after_model_call_length_force_skips_all():
    rail = LLMStabilityRail()
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=None,
    )
    ctx.inputs.response = AssistantMessage(
        content="",
        tool_calls=[_tc("tc1"), _tc("tc2")],
        finish_reason="length",
    )
    await rail.after_model_call(ctx)
    assert ctx.extra[FORCE_SKIP_ALL_KEY] is True
    assert ctx.extra[SKIP_KEY].get("tc1") == SKIP_TRUNCATED
    assert ctx.extra[RETRIES_KEY] == 1


@pytest.mark.asyncio
async def test_after_model_call_truncated_args_marked():
    rail = LLMStabilityRail()
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=None,
    )
    ctx.inputs.response = AssistantMessage(
        content="",
        tool_calls=[_tc("tc1", arguments='{"a": 1'), _tc("tc2", arguments="{}")],
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)
    assert ctx.extra[FORCE_SKIP_ALL_KEY] is False
    assert ctx.extra[SKIP_KEY].get("tc1") == SKIP_TRUNCATED
    assert "tc2" not in ctx.extra[SKIP_KEY]
    assert ctx.extra[RETRIES_KEY] == 1


@pytest.mark.asyncio
async def test_after_model_call_clean_resets_retry_counter():
    rail = LLMStabilityRail()
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=None,
    )
    ctx.extra[RETRIES_KEY] = 3
    ctx.inputs.response = AssistantMessage(
        content="ok",
        tool_calls=[_tc("tc1", arguments="{}")],
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)
    assert ctx.extra[SKIP_KEY] == {}
    assert ctx.extra[RETRIES_KEY] == 0


@pytest.mark.asyncio
async def test_after_model_call_no_response_is_noop():
    rail = LLMStabilityRail()
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=None,
    )
    await rail.after_model_call(ctx)
    assert ctx.extra[SKIP_KEY] == {}
    assert ctx.extra[FORCE_SKIP_ALL_KEY] is False


# =============================================================================
# Mount in create_deep_agent default rails
# =============================================================================


def test_create_deep_agent_mounts_llm_stability_rail(tmp_path: Path):
    agent = _make_agent(tmp_path)
    rails = agent.configured_rails()
    assert any(isinstance(r, LLMStabilityRail) for r in rails)


# =============================================================================
# LLMStabilityRail._apply_pairing (before_invoke / before_model_call)
# =============================================================================


class _FakeContext:
    """Minimal context stand-in exposing the interface ``_apply_pairing`` uses."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.added_messages = []

    def get_messages(self):
        return self._messages

    def pop_messages(self, size=None, with_history=True):
        popped = self._messages[:size]
        self._messages = self._messages[size:]
        return popped

    async def add_messages(self, message):
        self.added_messages.append(message)
        return self.added_messages


@pytest.mark.asyncio
async def test_apply_pairing_repairs_broken_args_with_complete_pairing():
    """Regression: broken arguments with complete pairing must still be repaired.

    Previously ``_apply_pairing`` early-returned when ``_has_orphan_tools`` was
    False, skipping ``ensure_json_arguments`` and leaving broken JSON for the
    LLM API to reject (e.g. context restored from an interrupted session).
    """
    rail = LLMStabilityRail()
    assistant = AssistantMessage(
        content="call",
        tool_calls=[_tc("tc1", arguments='{"a": 1')],  # truncated JSON
    )
    tool_msg = ToolMessage(content="res", tool_call_id="tc1")  # complete pairing
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=_FakeContext([assistant, tool_msg]),
    )

    await rail.before_invoke(ctx)

    repaired = ctx.context.added_messages
    repaired_assistant = next(m for m in repaired if isinstance(m, AssistantMessage))
    assert repaired_assistant.tool_calls[0].arguments == "{}"


@pytest.mark.asyncio
async def test_apply_pairing_skips_when_context_clean():
    """Clean, pair-consistent context with valid args should not be popped."""
    rail = LLMStabilityRail()
    assistant = AssistantMessage(
        content="call",
        tool_calls=[_tc("tc1", arguments='{"a": 1}')],  # valid JSON
    )
    tool_msg = ToolMessage(content="res", tool_call_id="tc1")
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ModelCallInputs(messages=[]),
        session=None,
        context=_FakeContext([assistant, tool_msg]),
    )

    await rail.before_model_call(ctx)

    assert ctx.context.added_messages == []
