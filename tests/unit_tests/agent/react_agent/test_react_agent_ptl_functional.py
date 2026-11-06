# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end functional coverage for the PTL fallback.

Unlike the unit tests that call the engine hook directly, these cases run the
real ReAct loop: a fake provider rejects the second turn's first request with
a prompt-too-long error, the engine truncates, and the same turn is retried.
"""

from unittest.mock import patch

import pytest

from openjiuwen.core.context_engine import (
    ContextEngine,
    ContextEngineConfig,
    PtlFallbackConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.unit_tests.fixtures.mock_llm import MockLLMModel

PTL_MESSAGE = "This model's maximum context length is 3000 tokens."


class _PtlProbeLLM(MockLLMModel):
    """Records every request and rejects a configurable number of calls."""

    def __init__(self):
        super().__init__()
        self.requests = []
        self.fail_next = 0

    async def invoke(self, messages, **kwargs):
        self.requests.append(list(messages))
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError(PTL_MESSAGE)
        return AssistantMessage(content="final answer")


def _agent(ptl_config: PtlFallbackConfig) -> ReActAgent:
    agent = ReActAgent(card=AgentCard(name="ptl_e2e", description="ptl e2e"))
    agent.context_engine = ContextEngine(
        ContextEngineConfig(
            ptl_fallback_config=ptl_config,
            enable_context_debug=False,
        )
    )
    return agent


def _content_text(message) -> str:
    return str(getattr(message, "content", "") or "")


@pytest.mark.asyncio
async def test_ptl_fallback_recovers_the_turn_and_keeps_the_current_question():
    agent = _agent(
        PtlFallbackConfig(
            enabled=True,
            phase_a_compress=False,
            archive_dropped_rounds=False,
            max_retries=3,
        )
    )
    llm = _PtlProbeLLM()

    with patch.object(agent, "_get_llm", return_value=llm):
        first = await agent.invoke({"conversation_id": "ptl_e2e", "query": "first turn " + "x" * 12000})
        assert first["result_type"] == "answer"

        requests_before = len(llm.requests)
        llm.fail_next = 1
        second = await agent.invoke({"conversation_id": "ptl_e2e", "query": "second question"})

    assert second["result_type"] == "answer"
    assert len(llm.requests) == requests_before + 2, "PTL turn must be retried exactly once"

    rejected = llm.requests[requests_before]
    retried = llm.requests[requests_before + 1]

    assert sum(len(_content_text(m)) for m in retried) < sum(len(_content_text(m)) for m in rejected)
    assert any("[CONTEXT_TRUNCATED]" in _content_text(m) for m in retried)
    assert any("second question" in _content_text(m) for m in retried)
    assert not any("first turn" in _content_text(m) for m in retried)


@pytest.mark.asyncio
async def test_ptl_fallback_gives_up_when_nothing_left_to_truncate():
    agent = _agent(
        PtlFallbackConfig(
            enabled=True,
            phase_a_compress=False,
            archive_dropped_rounds=False,
            max_retries=3,
        )
    )
    llm = _PtlProbeLLM()

    with patch.object(agent, "_get_llm", return_value=llm):
        await agent.invoke({"conversation_id": "ptl_budget", "query": "first turn " + "x" * 12000})
        requests_before = len(llm.requests)
        llm.fail_next = 99
        with pytest.raises(RuntimeError, match="maximum context length"):
            await agent.invoke({"conversation_id": "ptl_budget", "query": "second question"})

    # Initial call plus one successful truncation retry. The retry still fails
    # and the second recovery attempt finds nothing left to drop, so the
    # original provider error is surfaced instead of spinning the full budget.
    assert len(llm.requests) == requests_before + 2
    assert any("[CONTEXT_TRUNCATED]" in _content_text(m) for m in llm.requests[-1])
    assert any("second question" in _content_text(m) for m in llm.requests[-1])


@pytest.mark.asyncio
async def test_disabled_fallback_does_not_retry_the_turn():
    agent = _agent(
        PtlFallbackConfig(
            enabled=False,
            archive_dropped_rounds=False,
        )
    )
    llm = _PtlProbeLLM()

    with patch.object(agent, "_get_llm", return_value=llm):
        await agent.invoke({"conversation_id": "ptl_disabled", "query": "first turn " + "x" * 12000})
        requests_before = len(llm.requests)
        llm.fail_next = 1
        with pytest.raises(RuntimeError, match="maximum context length"):
            await agent.invoke({"conversation_id": "ptl_disabled", "query": "second question"})

    assert len(llm.requests) == requests_before + 1
