# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Unit tests for ReActAgent main-call context-overflow recovery.

**Scope**: ``_model_call_with_overflow_recovery`` / ``_recover_context_overflow``
wiring at the agent model-call entry point (compress-then-retry).

Verifies:
- First CONTEXT_OVERFLOW on the main call triggers exactly one forced
  compaction (``compress_context`` carrying the main-call credentials) and
  exactly one retry, which succeeds.
- busy/noop compaction or a compaction error does not retry; the original
  overflow error propagates.
- Non-overflow errors (HTTP 500) never trigger compaction.
- A second overflow after the retry propagates the error (invoke count == 2).
- ``context_overflow_recovery=False`` restores the legacy behaviour.
- At most one recovery per invoke, even across consecutive model calls.
- The streaming path retries only when zero chunks were emitted.

Does NOT cover: processor internals or compression model selection
(see test_auto_compression_models.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

pytestmark = pytest.mark.asyncio


class _TestableReActAgent(ReActAgent):
    async def call_model_for_test(self, *, ctx: AgentCallbackContext, context, tools=None):
        return await self._call_model(ctx=ctx, context=context, tools=tools)


def _make_agent(**config_overrides) -> _TestableReActAgent:
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="test-key",
        api_base="https://example.test/v1",
        model_name="test-model",
        verify_ssl=False,
    )
    for key, value in config_overrides.items():
        setattr(config, key, value)
    agent = _TestableReActAgent(card=AgentCard(name="test", id="test"))
    agent.configure(config)
    return agent


def _make_ctx(
    agent,
    *,
    compress_result="compressed",
    streaming=False,
    pre_messages=None,
    post_messages=None,
) -> AgentCallbackContext:
    """Build a context whose window becomes visible/observable after compaction.

    When ``post_messages`` is given, ``get_context_window`` returns the pre-
    compaction messages until ``compress_context`` completes, then returns the
    post-compaction messages. This lets the tests assert that a recovery retry
    actually re-reads the compacted window (compression happened between the
    two LLM calls and the second call used the new messages).
    """
    session = MagicMock(spec=Session)
    session.get_session_id = MagicMock(return_value="sess-1")
    ctx = AgentCallbackContext(agent=agent, session=session, inputs={}, extra={})
    if streaming:
        ctx.extra["_streaming"] = True

    pre_messages = pre_messages or []
    post_messages = post_messages or pre_messages
    compacted = {"done": False}

    async def _compress(**kwargs):
        compacted["done"] = True
        return compress_result

    async def _get_window(**kwargs):
        active = post_messages if compacted["done"] else pre_messages
        return ContextWindow(system_messages=[], context_messages=list(active), tools=[])

    model_context = MagicMock()
    model_context.get_messages = MagicMock(return_value=list(pre_messages))
    model_context.get_context_window = AsyncMock(side_effect=_get_window)
    model_context.detect_context_window_change = MagicMock(return_value=None)
    model_context.compress_context = AsyncMock(side_effect=_compress)
    ctx.context = model_context
    return ctx


def _make_llm() -> MagicMock:
    fake_llm = MagicMock()
    fake_llm.supports_kv_cache_release = MagicMock(return_value=False)
    return fake_llm


def _chunk(content: str = "ok") -> MagicMock:
    chunk = MagicMock()
    chunk.content = content
    chunk.reasoning_content = None
    chunk.tool_calls = []
    chunk.usage_metadata = None
    chunk.finish_reason = "stop"
    chunk.prompt_token_ids = None
    chunk.completion_token_ids = None
    chunk.logprobs = None
    return chunk


