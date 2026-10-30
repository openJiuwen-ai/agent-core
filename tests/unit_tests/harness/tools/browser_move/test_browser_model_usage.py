# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model boundary accounting and task-end logs; no browser or provider traffic."""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.harness.tools.browser_move.decision.jev_client import DecisionUnavailable
from openjiuwen.harness.tools.browser_move.playwright_runtime import model_usage
from openjiuwen.harness.tools.browser_move.playwright_runtime.status_logging import BrowserSubagentStatusLogger
from tests.unit_tests.harness.tools.browser_move.test_browser_jev_policy import (
    TOOLS, answer, messages_for, setup_policy,
)


class MemorySession:
    def __init__(self, phase=None, session_id="usage-test"):
        self.state = {"__browser_phase_budget_state__": phase or {}}
        self.session_id = session_id

    def get_state(self, key):
        return self.state.get(key)

    def update_state(self, values):
        self.state.update(values)

    def get_session_id(self):
        return self.session_id


def usage(input_tokens=10, output_tokens=3):
    return UsageMetadata(input_tokens=input_tokens, output_tokens=output_tokens,
                         total_tokens=input_tokens + output_tokens)


def context_for(session):
    return SimpleNamespace(session=session, extra={}, agent=object(),
                           inputs=SimpleNamespace(query="test", conversation_id="test"))


def collect(session):
    return model_usage.summarize_model_usage(model_usage.load_model_usage(session))


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_real_session_completed_call_is_removed_and_counted_once(status):
    from openjiuwen.core.session.agent import Session

    session = Session(session_id="usage-real-session")
    first = model_usage.start_model_call(session, "llm")
    pending = model_usage.start_model_call(session, "jev")
    model_usage.finish_model_call(session, first, usage(), 12, status=status)
    model_usage.finish_model_call(session, first, usage(), 12, status=status)

    summary = collect(session)
    assert summary["llm"]["calls"] == 1
    assert summary["llm"]["total_tokens"] == 13
    assert summary["llm"]["pending_calls"] == 0
    assert summary["jev"]["calls"] == summary["jev"]["pending_calls"] == 1
    model_usage.finish_model_call(session, pending, usage(20, 2), 5)
    assert collect(session)["total"]["pending_calls"] == 0


def test_real_session_new_invocation_drops_previous_active_calls():
    from openjiuwen.core.session.agent import Session

    session = Session(session_id="usage-real-session-reset")
    previous = model_usage.start_model_call(session, "jev")
    model_usage.store_model_usage(session, model_usage.new_model_usage())
    current = model_usage.start_model_call(session, "llm")
    model_usage.finish_model_call(session, previous, usage(20, 2), 5)
    assert collect(session)["jev"]["calls"] == 0
    model_usage.finish_model_call(session, current, usage(), 12)
    assert collect(session)["total"]["calls"] == 1
    assert collect(session)["total"]["pending_calls"] == 0


async def prepared(mode="hybrid"):
    policy, llm, client, runtime, context, captured = setup_policy(mode)
    phase = context.get_session_ref().get_state("__browser_phase_budget_state__")
    session = MemorySession(phase)
    context.get_session_ref = lambda: session
    logger = BrowserSubagentStatusLogger(logger=logging.getLogger("browser-usage-test"))
    ctx = context_for(session)
    logger.before_invoke(ctx)
    messages = await messages_for(policy, context, captured)
    llm.invoke.return_value = AssistantMessage(content="done", usage_metadata=usage())
    ctx.inputs = SimpleNamespace(messages=messages, tools=TOOLS)
    logger.before_model_call(ctx)
    return policy, llm, client, messages, session, logger, ctx


def finish_window(logger, ctx, response):
    ctx.inputs = SimpleNamespace(response=response)
    logger.after_model_call(ctx)


