# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The public stream delivers the result finalized by outer rails."""

# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
    BrowserRuntimeRail,
)
from openjiuwen.harness.tools.subagent.task_tool import TaskTool, _run_subagent_with_observable_stream
from tests.unit_tests.harness.test_deep_agent import FakeReactAgent, _create_dummy_model
from tests.unit_tests.harness.tools.browser_move.test_browser_runtime_rail import _declare_output


@pytest_asyncio.fixture(autouse=True)
async def isolated_runner(monkeypatch):
    monkeypatch.setattr("openjiuwen.harness.deep_agent.schedule_image_support_probe", MagicMock())
    await Runner.start()
    yield
    await Runner.stop()


class FinalizingRail(AgentRail):
    def __init__(self):
        self.sessions = []
        self.finished = False

    async def before_invoke(self, ctx):
        self.sessions.append(ctx.session)

    async def after_invoke(self, ctx):
        assert ctx.session is self.sessions[-1]
        self.finished = True
        if ctx.inputs.result is not None:
            # Replace the result object, not just a field in the old payload.
            ctx.inputs.result = {**ctx.inputs.result, "output": "finalized", "rail_field": "kept"}
            ctx.session.update_state({"terminal_marker": "finalized"})


class AnswerInner(FakeReactAgent):
    def __init__(self, envelope="schema", *, answer=True):
        super().__init__()
        self.envelope = envelope
        self.answer = answer
        self.original = None
        self.session = None

    async def invoke(self, inputs, session=None, **kwargs):
        self.session = session
        return {"output": "draft", "result_type": "answer"}

    async def stream(self, inputs, session=None, stream_modes=None):
        self.session = session
        yield OutputSchema(type="llm_output", index=3, payload={"content": "draft"})
        if self.answer:
            payload = {"output": "draft", "result_type": "answer"}
            self.original = (
                {"type": "answer", "index": 7, "request_id": "req-1", "payload": payload}
                if self.envelope == "dict"
                else OutputSchema(type="answer", index=7, payload=payload)
            )
            yield self.original


def make_agent(inner):
    agent = DeepAgent(AgentCard(id="terminal-test", name="terminal-test")).configure(
        DeepAgentConfig(enable_task_loop=False)
    )
    agent.set_react_agent(inner, initialized=False)
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", ["dict", "schema"])
@pytest.mark.parametrize("explicit_session", [False, True])
async def test_terminal_is_finalized_once_before_delivery(monkeypatch, envelope, explicit_session):
    inner = AnswerInner(envelope)
    agent = make_agent(inner)
    rail = FinalizingRail()
    agent.add_rail(rail)
    saved = []
    original_save = agent.save_state

    def save(session):
        assert rail.finished
        saved.append(session.get_state("terminal_marker"))
        original_save(session)

    monkeypatch.setattr(agent, "save_state", save)
    session = Session(session_id="explicit") if explicit_session else None
    chunks = []
    async for chunk in agent.stream({"query": "read", "conversation_id": "implicit"}, session):
        chunk_type = chunk.get("type") if isinstance(chunk, dict) else chunk.type
        if chunk_type == "llm_output":
            assert not rail.finished
        if chunk_type == "answer":
            assert saved == ["finalized"]
        chunks.append(chunk)

    assert len(chunks) == 2
    terminal = chunks[-1]
    payload = terminal["payload"] if isinstance(terminal, dict) else terminal.payload
    assert payload == {"output": "finalized", "result_type": "answer", "rail_field": "kept"}
    assert inner.session is rail.sessions[0]
    assert inner.session.get_session_id() == ("explicit" if explicit_session else "implicit")
    if envelope == "dict":
        assert terminal["index"] == 7 and terminal["request_id"] == "req-1"
        assert inner.original["payload"]["output"] == "draft"
    else:
        assert terminal.index == 7
        assert inner.original.payload["output"] == "draft"


@pytest.mark.asyncio
async def test_invoke_honors_replaced_rail_result():
    inner = AnswerInner()
    agent = make_agent(inner)
    rail = FinalizingRail()
    agent.add_rail(rail)
    result = await agent.invoke({"query": "read", "conversation_id": "invoke"})
    assert result["rail_field"] == "kept"
    assert result["output"] == "finalized"
    assert inner.session is rail.sessions[0]