async def test_invoke_overflow_triggers_one_compaction_and_one_retry():
    agent = _make_agent()
    pre = [UserMessage(content="overflow history")]
    post = [UserMessage(content="compact summary")]
    ctx = _make_ctx(agent, compress_result="compressed", pre_messages=pre, post_messages=post)
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(
        side_effect=[
            RuntimeError("413 Request Entity Too Large: context length exceeded"),
            AssistantMessage(content="recovered", tool_calls=[]),
        ]
    )
    agent.set_llm(fake_llm)

    result = await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert result.content == "recovered"
    assert fake_llm.invoke.await_count == 2
    ctx.context.compress_context.assert_awaited_once()

    # The compaction must have happened between the two LLM calls and the
    # retry must have re-read the compacted window: the first call carried the
    # pre-compaction messages, the second carried the post-compaction ones.
    first_messages = fake_llm.invoke.await_args_list[0].kwargs["messages"]
    second_messages = fake_llm.invoke.await_args_list[1].kwargs["messages"]
    assert [m.content for m in first_messages] == ["overflow history"]
    assert [m.content for m in second_messages] == ["compact summary"]

    # The forced compaction must carry the main-call credentials so the
    # dedicated auto_compression model pair reuses them.
    kwargs = ctx.context.compress_context.await_args.kwargs
    assert kwargs.get("model_config") is agent._config.model_config_obj
    assert kwargs.get("model_client_config") is agent._config.model_client_config


async def test_invoke_overflow_dict_result_counts_as_compressed():
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result={"result": "compressed"})
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(
        side_effect=[
            RuntimeError("context length exceeded"),
            AssistantMessage(content="recovered", tool_calls=[]),
        ]
    )
    agent.set_llm(fake_llm)

    result = await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert result.content == "recovered"
    assert fake_llm.invoke.await_count == 2


@pytest.mark.parametrize("compress_result", ["busy", "noop"])
async def test_invoke_overflow_noop_compaction_does_not_retry(compress_result):
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result=compress_result)
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(side_effect=RuntimeError("413 context length exceeded"))
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.invoke.await_count == 1
    ctx.context.compress_context.assert_awaited_once()


async def test_invoke_overflow_compaction_error_does_not_retry():
    agent = _make_agent()
    ctx = _make_ctx(agent)
    ctx.context.compress_context = AsyncMock(side_effect=RuntimeError("lock wedged"))
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(side_effect=RuntimeError("413 context length exceeded"))
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.invoke.await_count == 1


async def test_non_overflow_error_propagates_without_compaction():
    agent = _make_agent()
    ctx = _make_ctx(agent)
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(side_effect=RuntimeError("500 internal server error"))
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="internal server error"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.invoke.await_count == 1
    ctx.context.compress_context.assert_not_awaited()


async def test_text_containing_413_substring_but_not_overflow_does_not_compact():
    # Regression: a bare "413" substring inside the error text (request id,
    # timeout, numeric field) must NOT be treated as a context overflow. The
    # loose compression-chain classifier would flag it, but the main-call
    # recovery path uses the strict classifier to avoid destructively
    # force-compacting and rewriting session history on an unrelated error.
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="compressed")
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(
        side_effect=[
            RuntimeError("request abc413 timed out after 120ms"),
            AssistantMessage(content="recovered", tool_calls=[]),
        ]
    )
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="timed out"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.invoke.await_count == 1
    ctx.context.compress_context.assert_not_awaited()


async def test_tpm_overlap_text_does_not_trigger_destructive_compaction():
    # "token limit"/"too many tokens" overlap with TPM rate-limit wording. The
    # lenient compression-chain classifier flags them, but on the destructive
    # main-call path treating them as an overflow would both fail to fix the
    # real (rate) limit and irreversibly rewrite session history. The strict
    # main-call predicate must reject them.
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="compressed")
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(
        side_effect=RuntimeError("tokens per minute (TPM) limit exceeded")
    )
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="TPM"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.invoke.await_count == 1
    ctx.context.compress_context.assert_not_awaited()


async def test_text_containing_413_substring_but_not_overflow_stream_does_not_compact():
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="compressed", streaming=True)
    fake_llm = _make_llm()
    fake_llm.stream = MagicMock(
        side_effect=[_stream_outcome(RuntimeError("gateway aborted on request 4130"))]
    )
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="gateway aborted"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.stream.call_count == 1
    ctx.context.compress_context.assert_not_awaited()


async def test_retry_still_overflow_propagates_original_error():
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="compressed")
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(
        side_effect=[
            RuntimeError("413 context length exceeded"),
            RuntimeError("413 still too long"),
        ]
    )
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="still too long"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.invoke.await_count == 2
    ctx.context.compress_context.assert_awaited_once()


