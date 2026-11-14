# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Memory receives complete logical turns across real interrupt/resume calls."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.core.single_agent.rail.base import RunKind
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserPayload, AskUserRail
from openjiuwen.harness.rails.memory import jiuwen_memory_rail as memory_module
from openjiuwen.harness.rails.memory.jiuwen_memory_rail import JiuwenMemoryRail
from tests.unit_tests.fixtures.deep_agent_interrupts import (
    APPROVE, ask_permissions, by_call_id, by_interrupt_id,
    make_deep_agent, pending, run, scripted,
)
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel, create_text_response, create_tool_call_response,
)
from tests.unit_tests.harness.rails.test_jiuwen_memory_rail import (
    MockAgent, MockMemoryProvider, make_ctx,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.level0]

REQUEST = "用 sender@example.com 给 recipient@example.com 发文档"
QUESTION = "请提供授权码"
ANSWER = "example-test-code"


class MemorySession:
    def __init__(self, session_id="memory-interrupt"):
        self.session_id = session_id
        self.interruption = None

    def get_session_id(self):
        return self.session_id

    def get_state(self, key):
        return self.interruption if key == INTERRUPTION_KEY else None


@pytest_asyncio.fixture
async def runner():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


def ask(question=QUESTION, call_id="ask_1"):
    return create_tool_call_response("ask_user", json.dumps({"questions": [{
        "header": "信息", "question": question,
        "options": [{"label": "填写", "description": ""}],
    }]}), tool_call_id=call_id)


async def drive(agent, query, streaming, session_id="memory-e2e"):
    if not streaming:
        return await run(agent, query, session_id)
    chunks = [chunk async for chunk in agent.stream({"query": query, "conversation_id": session_id})]
    interactions = [chunk for chunk in chunks if getattr(chunk, "type", None) == INTERACTION]
    if interactions:
        return {"result_type": "interrupt", "state": interactions}
    return next(chunk.payload for chunk in reversed(chunks) if getattr(chunk, "type", None) == "answer")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("address", ["call", "interrupt"])
@pytest.mark.parametrize("shape", ["answers", "answer", "text", "payload"])
async def test_real_ask_user_turn_is_synced_once(tmp_path, runner, streaming, address, shape):
    provider = MockMemoryProvider()
    rail = JiuwenMemoryRail(provider)
    agent = make_deep_agent(tmp_path, rails=[AskUserRail(), rail])
    llm = MockLLMModel()
    llm.set_responses([ask(), create_text_response("已发送"), create_text_response("下一轮回复")])
    value = {
        "answers": {"answers": {QUESTION: ANSWER}},
        "answer": {"answer": ANSWER},
        "text": ANSWER,
        "payload": AskUserPayload(answers={QUESTION: ANSWER}),
    }[shape]
    with scripted(llm):
        [(call_id, request)] = pending(await drive(agent, REQUEST, streaming))
        assert provider.sync_calls == []
        reply = by_call_id(call_id, value) if address == "call" else by_interrupt_id(request, value)
        result = await drive(agent, reply, streaming)
        assert result["result_type"] == "answer"
        await rail._sync_task
        assert len(provider.sync_calls) == 1
        user_text, output = provider.sync_calls[0]
        assert REQUEST in user_text
        assert QUESTION in user_text
        assert user_text.count(ANSWER) == 1
        assert output == "已发送"
        assert rail._memory_turns == {}
        await drive(agent, "新任务", streaming)
        await rail._sync_task
        assert provider.sync_calls[-1] == ("新任务", "下一轮回复")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("plain_text", [False, True])
async def test_repeated_interrupt_preserves_original_and_both_answers(tmp_path, runner, streaming, plain_text):
    provider = MockMemoryProvider()
    rail = JiuwenMemoryRail(provider)
    agent = make_deep_agent(tmp_path, rails=[AskUserRail(), rail])
    llm = MockLLMModel()
    llm.set_responses([ask(), ask("文档名称？", "ask_2"), create_text_response("已发送")])
    with scripted(llm):
        [(_, first)] = pending(await drive(agent, REQUEST, streaming))
        reply = ANSWER if plain_text else by_interrupt_id(first, {"answers": {QUESTION: ANSWER}})
        [(_, second)] = pending(await drive(agent, reply, streaming))
        assert provider.sync_calls == []
        await drive(agent, by_interrupt_id(second, {"answers": {"文档名称？": "report.pdf"}}), streaming)
        await rail._sync_task
    assert len(provider.sync_calls) == 1
    text, _ = provider.sync_calls[0]
    assert REQUEST in text and QUESTION in text and ANSWER in text and "report.pdf" in text
    assert text.count(ANSWER) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("unanswered", ["用户已取消本次问答，未作答。", "用户已跳过此题，未选择任何选项。"])