@pytest.mark.asyncio
async def test_text_only_stream_gets_one_finalized_answer():
    agent = make_agent(AnswerInner(answer=False))
    agent.add_rail(FinalizingRail())
    chunks = [chunk async for chunk in agent.stream({"query": "read", "conversation_id": "text-only"})]
    assert [chunk.type for chunk in chunks] == ["llm_output", "answer"]
    assert chunks[-1].payload["rail_field"] == "kept"


@pytest.mark.asyncio
async def test_interrupt_does_not_turn_provisional_text_into_an_answer():
    class InterruptInner(AnswerInner):
        async def stream(self, inputs, session=None, stream_modes=None):
            yield OutputSchema(type="llm_output", index=0, payload={"content": "Need approval"})
            yield OutputSchema(type=INTERACTION, index=1, payload={"id": "approval"})

    agent = make_agent(InterruptInner())
    rail = FinalizingRail()
    agent.add_rail(rail)
    chunks = [chunk async for chunk in agent.stream("read")]
    assert [chunk.type for chunk in chunks] == ["llm_output", INTERACTION]
    assert rail.finished


@pytest.mark.asyncio
async def test_only_last_answer_is_delivered():
    class MultipleAnswers(AnswerInner):
        async def stream(self, inputs, session=None, stream_modes=None):
            yield {"type": "answer", "index": 1, "payload": {"output": "first"}}
            yield {"type": "llm_usage", "payload": {"tokens": 10}}
            yield {"type": "answer", "index": 2, "payload": {"output": "last"}}

    agent = make_agent(MultipleAnswers())
    agent.add_rail(FinalizingRail())
    chunks = [chunk async for chunk in agent.stream({"query": "read", "conversation_id": "multiple"})]
    assert [chunk["type"] for chunk in chunks] == ["llm_usage", "answer"]
    assert chunks[-1]["index"] == 2
    assert chunks[-1]["payload"]["rail_field"] == "kept"


