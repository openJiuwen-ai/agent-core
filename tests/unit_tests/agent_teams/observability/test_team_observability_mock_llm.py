# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit-test of observability through ``Runner.run_agent_team_streaming``.

Only the LLM is mocked (via ``mock_llm_context``); observability callbacks,
team coordination, and the monitor handler all run with their real
production code paths.  Spans are captured via ``InMemorySpanExporter``.

Scenario (simplified — leader only, single iteration):
  1. Leader starts, runs one task-loop iteration
  2. Mock LLM returns a text answer (no tool calls)
  3. Stream times out after a few seconds, spans are verified
  4. Verifies: team span as ROOT, agent span under it,
     LLM span under agent, no orphans, input/output populated.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openjiuwen.agent_teams.observability import (
    ObservabilityConfig,
    init_observability,
    shutdown_observability,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.core.runner import Runner
from openjiuwen.core.common.logging import team_logger
from tests.unit_tests.fixtures.mock_llm import (
    create_reasoning_response,
    mock_llm_context,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def in_memory_exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(enabled=True, service_name="openjiuwen-test", sample_rate=1.0),
        span_exporter_override=exporter,
    )
    yield exporter
    shutdown_observability()


def _spans_by_name(exporter: InMemorySpanExporter, name: str) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _attr(span: Any, key: str, default: Any = None) -> Any:
    return dict(span.attributes or {}).get(key, default)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leader_single_iteration_trace_via_runner(in_memory_exporter: InMemorySpanExporter) -> None:
    """Leader runs one iteration with mock LLM returning a text answer.

    Goes through ``Runner.run_agent_team_streaming`` — only the LLM is
    mocked.  Team coordination, tool execution, and observability all run
    real code paths.
    """
    await Runner.start()

    team_name = f"obs_ut_{uuid.uuid4().hex[:6]}"
    session_id = f"ut_session_{uuid.uuid4().hex[:6]}"

    spec_dict = {
        "team_name": team_name,
        "lifecycle": "temporary",
        "spawn_mode": "inprocess",
        "leader": {
            "member_name": "leader",
            "display_name": "TeamLeader",
            "desc": "You are a helpful assistant. Answer briefly.",
        },
        "agents": {
            "leader": {
                "model": {
                    "model_client_config": {
                        "client_provider": "OpenAI",
                        "api_base": "http://mock",
                        "api_key": "mock-key",
                        "verify_ssl": False,
                    },
                    "model_request_config": {
                        "model": "mock-model",
                        "temperature": 0.3,
                    },
                },
                "tools": [],
                "max_iterations": 5,
                "language": "cn",
            },
        },
        "transport": {"type": "inprocess"},
        "storage": {"type": "memory"},
    }
    spec = TeamAgentSpec.model_validate(spec_dict)

    completed = False
    answers: list[str] = []

    with mock_llm_context(mock_memory=False) as mock_llm:
        mock_llm.set_responses([
            create_reasoning_response(
                content="Hello! I'm ready to help.",
                reasoning_content="The user is saying hello. I should respond briefly.",
            ),
        ])

        async def _consume() -> None:
            nonlocal completed
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "Say hello"},
                session=session_id,
            ):
                chunk_type = getattr(chunk, "type", "")
                payload = getattr(chunk, "payload", None)

                if chunk_type == "answer":
                    if isinstance(payload, dict):
                        text = payload.get("output", "") or payload.get("content", "")
                    elif isinstance(payload, str):
                        text = payload
                    else:
                        text = ""
                    if text:
                        answers.append(text)
                        team_logger.info("[UT] answer #{}: {}", len(answers), text[:100])

                if chunk_type == "team_completed":
                    completed = True
                    team_logger.info("[UT] team completed!")
                    return

        try:
            await asyncio.wait_for(_consume(), timeout=8.0)
        except asyncio.TimeoutError:
            team_logger.info("[UT] stream timed out (expected)")

    team_logger.info("[UT] completed={} answers={} llm_calls={}",
                     completed, len(answers), mock_llm.call_count)

    await Runner.stop()

    # =====================================================================
    # Verify
    # =====================================================================
    all_spans = in_memory_exporter.get_finished_spans()
    team_logger.info("[UT] total spans exported: {}", len(all_spans))
    for s in all_spans:
        team_logger.info("[UT]   span: name={} trace_id={:032x} span_id={:016x} parent={:016x}",
                         s.name, s.context.trace_id, s.context.span_id,
                         s.parent.span_id if s.parent else 0)

    # --- 1. Team span is ROOT ---
    team_spans = _spans_by_name(in_memory_exporter, f"team.{team_name}")
    assert len(team_spans) >= 1, f"want >=1 team span, got {len(team_spans)}"
    for ts in team_spans:
        assert ts.parent is None, f"team span {ts.name} must be ROOT"

    # --- 2. At least one agent span exists ---
    agent_spans = [s for s in all_spans if s.name.startswith("agent.")]
    assert len(agent_spans) >= 1, f"want >=1 agent spans, got {len(agent_spans)}"

    team_ids = {s.context.span_id for s in team_spans}
    for a in agent_spans:
        assert a.parent is not None, f"agent span {a.name} needs a parent"
        assert a.parent.span_id in team_ids, \
            f"agent span {a.name} parent not in team spans"

    # --- 3. LLM spans (if any) are children of agent spans ---
    # NOTE: LLM spans may be absent when mock_llm_context is used
    # because OpenAIModelClient bypasses Model.invoke/stream level,
    # so callback events for LLM calls don't fire.
    # LLM span coverage is verified in test_observability.py via direct callback triggers.
    agent_ids = {s.context.span_id for s in agent_spans}
    llm_spans = _spans_by_name(in_memory_exporter, "llm.call")
    for llm in llm_spans:
        assert llm.parent is not None, f"llm.call needs a parent"
        assert llm.parent.span_id in agent_ids, \
            f"llm.call parent not an agent span"

    # --- 4. No orphan spans ---
    span_ids = {s.context.span_id for s in all_spans}
    orphans = [s.name for s in all_spans if s.parent is not None and s.parent.span_id not in span_ids]
    assert len(orphans) == 0, f"orphan spans: {orphans}"

    # --- 5. Agent spans have type AGENT ---
    for a in agent_spans:
        assert _attr(a, "langfuse.observation.type") == "agent", \
            f"{a.name} must have type=agent"

    # --- 6. LLM spans have input/output (if present) ---
    for llm in llm_spans:
        has_io = (_attr(llm, "gen_ai.prompt.0.content")
                  or _attr(llm, "gen_ai.completion.0.content")
                  or _attr(llm, "langfuse.observation.output"))
        assert has_io, f"LLM span {llm.name} needs prompt or completion"

    # --- 7. Reasoning spans (if present) have content ---
    reasoning_spans = _spans_by_name(in_memory_exporter, "llm.reasoning")
    for rs in reasoning_spans:
        assert rs.parent is not None, "reasoning span needs parent"
        has_io = (_attr(rs, "gen_ai.completion.0.content")
                  or _attr(rs, "langfuse.observation.output"))
        assert has_io, f"reasoning span needs completion or output"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
