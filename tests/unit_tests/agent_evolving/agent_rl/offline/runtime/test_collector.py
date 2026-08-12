# -*- coding: utf-8 -*-
"""Focused tests for the canonical RL rail and offline collector."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState

from openjiuwen.agent_evolving.agent_rl.offline.runtime.collector import run_agent_and_collect_trajectory
from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import (
    CASE_ID,
    RL_COMPLETION_TOKEN_IDS,
    RL_LOGPROBS,
    RL_PROMPT_TOKEN_IDS,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import iter_spans, read_tool_call, span_attributes
from openjiuwen.extensions.observability import semconv
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs, ToolCallInputs


def _span(span_id: int, *, name: str = "llm.call", attrs: dict | None = None) -> ReadableSpan:
    context = SpanContext(
        trace_id=1,
        span_id=span_id,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )
    return ReadableSpan(
        name=name,
        context=context,
        resource=Resource.create({}),
        kind=SpanKind.INTERNAL,
        attributes=attrs or {},
        status=Status(StatusCode.OK),
        start_time=span_id,
        end_time=span_id + 1,
    )


def _ctx(inputs) -> AgentCallbackContext:
    return AgentCallbackContext(agent=None, inputs=inputs)


def _llm_attrs(prompt: str = "q", completion: str = "a") -> dict[str, str]:
    return {
        f"{semconv.GEN_AI_PROMPT}.0.role": "user",
        f"{semconv.GEN_AI_PROMPT}.0.content": prompt,
        f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
        f"{semconv.GEN_AI_COMPLETION}.0.content": completion,
    }


@pytest.mark.asyncio
async def test_rl_rail_uses_clean_canonical_projection() -> None:
    processor = TrajectorySpanProcessor()
    rail = RLRail(
        session_id="test-session",
        source="offline",
        case_id="case-123",
        trajectory_span_processor=processor,
    )
    invoke_inputs = InvokeInputs(query="hi", conversation_id="test-session")

    await rail.before_invoke(_ctx(invoke_inputs))
    processor.on_end(_span(1, attrs=_llm_attrs()))
    await rail.after_model_call(_ctx(ModelCallInputs(response={"role": "assistant", "content": "a"})))
    await rail.after_invoke(_ctx(invoke_inputs))

    trajectory = rail.get_trajectory(session_id="test-session")
    assert trajectory is not None
    assert trajectory.resource_attributes[TRAJECTORY_SOURCE] == "offline"
    assert trajectory.resource_attributes[CASE_ID] == "case-123"
    assert len(list(iter_spans(trajectory))) == 1


@pytest.mark.asyncio
async def test_rl_rail_reads_tool_span_without_legacy_step_projection() -> None:
    processor = TrajectorySpanProcessor()
    rail = RLRail(trajectory_span_processor=processor, session_id="test")
    invoke_inputs = InvokeInputs(query="q", conversation_id="test")
    await rail.before_invoke(_ctx(invoke_inputs))
    processor.on_end(
        _span(
            2,
            name="tool.lookup",
            attrs={
                semconv.GEN_AI_TOOL_NAME: "lookup",
                semconv.GEN_AI_TOOL_ID: "call-1",
                semconv.GEN_AI_TOOL_INPUT: '{"q":"x"}',
                semconv.GEN_AI_TOOL_OUTPUT: '{"ok":true}',
            },
        )
    )
    await rail.after_tool_call(_ctx(ToolCallInputs(tool_name="lookup")))
    await rail.after_invoke(_ctx(invoke_inputs))

    trajectory = rail.get_trajectory(session_id="test")
    assert trajectory is not None
    assert read_tool_call(next(iter(iter_spans(trajectory))))["name"] == "lookup"


@pytest.mark.asyncio
async def test_rl_rail_lifts_token_fields_into_immutable_spans() -> None:
    processor = TrajectorySpanProcessor()
    rail = RLRail(trajectory_span_processor=processor, session_id="test")
    invoke_inputs = InvokeInputs(query="q", conversation_id="test")
    await rail.before_invoke(_ctx(invoke_inputs))
    processor.on_end(_span(3, attrs=_llm_attrs()))
    response = {
        "role": "assistant",
        "content": "a",
        "prompt_token_ids": [11, 12],
        "completion_token_ids": [21, 22],
        "logprobs": [-0.1, -0.2],
    }
    await rail.after_model_call(_ctx(ModelCallInputs(response=response)))
    await rail.after_invoke(_ctx(invoke_inputs))

    trajectory = rail.get_trajectory(session_id="test")
    assert trajectory is not None
    attrs = span_attributes(next(iter(iter_spans(trajectory))))
    assert attrs[RL_PROMPT_TOKEN_IDS] == [11, 12]
    assert attrs[RL_COMPLETION_TOKEN_IDS] == [21, 22]
    assert attrs[RL_LOGPROBS] == [-0.1, -0.2]
    assert response["prompt_token_ids"] == [11, 12]


@pytest.mark.asyncio
async def test_rl_rail_clean_window_can_accumulate_invokes() -> None:
    processor = TrajectorySpanProcessor()
    rail = RLRail(trajectory_span_processor=processor, session_id="same-session")
    for index in (1, 2):
        invoke = InvokeInputs(query=f"q{index}", conversation_id="same-session")
        await rail.before_invoke(_ctx(invoke))
        processor.on_end(_span(index, attrs=_llm_attrs(prompt=f"q{index}")))
        await rail.after_model_call(_ctx(ModelCallInputs(response={"role": "assistant", "content": "a"})))
        await rail.after_invoke(_ctx(invoke))

    trajectory = rail.get_trajectory(session_id="same-session")
    assert trajectory is not None
    assert len(list(iter_spans(trajectory))) == 2


@pytest.mark.asyncio
async def test_rl_rail_keeps_full_single_invoke_window() -> None:
    processor = TrajectorySpanProcessor()
    rail = RLRail(trajectory_span_processor=processor, session_id="same-session")
    invoke = InvokeInputs(query="q", conversation_id="same-session")
    await rail.before_invoke(_ctx(invoke))
    for index in range(1, 202):
        processor.on_end(_span(index, attrs=_llm_attrs(prompt=f"q{index}")))
        await rail.after_model_call(_ctx(ModelCallInputs(response={"role": "assistant", "content": "a"})))
    await rail.after_invoke(_ctx(invoke))

    trajectory = rail.get_trajectory(session_id="same-session")
    assert trajectory is not None
    assert len(list(iter_spans(trajectory))) == 201


@pytest.mark.asyncio
async def test_run_agent_reads_before_unregister_on_failure() -> None:
    rail_holder: dict[str, RLRail] = {}

    async def register(rail: RLRail) -> None:
        rail_holder["rail"] = rail

    async def invoke(inputs, session=None) -> None:
        rail = rail_holder["rail"]
        invoke_inputs = InvokeInputs(query="test", conversation_id="test")
        await rail.before_invoke(AgentCallbackContext(agent=None, inputs=invoke_inputs, session=session))
        rail.trajectory_span_processor.on_end(_span(9, attrs=_llm_attrs(prompt="q")))
        await rail.after_model_call(_ctx(ModelCallInputs(response={"role": "assistant", "content": "partial"})))
        raise RuntimeError("something went wrong")

    mock_agent = MagicMock()
    mock_agent.register_rail = register
    mock_agent.unregister_rail = AsyncMock()
    mock_agent.invoke = invoke
    mock_agent.card = AgentCard(id="test-agent", name="test-agent")

    result = await run_agent_and_collect_trajectory(
        mock_agent,
        {"query": "test"},
        trajectory_span_processor=TrajectorySpanProcessor(),
    )

    assert result is not None
    assert result.resource_attributes[TRAJECTORY_SOURCE] == "offline"
    assert len(list(iter_spans(result))) == 1
    mock_agent.unregister_rail.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_agent_returns_none_without_ended_spans() -> None:
    mock_agent = MagicMock()
    mock_agent.register_rail = AsyncMock()
    mock_agent.unregister_rail = AsyncMock()
    mock_agent.invoke = AsyncMock()
    mock_agent.card = AgentCard(id="test-agent", name="test-agent")

    result = await run_agent_and_collect_trajectory(
        mock_agent,
        {"query": "test"},
        trajectory_span_processor=TrajectorySpanProcessor(),
    )

    assert result is None
    mock_agent.register_rail.assert_awaited_once()
    mock_agent.unregister_rail.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_agent_rejects_unsupported_agent() -> None:
    class PlainAgent:
        pass

    with pytest.raises(ValueError, match="register_rail"):
        await run_agent_and_collect_trajectory(
            PlainAgent(),
            {"query": "test"},
            trajectory_span_processor=TrajectorySpanProcessor(),
        )


@pytest.mark.asyncio
async def test_run_agent_unregisters_and_closes_when_pre_run_fails(monkeypatch) -> None:
    session = MagicMock()
    session.pre_run = AsyncMock(side_effect=RuntimeError("pre-run failed"))
    session.close_stream = AsyncMock()
    session.commit = AsyncMock()
    monkeypatch.setattr("openjiuwen.core.session.agent.create_agent_session", lambda **kwargs: session)
    mock_agent = MagicMock()
    mock_agent.register_rail = AsyncMock()
    mock_agent.unregister_rail = AsyncMock()
    mock_agent.card = AgentCard(id="test-agent", name="test-agent")

    with pytest.raises(RuntimeError, match="pre-run failed"):
        await run_agent_and_collect_trajectory(
            mock_agent,
            {"query": "test"},
            trajectory_span_processor=TrajectorySpanProcessor(),
        )

    mock_agent.unregister_rail.assert_awaited_once()
    session.close_stream.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_runs_isolate_subscriptions_on_shared_processor() -> None:
    processor = TrajectorySpanProcessor()

    class Agent:
        def __init__(self, name: str, span_id: int) -> None:
            self.card = AgentCard(id=name, name=name)
            self._span_id = span_id
            self._rail: RLRail | None = None

        async def register_rail(self, rail: RLRail) -> None:
            self._rail = rail

        async def unregister_rail(self, rail: RLRail) -> None:
            assert self._rail is rail

        async def invoke(self, inputs, session=None) -> None:
            assert self._rail is not None
            invoke_inputs = InvokeInputs(query=inputs["query"], conversation_id=inputs["conversation_id"])
            invoke_ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)
            await self._rail.before_invoke(invoke_ctx)
            await asyncio.sleep(0)
            processor.on_end(_span(self._span_id, attrs=_llm_attrs(prompt=inputs["query"])))
            await self._rail.after_model_call(
                AgentCallbackContext(
                    agent=self,
                    inputs=ModelCallInputs(response={"role": "assistant", "content": "a"}),
                    session=session,
                )
            )
            await self._rail.after_invoke(invoke_ctx)

    async def run(name: str, span_id: int):
        return await run_agent_and_collect_trajectory(
            Agent(name, span_id),
            {"query": name, "conversation_id": name},
            trajectory_span_processor=processor,
            session_id=name,
        )

    first, second = await asyncio.gather(run("first", 31), run("second", 32))

    assert first is not None and second is not None
    first_spans = list(iter_spans(first))
    second_spans = list(iter_spans(second))
    assert len(first_spans) == len(second_spans) == 1
    assert span_attributes(first_spans[0])[f"{semconv.GEN_AI_PROMPT}.0.content"] == "first"
    assert span_attributes(second_spans[0])[f"{semconv.GEN_AI_PROMPT}.0.content"] == "second"