@pytest.mark.asyncio
async def test_accepted_jev_counts_once_at_client_boundary_and_task_end(caplog):
    policy, llm, _, messages, session, logger, ctx = await prepared()
    with caplog.at_level(logging.INFO, logger="browser-usage-test"):
        response = await policy.invoke(messages, tools=TOOLS)
        finish_window(logger, ctx, response)
        finish_window(logger, ctx, response)  # A duplicate callback cannot rebill usage.
        ctx.inputs = SimpleNamespace(result={"output": "done"})
        logger.after_invoke(ctx)
    end = next(json.loads(record.message.split("[BROWSER_SUBAGENT] ", 1)[1])
               for record in caplog.records if '"phase": "task_end"' in record.message)
    assert end["model_calls"] == 1
    summary = end["model_usage"]
    assert summary["jev"]["calls"] == summary["total"]["calls"] == 1
    assert summary["jev"]["total_tokens"] == 34
    assert summary["llm"]["calls"] == 0
    assert summary["total"]["token_usage_complete"]
    assert end["elapsed_ms"] >= 0
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [answer(confidence=0.1), answer("HANDOFF"), answer("FINISH")])
async def test_rejected_jev_and_llm_fallback_both_contribute(response):
    policy, _, client, messages, session, logger, ctx = await prepared()
    client.evaluate.return_value = response
    finish_window(logger, ctx, await policy.invoke(messages, tools=TOOLS))
    totals = collect(session)
    assert totals["jev"]["calls"] == totals["llm"]["calls"] == 1
    assert totals["total"]["calls"] == 2
    assert totals["total"]["input_tokens"] == 40
    assert totals["total"]["output_tokens"] == 7
    assert totals["total"]["total_tokens"] == 47
    assert totals["total"]["usage_unknown_calls"] == 0


@pytest.mark.asyncio
async def test_provider_timeout_is_a_call_with_unknown_usage_and_llm_is_still_counted():
    policy, _, client, messages, session, logger, ctx = await prepared()
    client.evaluate.side_effect = DecisionUnavailable("jev_timeout")
    finish_window(logger, ctx, await policy.invoke(messages, tools=TOOLS))
    totals = collect(session)
    assert totals["jev"]["calls"] == totals["jev"]["failed_calls"] == 1
    assert totals["jev"]["usage_unknown_calls"] == 1
    assert totals["total"]["total_tokens"] == 13
    assert not totals["total"]["token_usage_complete"]
    assert totals["llm"]["calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_source", ["jev", "llm"])
async def test_cancellation_without_model_end_is_counted_without_inventing_a_fallback(cancel_source):
    policy, llm, client, messages, session, logger, ctx = await prepared()
    if cancel_source == "jev":
        client.evaluate.side_effect = asyncio.CancelledError()
    else:
        client.evaluate.return_value = answer("HANDOFF")
        llm.invoke.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await policy.invoke(messages, tools=TOOLS)
    logger.after_invoke(ctx)
    totals = collect(session)
    assert totals[cancel_source]["cancelled_calls"] == 1
    assert totals[cancel_source]["usage_unknown_calls"] == 1
    assert totals["total"]["calls"] == (1 if cancel_source == "jev" else 2)


@pytest.mark.asyncio
async def test_stream_usage_uses_latest_snapshot_once():
    policy, llm, client, messages, session, logger, ctx = await prepared()
    client.evaluate.return_value = answer("HANDOFF")

    async def stream(**kwargs):
        yield AssistantMessageChunk(content="one", usage_metadata=usage(10, 1))
        yield AssistantMessageChunk(content="two", usage_metadata=usage(10, 3))
        yield AssistantMessageChunk(content="", usage_metadata=usage(10, 3))

    llm.stream = stream
    chunks = [chunk async for chunk in policy.stream(messages, tools=TOOLS)]
    response = chunks[0] + chunks[1] + chunks[2]
    finish_window(logger, ctx, response)
    totals = collect(session)
    assert totals["llm"]["calls"] == 1
    assert totals["llm"]["total_tokens"] == 13
    assert totals["jev"]["total_tokens"] == 34
    assert totals["total"]["calls"] == 2


@pytest.mark.asyncio
async def test_interrupted_stream_preserves_known_partial_tokens_but_marks_unknown_total():
    policy, llm, client, messages, session, logger, ctx = await prepared()
    client.evaluate.return_value = answer("HANDOFF")

    async def stream(**kwargs):
        yield AssistantMessageChunk(content="partial", usage_metadata=usage(10, 1))
        raise asyncio.CancelledError()

    llm.stream = stream
    with pytest.raises(asyncio.CancelledError):
        async for _ in policy.stream(messages, tools=TOOLS):
            pass
    logger.after_invoke(ctx)
    totals = collect(session)
    assert totals["llm"]["calls"] == totals["llm"]["cancelled_calls"] == 1
    assert totals["llm"]["total_tokens"] == 11
    assert not totals["llm"]["token_usage_complete"]