async def test_unanswered_question_preserves_request_without_ending_the_task(tmp_path, runner, streaming, unanswered):
    """A host's textual cancellation is an answer, not an execution abort."""
    provider = MockMemoryProvider()
    rail = JiuwenMemoryRail(provider)
    agent = make_deep_agent(tmp_path, rails=[AskUserRail(), rail])
    llm = MockLLMModel()
    llm.set_responses([
        ask(), create_text_response("缺少授权码，邮件未发送"), create_text_response("已收到补充信息"),
    ])
    with scripted(llm):
        [(_, question)] = pending(await drive(agent, REQUEST, streaming))
        assert provider.sync_calls == []
        await drive(agent, by_interrupt_id(question, {"answers": {QUESTION: unanswered}}), streaming)
        await rail._sync_task
        assert len(provider.sync_calls) == 1
        user_text, reply = provider.sync_calls[0]
        assert REQUEST in user_text
        assert user_text.count(unanswered) == 1
        assert reply == "缺少授权码，邮件未发送"
        assert rail._memory_turns == {}
        await drive(agent, ANSWER, streaming)
        await rail._sync_task
    assert provider.sync_calls[1] == (ANSWER, "已收到补充信息")


async def test_permission_and_stale_answers_are_not_user_facts(tmp_path, runner):
    provider = MockMemoryProvider()
    rail = JiuwenMemoryRail(provider)
    agent = make_deep_agent(tmp_path, rails=[AskUserRail(), rail], permissions=ask_permissions("ask_user"))
    llm = MockLLMModel()
    llm.set_responses([ask(), create_text_response("已发送")])
    with scripted(llm):
        [(_, permission)] = pending(await run(agent, REQUEST, "permission-memory"))
        [(_, question)] = pending(await run(agent, by_interrupt_id(permission, APPROVE), "permission-memory"))
        stale = InteractiveInput()
        stale.answer("intr_stale", {"answers": {QUESTION: "STALE-SECRET"}})
        assert pending(await run(agent, stale, "permission-memory"))
        assert provider.sync_calls == []
        await run(agent, by_interrupt_id(question, {"answers": {QUESTION: ANSWER}}), "permission-memory")
        await rail._sync_task
    text, _ = provider.sync_calls[0]
    assert REQUEST in text and ANSWER in text
    assert "approved" not in text and "STALE-SECRET" not in text


def unit_rail():
    provider = MockMemoryProvider()
    rail = JiuwenMemoryRail(provider)
    rail.init(MockAgent())
    return rail, provider


async def invoke_hook(rail, session, query, result, *, run_kind=None, exception=None):
    ctx = make_ctx(query=query, run_kind=run_kind)
    ctx.session = session
    await rail.before_invoke(ctx)
    ctx.inputs.result = result
    ctx.exception = exception
    await rail.after_invoke(ctx)
    if rail._sync_task is not None:
        await rail._sync_task


@pytest.mark.parametrize("run_kind", [RunKind.HEARTBEAT, RunKind.CRON])
async def test_background_does_not_touch_pending_turn(run_kind):
    rail, provider = unit_rail()
    session = MemorySession()
    session.interruption = object()
    await invoke_hook(rail, session, REQUEST, {"result_type": "interrupt"})
    before = rail._memory_turns[session.session_id].render()
    await invoke_hook(rail, session, "background", {"output": "background"}, run_kind=run_kind)
    assert rail._memory_turns[session.session_id].render() == before
    await invoke_hook(rail, session, "补充", {"output": "已完成"})
    assert REQUEST in provider.sync_calls[0][0]
    assert "background" not in provider.sync_calls[0][0]


async def test_new_task_and_other_session_do_not_inherit_pending_text():
    rail, provider = unit_rail()
    first = MemorySession("first")
    await invoke_hook(rail, first, REQUEST, {"result_type": "interrupt"})
    await invoke_hook(rail, MemorySession("second"), "其他会话", {"output": "回复"})
    await invoke_hook(rail, first, "新任务", {"output": "新回复"})
    assert provider.sync_calls == [("其他会话", "回复"), ("新任务", "新回复")]


async def test_breaker_maintains_interrupted_turn_and_consumes_finished_turn():
    rail, provider = unit_rail()
    rail._sync_consecutive_failures = memory_module._SYNC_BREAKER_THRESHOLD
    rail._sync_breaker_until = float("inf")
    session = MemorySession()
    session.interruption = object()
    await invoke_hook(rail, session, REQUEST, {"result_type": "interrupt"})
    assert session.session_id in rail._memory_turns
    await invoke_hook(rail, session, InteractiveInput(), {"output": "已完成"})
    assert provider.sync_calls == []
    assert rail._memory_turns == {}


