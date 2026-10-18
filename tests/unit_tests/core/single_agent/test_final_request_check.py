# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReActAgent final request check: an optional per-instance hook on each final model request."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig, SystemMessage, UserMessage
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentRail
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response

FINISH = {"result_type": "stopped_before_model", "output": ""}


def make_agent():
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    config = ReActAgentConfig(
        model_config_obj=ModelRequestConfig(model="gpt-3.5-turbo"),
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="mock_key", api_base="mock_url", timeout=30, verify_ssl=False
        ),
        prompt_template=[{"role": "system", "content": "你是一个助手。"}],
    )
    return ReActAgent(card=AgentCard(description="final request check")).configure(config)


class Check:
    """Records the final request it sees and returns a fixed decision."""

    def __init__(self, result=None, error=None):
        self.seen = []
        self.result = result
        self.error = error

    async def __call__(self, ctx):
        self.seen.append((list(ctx.inputs.messages), ctx.inputs.tools))
        if self.error is not None:
            raise self.error
        return self.result


def model(*responses):
    llm = MockLLMModel()
    llm.set_responses(list(responses) or [create_text_response("ok")])
    return llm


async def invoke(agent, llm):
    with patch.object(agent, "_get_llm", return_value=llm):
        return await agent.invoke({"query": "hello"})


@pytest.mark.asyncio
async def test_without_a_check_the_model_is_called_as_before():
    agent = make_agent()
    assert agent._final_request_check is None
    llm = model()
    result = await invoke(agent, llm)
    assert result["result_type"] == "answer" and llm.call_count == 1


@pytest.mark.asyncio
async def test_a_passing_check_sees_the_final_request_then_the_model_is_called():
    agent = make_agent()
    check = Check()
    agent.set_final_request_check(check)
    llm = model()
    result = await invoke(agent, llm)
    assert result["result_type"] == "answer" and llm.call_count == 1
    ((messages, _tools),) = check.seen
    assert isinstance(messages[0], SystemMessage) and "你是一个助手" in messages[0].content, "the final system prompt"
    assert messages[-1].content == "hello"


@pytest.mark.asyncio
async def test_a_check_can_replace_context_and_rebuild_the_same_final_request_once():
    agent = make_agent()
    seen = []

    async def rebuild_once(ctx):
        seen.append(list(ctx.inputs.messages))
        if len(seen) == 1:
            ctx.context.set_messages([UserMessage(content="replacement request")])
            ctx.request_context_rebuild()
            assert ctx.has_context_rebuild_request is True
        return None

    agent.set_final_request_check(rebuild_once)
    llm = model()

    result = await invoke(agent, llm)

    assert result["result_type"] == "answer" and llm.call_count == 1
    assert len(seen) == 2
    assert seen[0][-1].content == "hello"
    assert seen[1][-1].content == "replacement request"


@pytest.mark.asyncio
async def test_a_finishing_check_ends_the_round_without_the_model_retry_or_recovery():
    agent = make_agent()
    agent.set_final_request_check(Check(result=FINISH))
    recover = AsyncMock(return_value=True)
    agent._recover_from_model_exception = recover
    exceptions = []

    class Watch(AgentRail):
        async def on_model_exception(self, ctx):
            exceptions.append(ctx.exception)

    await agent.register_rail(Watch())
    llm = model()
    result = await invoke(agent, llm)
    assert result["result_type"] == "stopped_before_model"
    assert llm.call_count == 0, "the provider is never called"
    assert exceptions == [] and recover.await_count == 0, "an expected finish is not an exception"


@pytest.mark.asyncio
async def test_every_rail_retry_builds_and_checks_the_request_again():
    agent = make_agent()
    check = Check()
    agent.set_final_request_check(check)

    class RetryOnce(AgentRail):
        async def on_model_exception(self, ctx):
            if ctx.retry_attempt < 1:
                ctx.request_retry()

    await agent.register_rail(RetryOnce())
    llm = model(create_text_response("ok"))
    calls = {"count": 0}
    original = llm.invoke

    async def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise build_error(StatusCode.AGENT_CONTROLLER_INVOKE_CALL_FAILED, error_msg="failed once")
        return await original(*args, **kwargs)

    llm.invoke = flaky
    result = await invoke(agent, llm)
    assert result["result_type"] == "answer" and calls["count"] == 2
    assert len(check.seen) == 2, "the retried attempt is checked too"


@pytest.mark.asyncio
async def test_a_recovery_retry_is_checked_again_and_can_be_finished():
    agent = make_agent()
    decisions = [None, FINISH]
    seen = []

    async def check(ctx):
        seen.append(ctx.inputs.messages)
        return decisions[len(seen) - 1]

    agent.set_final_request_check(check)
    agent._recover_from_model_exception = AsyncMock(return_value=True)
    llm = model()

    async def failing(*args, **kwargs):
        raise build_error(StatusCode.AGENT_CONTROLLER_INVOKE_CALL_FAILED, error_msg="context too long")

    llm.invoke = failing
    result = await invoke(agent, llm)
    assert len(seen) == 2 and result["result_type"] == "stopped_before_model"


@pytest.mark.asyncio
async def test_cancellation_in_the_check_propagates_and_the_model_is_not_called():
    agent = make_agent()
    agent.set_final_request_check(Check(error=asyncio.CancelledError()))
    llm = model()
    with pytest.raises(asyncio.CancelledError):
        await invoke(agent, llm)
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_a_streamed_call_is_checked_before_the_provider_stream():
    agent = make_agent()
    agent.set_final_request_check(Check(result=FINISH))
    llm = model()
    with patch.object(agent, "_get_llm", return_value=llm):
        chunks = [chunk async for chunk in agent.stream({"query": "hello"})]
    assert llm.call_count == 0
    answers = [chunk.payload for chunk in chunks if getattr(chunk, "type", None) == "answer"]
    assert answers == [FINISH], "the stream ends with the check's result"


@pytest.mark.asyncio
async def test_removing_the_check_restores_the_plain_call():
    agent = make_agent()
    agent.set_final_request_check(Check(result=FINISH))
    agent.set_final_request_check(None)
    llm = model()
    result = await invoke(agent, llm)
    assert result["result_type"] == "answer" and llm.call_count == 1