@pytest.mark.asyncio
@pytest.mark.parametrize("close_mode", ["cancel", "aclose"])
async def test_stream_cancellation_finalizes_without_late_answer(close_mode):
    waiting = asyncio.Event()
    closed = asyncio.Event()

    class WaitingInner(AnswerInner):
        async def stream(self, inputs, session=None, stream_modes=None):
            try:
                yield OutputSchema(type="llm_output", index=0, payload={"content": "draft"})
                waiting.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    agent = make_agent(WaitingInner())
    rail = FinalizingRail()
    agent.add_rail(rail)
    stream = agent.stream("read")
    assert (await anext(stream)).type == "llm_output"
    if close_mode == "aclose":
        await stream.aclose()
    else:
        task = asyncio.create_task(anext(stream))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert rail.finished and closed.is_set()
    assert not agent._invoke_active
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "explicit_contract"),
    [
        ("completed", False), ("partial", False), ("blocked", False),
        ("provider_error", False), ("max_iterations", False), ("raw_weather", False),
        ("completed", True), ("partial", True),
    ],
)
async def test_real_browser_rail_stream_reaches_tasktool(monkeypatch, outcome, explicit_contract):
    browser = DeepAgent(AgentCard(id="openjiuwen.browser_agent", name="browser_agent")).configure(
        DeepAgentConfig(model=_create_dummy_model(), enable_task_loop=False, tools=[], mcps=[], skills=[])
    )
    runtime = MagicMock(spec=BrowserAgentRuntime)
    runtime.ensure_runtime_ready = AsyncMock()
    runtime.service.guardrails.timeout_s = 600
    runtime.service.build_failure_summary.return_value = "browser task incomplete"
    rail = BrowserRuntimeRail(runtime)
    monkeypatch.setattr(rail, "_ensure_browser_mcp_ability", AsyncMock())
    if explicit_contract:
        build_state = rail._build_phase_state
        monkeypatch.setattr(rail, "_build_phase_state", lambda task: _declare_output(build_state(task), ["title"]))
    browser.add_rail(rail)
    seen_sessions = []

    async def call_model(ctx, context, tools):
        session = ctx.session
        seen_sessions.append(session)
        state = session.get_state("__browser_phase_budget_state__")
        assert state is not None  # The real BEFORE_INVOKE had the same session.
        assert state["requirements_source"] == ("explicit" if explicit_contract else "observations")
        assert state["required_fields"] == (["title"] if explicit_contract else [])
        if outcome == "completed":
            state["last_page"] = {"url": "https://example.test/weather", "generation_id": "g1"}
            BrowserRuntimeRail._record_tool_evidence(
                state, {"result": {"title": "Weather"}, "generation_id": "g1"},
                tool_name="mcp_playwright-official_browser_evaluate",
                tool_args={"function": "() => ({title: document.title})"},
            )
        elif outcome == "raw_weather":
            state["last_page"] = {"url": "https://www.bing.com/search?q=Singapore+weather"}
            BrowserRuntimeRail._record_tool_evidence(
                state,
                {
                    "result": {"sel": "#weather", "text": "Singapore weather: high 33 C, low 26 C"},
                    "generation_id": "g1",
                },
                tool_name="mcp_playwright-official_browser_evaluate",
                tool_args={"function": "() => document.querySelector('#weather').textContent"},
            )
        elif outcome == "blocked":
            state.update(status="blocked", blockers=["login_required"], terminal_reason="runtime_blocked")
        session.update_state({"__browser_phase_budget_state__": state})
        if outcome == "provider_error":
            raise RuntimeError("provider unavailable")
        if outcome == "max_iterations":
            return {"output": "Max iterations reached without completion", "result_type": "error"}
        answer = "Singapore weather: high 33 C, low 26 C" if outcome == "raw_weather" else "Weather"
        return AssistantMessage(content=answer, finish_reason="stop")

    monkeypatch.setattr(browser.react_agent, "_call_model", call_model)
    parent = SimpleNamespace(create_subagent=lambda *_args, **_kwargs: browser)
    tool = TaskTool(card=ToolCard(id="terminal-task", name="task_tool"), parent_agent=parent)
    result = await tool.invoke(
        {"subagent_type": "browser_agent", "task_description": (
            "Report Singapore low_temperature" if outcome == "raw_weather" else "Read the title"
        )},
        session=Session(session_id="parent"),
    )
    assert result.success
    authority = result.data["browser_result"]
    expected = "completed" if outcome == "raw_weather" else outcome
    assert authority["status"] == (expected if expected in {"completed", "partial", "blocked"} else "blocked")
    assert "authoritative_browser_result_missing" not in str(result.data)
    assert seen_sessions[0].get_session_id().startswith("parent_sub_browser_agent_")
    assert authority["verification_scope"] == "execution_and_observations"
    assert authority["completion_basis"] == "worker_judgment"
    assert "unverified_fields" not in authority
    if not explicit_contract:
        assert authority["requested_slots"] == authority["evidence"] == []
    if outcome == "completed":
        observation = authority["observations"][0]
        assert observation["source"] == "https://example.test/weather"
        assert observation["generation_id"] == "g1"
        assert "Weather" in observation["raw_text"]
        assert authority["missing_fields"] == []
        if explicit_contract:
            assert authority["evidence"][0]["value"] == "Weather"
            assert authority["requested_slots"][0]["field"] == "title"
    elif outcome == "partial":
        assert authority["missing_fields"] == (["title"] if explicit_contract else [])
        assert authority["observations"] == []
        assert authority["terminal_reason"] == "no_task_observation"
        assert authority["retryable"]
        assert authority["summary"] == "Weather"
    elif outcome == "raw_weather":
        assert authority["missing_fields"] == []
        assert "26 C" in authority["summary"]
        assert "26 C" in authority["observations"][0]["raw_text"]
        assert authority["observations"][0]["source"] == "https://www.bing.com/search?q=Singapore+weather"
        assert authority["observations"][0]["generation_id"] == "g1"


@pytest.mark.asyncio
async def test_unstructured_stream_error_still_raises():
    class ErrorAgent:
        async def stream(self, inputs):
            yield {"type": "answer", "payload": {"output": "genuine error", "result_type": "error"}}

    with pytest.raises(Exception, match="genuine error"):
        await _run_subagent_with_observable_stream(ErrorAgent(), {"query": "read"})


