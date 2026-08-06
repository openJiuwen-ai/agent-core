# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ReActAgent LLM calls must reach the trajectory recorder with their usage.

``ReActAgent._get_llm()`` builds its ``Model`` inline instead of going through
``ModelMgr``, so the model is not tracer-decorated by construction.
``_railed_model_call`` decorates it at call time; without that, no ``llm``
span is emitted and every usage total in ``final_metrics`` stays zero.
"""
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.trajectory import TrajectoryConfig, TrajectoryRecorder, read_trajectory

from tests.unit_tests.core.session.trajectory.helpers import make_react_agent
from tests.unit_tests.fixtures.mock_llm import MockLLMModel

pytestmark = pytest.mark.asyncio

SESSION_ID = "usage-session"


def _response() -> AssistantMessage:
    """An answer carrying the usage numbers we expect to be aggregated."""
    return AssistantMessage(
        content="done",
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="mock-model",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            cache_tokens=80,
            total_cost=0.25,
        ),
    )


async def _record_one_turn(tmp_path, *, streaming: bool = False):
    """Run one agent turn with recording active and return the trajectory.

    Args:
        tmp_path: Trajectory storage root.
        streaming: Drive ``llm.stream()`` instead of ``llm.invoke()``.
    """
    agent = make_react_agent("usage_agent")
    recorder = TrajectoryRecorder(SESSION_ID, TrajectoryConfig(root=str(tmp_path))).attach(agent)
    try:
        mock_llm = MockLLMModel()
        mock_llm.set_responses([_response()])
        # The session must be created *after* attach (tracers pick up
        # extension handlers at session construction time) and must carry
        # the agent's card so the trace matches the attached identity.
        session = create_agent_session(session_id=SESSION_ID, card=agent.card)
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            await agent.invoke(
                {"conversation_id": "c1", "query": "hi"},
                session=session,
                _streaming=streaming,
            )
    finally:
        await recorder.close()

    files = sorted((tmp_path / SESSION_ID).glob("*/trajectory.json"))
    assert files, "no trajectory file was written"
    return read_trajectory(files[0])


async def test_llm_step_and_usage_totals_are_recorded(tmp_path):
    """The agent's LLM call produces an llm step whose usage reaches final_metrics."""
    recorded = await _record_one_turn(tmp_path)
    llm_steps = [s for s in recorded.steps if (s.extra or {}).get("invoke_type") == "llm"]
    assert len(llm_steps) == 1
    assert llm_steps[0].role == "agent"
    assert llm_steps[0].model_name == "mock-model"

    metrics = recorded.final_metrics
    assert metrics is not None
    assert metrics.llm_call_count == 1
    assert metrics.total_input_tokens == 100
    assert metrics.total_output_tokens == 20
    assert metrics.total_tokens == 120
    assert metrics.total_cache_tokens == 80
    assert metrics.total_cost == 0.25


async def test_streaming_path_also_records_usage(tmp_path):
    """The llm.stream() path reports the same usage as llm.invoke().

    Streaming chunks are accumulated by the tracer and handed to on_llm_end
    as a list, so usage is read from the chunk that carries it.
    """
    recorded = await _record_one_turn(tmp_path, streaming=True)
    llm_steps = [s for s in recorded.steps if (s.extra or {}).get("invoke_type") == "llm"]
    assert len(llm_steps) == 1
    assert llm_steps[0].metrics.input_tokens == 100
    assert llm_steps[0].metrics.cache_tokens == 80

    metrics = recorded.final_metrics
    assert metrics.llm_call_count == 1
    assert metrics.total_input_tokens == 100
    assert metrics.total_output_tokens == 20
    assert metrics.total_cache_tokens == 80
    assert metrics.total_cost == 0.25