@pytest.mark.parametrize("result,exception", [
    (None, None), (None, RuntimeError("failed")),
    ({"result_type": "error", "output": "失败"}, None),
])
async def test_empty_and_failed_calls_do_not_leave_a_turn(result, exception):
    rail, provider = unit_rail()
    await invoke_hook(rail, MemorySession(), REQUEST, result, exception=exception)
    assert rail._memory_turns == {}
    assert provider.sync_calls == []


async def test_streaming_empty_interrupt_and_missing_cache():
    rail, provider = unit_rail()
    session = MemorySession()
    session.interruption = object()
    await invoke_hook(rail, session, REQUEST, None)
    assert session.session_id in rail._memory_turns
    await invoke_hook(rail, MemorySession("missing"), InteractiveInput(), {"output": "完成"})
    assert provider.sync_calls == []


async def test_cancelled_invoke_clears_turn_even_with_old_interrupt_state():
    rail, provider = unit_rail()
    session = MemorySession()
    session.interruption = object()
    started = asyncio.Event()

    async def cancel_during_invoke():
        ctx = make_ctx(query=REQUEST)
        ctx.session = session
        await rail.before_invoke(ctx)
        started.set()
        try:
            await asyncio.Future()
        finally:
            await rail.after_invoke(ctx)

    task = asyncio.create_task(cancel_during_invoke())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rail._memory_turns == {}
    assert provider.sync_calls == []


async def test_carried_and_replayed_answers_are_not_appended_twice():
    rail, _ = unit_rail()
    session = MemorySession()
    ctx = make_ctx(query=REQUEST)
    ctx.session = session
    await rail.before_invoke(ctx)
    answer = SimpleNamespace(
        kind="ask_user", carried=False, unaddressed=False,
        request=SimpleNamespace(interrupt_id="answer-1"),
    )
    tool_ctx = SimpleNamespace(
        session=session, extra={memory_module.RESUME_INTERRUPT_ANSWERS_KEY: {"call": {"owner": answer}}},
        inputs=SimpleNamespace(tool_name="ask_user", tool_call=SimpleNamespace(id="call"), tool_result=ANSWER),
        exception=None, is_paused=False,
    )
    await rail.after_tool_call(tool_ctx)
    await rail.after_tool_call(tool_ctx)
    answer.carried = True
    await rail.after_tool_call(tool_ctx)
    assert rail._memory_turns[session.session_id].render().count(ANSWER) == 1


async def test_pending_turn_size_expiration_and_capacity(monkeypatch):
    rail, _ = unit_rail()
    turn = memory_module._MemoryTurn("HEAD" + "x" * 20000 + "TAIL")
    for i in range(100):
        turn.append(f"question-{i}:" + "y" * 300)
    rendered = turn.render()
    assert len(rendered) <= 16000
    assert "HEAD" in rendered and "TAIL" in rendered
    assert "question-99:" in rendered and "question-0:" not in rendered
    assert "已截断" in rendered
    turn.append("oversize-answer:" + "z" * 20000)
    assert "question-99:" in turn.render()
    assert "oversize-answer:" not in turn.render()
    assert len(turn.render()) <= 16000
    monkeypatch.setattr(memory_module, "_TURN_MAX_SESSIONS", 2)
    for sid in ["one", "two", "three"]:
        await invoke_hook(rail, MemorySession(sid), REQUEST, {"result_type": "interrupt"})
    assert set(rail._memory_turns) == {"two", "three"}
    for pending_turn in rail._memory_turns.values():
        pending_turn.updated -= memory_module._TURN_TTL
    await invoke_hook(rail, MemorySession("four"), REQUEST, {"result_type": "interrupt"})
    assert set(rail._memory_turns) == {"four"}
    await rail.end_session()
    assert rail._memory_turns == {}


async def test_conversation_id_fallback_and_missing_identity():
    rail, provider = unit_rail()
    first = make_ctx(query=REQUEST, result={"result_type": "interrupt"})
    first.inputs.conversation_id = "fallback"
    await rail.before_invoke(first)
    await rail.after_invoke(first)
    last = make_ctx(query=InteractiveInput(), result={"output": "完成"})
    last.inputs.conversation_id = "fallback"
    await rail.before_invoke(last)
    await rail.after_invoke(last)
    await rail._sync_task
    assert provider.sync_calls == [(REQUEST, "完成")]
    await invoke_hook(rail, None, "无会话", {"result_type": "interrupt"})
    assert rail._memory_turns == {}