@pytest.mark.asyncio
async def test_pre_gate_skip_counts_only_the_llm():
    policy, _, client, messages, session, logger, ctx = await prepared()
    finish_window(logger, ctx, await policy.invoke(messages, tools=[]))
    totals = collect(session)
    assert totals["jev"]["calls"] == 0
    assert totals["llm"]["calls"] == totals["total"]["calls"] == 1
    client.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_deadline_before_fallback_does_not_count_an_llm_call(monkeypatch):
    policy, llm, client, messages, session, logger, ctx = await prepared()
    client.evaluate.return_value = answer("HANDOFF")

    def expired(deadline):
        raise TimeoutError("deadline exhausted before fallback")

    monkeypatch.setattr(policy, "_remaining", expired)
    with pytest.raises(TimeoutError):
        await policy.invoke(messages, tools=TOOLS)
    ctx.exception = TimeoutError()
    logger.on_model_exception(ctx)
    assert collect(session)["jev"]["calls"] == 1
    assert collect(session)["llm"]["calls"] == 0
    llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_counts_both_clients_without_double_counting_llm():
    policy, _, _, messages, session, logger, ctx = await prepared("shadow")
    finish_window(logger, ctx, await policy.invoke(messages, tools=TOOLS))
    await asyncio.gather(*policy._shadow_tasks)
    totals = collect(session)
    assert totals["llm"]["calls"] == totals["jev"]["calls"] == 1
    assert totals["total"]["total_tokens"] == 47


@pytest.mark.parametrize("mode", ["success", "failure", "missing_end"])
def test_pure_llm_callbacks_count_once_and_keep_unknown_usage_explicit(mode):
    session = MemorySession()
    logger = BrowserSubagentStatusLogger()
    ctx = context_for(session)
    logger.before_invoke(ctx)
    logger.before_model_call(ctx)
    if mode == "success":
        finish_window(logger, ctx, AssistantMessage(content="done", usage_metadata=usage()))
    elif mode == "failure":
        ctx.exception = TimeoutError()
        logger.on_model_exception(ctx)
    logger.after_invoke(ctx)
    totals = collect(session)
    assert totals["llm"]["calls"] == totals["total"]["calls"] == 1
    assert totals["llm"]["usage_unknown_calls"] == (0 if mode == "success" else 1)
    assert totals["jev"]["calls"] == 0


def test_pending_shadow_and_new_invocations_are_isolated(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(model_usage.time, "monotonic", lambda: now[0])
    session = MemorySession()
    logger = BrowserSubagentStatusLogger()
    ctx = context_for(session)
    logger.before_invoke(ctx)
    old_call = model_usage.start_model_call(session, "jev")
    now[0] += 2
    pending = collect(session)["jev"]
    assert pending["calls"] == pending["pending_calls"] == pending["usage_unknown_calls"] == 1
    assert pending["elapsed_ms"] == 2000
    logger.after_invoke(ctx)
    logger.before_invoke(ctx)
    model_usage.finish_model_call(session, old_call, usage(), 2000)
    assert collect(session)["total"]["calls"] == 0


def test_model_elapsed_sum_excludes_tools_and_sessions_do_not_leak():
    session = MemorySession()
    other = MemorySession(session_id="other")
    model_usage.store_model_usage(other, model_usage.new_model_usage())
    first = model_usage.start_model_call(session, "jev")
    second = model_usage.start_model_call(session, "llm")
    model_usage.finish_model_call(session, second, usage(), 700)
    model_usage.finish_model_call(session, first, {"input_tokens": 30, "output_tokens": 4}, 200)
    model_usage.finish_model_call(session, second, usage(), 700)
    totals = collect(session)
    assert totals["total"]["elapsed_ms"] == 900
    assert totals["llm"]["elapsed_ms"] == 700
    assert totals["jev"]["elapsed_ms"] == 200
    assert totals["total"]["calls"] == 2
    assert collect(other)["total"]["calls"] == 0


@pytest.mark.parametrize("invalid", [None, {}, {"input_tokens": True, "output_tokens": "4"},
                                    {"input_tokens": -1, "output_tokens": 4}])
def test_missing_or_invalid_usage_is_not_reported_as_complete(invalid):
    state = model_usage.new_model_usage()
    model_usage.add_model_usage(state, "jev", invalid, 5)
    totals = model_usage.summarize_model_usage(state)
    assert totals["total"]["usage_unknown_calls"] == 1
    assert not totals["total"]["token_usage_complete"]