async def test_recovery_disabled_restores_legacy_behaviour():
    agent = _make_agent(context_overflow_recovery=False)
    ctx = _make_ctx(agent)
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(side_effect=RuntimeError("413 context length exceeded"))
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.invoke.await_count == 1
    ctx.context.compress_context.assert_not_awaited()


async def test_recovery_attempted_at_most_once_per_invoke():
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="noop")
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(side_effect=RuntimeError("413 context length exceeded"))
    agent.set_llm(fake_llm)

    # Two consecutive model calls (ReAct iterations) share one ctx.extra.
    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)
    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    # The second call must not burn another forced compaction.
    ctx.context.compress_context.assert_awaited_once()


async def test_recovery_budget_resets_for_new_invoke():
    # Distinguishes "suppression scoped to ctx.extra per invoke" (a fresh ctx
    # gets a fresh recovery budget) from a buggy instance/global-level guard
    # that would keep suppressing recovery across unrelated invokes.
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="noop")
    fake_llm = _make_llm()
    fake_llm.invoke = AsyncMock(side_effect=RuntimeError("413 context length exceeded"))
    agent.set_llm(fake_llm)

    # First invoke: burns its one recovery attempt (noop -> no retry).
    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    # Second, brand-new invoke (fresh ctx.extra): a new recovery is allowed.
    ctx2 = _make_ctx(agent, compress_result="noop")
    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx2, context=ctx2.context)

    # Two distinct invokes -> two forced compactions.
    assert ctx.context.compress_context.await_count == 1
    assert ctx2.context.compress_context.await_count == 1


def _stream_outcome(*events):
    """Build a one-shot async generator that yields chunks and/or raises.

    ``MagicMock(side_effect=[...])`` returns list elements as-is, so the list
    must hold generator *objects*, not generator functions.
    """
    async def stream(**kwargs):
        for event in events:
            if isinstance(event, BaseException):
                raise event
            yield event

    return stream()


async def test_stream_overflow_retries_once_with_compacted_window():
    agent = _make_agent()
    pre = [UserMessage(content="overflow history")]
    post = [UserMessage(content="compact summary")]
    ctx = _make_ctx(
        agent,
        compress_result="compressed",
        streaming=True,
        pre_messages=pre,
        post_messages=post,
    )
    fake_llm = _make_llm()
    fake_llm.stream = MagicMock(
        side_effect=[
            _stream_outcome(RuntimeError("413 context length exceeded")),
            _stream_outcome(_chunk("recovered")),
        ]
    )
    agent.set_llm(fake_llm)

    result = await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert result.content == "recovered"
    assert fake_llm.stream.call_count == 2
    ctx.context.compress_context.assert_awaited_once()

    # Compression must have happened between the two stream calls and the
    # retry must re-read the compacted window (second call used the new
    # messages, not the overflowing ones).
    first_messages = fake_llm.stream.call_args_list[0].kwargs["messages"]
    second_messages = fake_llm.stream.call_args_list[1].kwargs["messages"]
    assert [m.content for m in first_messages] == ["overflow history"]
    assert [m.content for m in second_messages] == ["compact summary"]


async def test_stream_overflow_after_first_chunk_does_not_retry():
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="compressed", streaming=True)
    fake_llm = _make_llm()
    fake_llm.stream = MagicMock(
        side_effect=[
            _stream_outcome(_chunk("partial "), RuntimeError("413 context length exceeded")),
        ]
    )
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="context length"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.stream.call_count == 1
    ctx.context.compress_context.assert_not_awaited()


async def test_stream_non_overflow_error_does_not_retry():
    agent = _make_agent()
    ctx = _make_ctx(agent, compress_result="compressed", streaming=True)
    fake_llm = _make_llm()
    fake_llm.stream = MagicMock(
        side_effect=[_stream_outcome(RuntimeError("429 too many requests"))]
    )
    agent.set_llm(fake_llm)

    with pytest.raises(RuntimeError, match="too many requests"):
        await agent.call_model_for_test(ctx=ctx, context=ctx.context)

    assert fake_llm.stream.call_count == 1
    ctx.context.compress_context.assert_not_awaited()