@pytest.mark.asyncio
async def test_react_emitter_preserves_result_extensions():
    browser = DeepAgent(AgentCard(id="emitter-test")).configure(
        DeepAgentConfig(model=_create_dummy_model(), enable_task_loop=False)
    )
    session = SimpleNamespace(write_stream=AsyncMock())
    result = {"output": "ok", "result_type": "answer", "rail_field": "kept"}
    await browser.react_agent.write_invoke_result_to_stream(result, session)
    assert session.write_stream.call_args.args[0].payload == result


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_contract", [False, True])
async def test_focused_tasktool_resume_preserves_real_browser_requirements(monkeypatch, explicit_contract):
    observed_states = []

    def create_browser(*_args, **_kwargs):
        browser = DeepAgent(AgentCard(id="openjiuwen.browser_agent", name="browser_agent")).configure(
            DeepAgentConfig(model=_create_dummy_model(), enable_task_loop=False, tools=[], mcps=[], skills=[])
        )
        runtime = MagicMock(spec=BrowserAgentRuntime)
        runtime.ensure_runtime_ready = AsyncMock()
        runtime.service.guardrails.timeout_s = 600
        runtime.semantic_progress = {}
        rail = BrowserRuntimeRail(runtime)
        monkeypatch.setattr(rail, "_ensure_browser_mcp_ability", AsyncMock())
        if explicit_contract:
            build_state = rail._build_phase_state
            monkeypatch.setattr(rail, "_build_phase_state", lambda task: _declare_output(
                build_state(task), ["title", "product_rating"],
            ))
        browser.add_rail(rail)

        async def call_model(ctx, _context, _tools):
            state = ctx.session.get_state("__browser_phase_budget_state__")
            assert state["requirements_source"] == ("explicit" if explicit_contract else "observations")
            assert state["required_fields"] == (["title", "product_rating"] if explicit_contract else [])
            run_context = rail._browser_run_context(ctx)
            if observed_states:
                assert run_context["browser_resume"] is True
                assert state["task_id"] == observed_states[0]["task_id"]
                assert state["deadline_at"] == observed_states[0]["deadline_at"]
                assert BrowserRuntimeRail._task_observations(state) == BrowserRuntimeRail._task_observations(
                    observed_states[0]
                )
                if explicit_contract:
                    assert any(slot["field"] == "title" for slot in state["evidence_slots"])
                assert state["resume_count"] == 1
            state["last_page"] = {"url": "https://example.test/item/keyboard", "generation_id": "g1"}
            extracted = {"product_rating": "4.8"} if observed_states else {"title": "Keyboard"}
            BrowserRuntimeRail._record_tool_evidence(
                state, {"extracted": extracted, "generation_id": "g1"},
                tool_name="browser_batch_interact", tool_args={},
            )
            ctx.session.update_state({"__browser_phase_budget_state__": state})
            observed_states.append(copy.deepcopy(state))
            content = "Keyboard, rated 4.8"
            if len(observed_states) == 1:
                content = (
                    "Keyboard found; product rating is not read yet."
                    '<browser_progress>{"status":"partial","next_action":"read product rating"}</browser_progress>'
                )
            return AssistantMessage(content=content)

        monkeypatch.setattr(browser.react_agent, "_call_model", call_model)
        return browser

    tool = TaskTool(card=ToolCard(id="resume-test", name="task_tool"), parent_agent=SimpleNamespace(
        create_subagent=create_browser,
    ))
    session = Session(session_id="resume-parent")
    first = await tool.invoke({
        "subagent_type": "browser_agent", "task_description": "Return product title and product_rating",
    }, session=session)
    assert first.success
    assert first.data["browser_result"]["status"] == "partial"
    first_observations = first.data["browser_result"]["observations"]
    assert any("Keyboard" in observation["raw_text"] for observation in first_observations)
    if explicit_contract:
        assert first.data["browser_result"]["missing_fields"] == ["product_rating"]
    else:
        assert first.data["browser_result"]["requested_slots"] == []
    second = await tool.invoke({
        "subagent_type": "browser_agent", "task_description": "Only read the missing product rating",
        "resume_task_id": first.data["resume_task_id"],
    }, session=session)
    assert second.success
    assert len(observed_states) == 2
    assert second.data["browser_result"]["status"] == "completed"
    assert second.data["browser_result"]["missing_fields"] == []
    second_observations = second.data["browser_result"]["observations"]
    assert all(observation in second_observations for observation in first_observations)
    assert any("4.8" in observation["raw_text"] for observation in second_observations)
    assert all(observation["source"] == "https://example.test/item/keyboard" for observation in second_observations)
