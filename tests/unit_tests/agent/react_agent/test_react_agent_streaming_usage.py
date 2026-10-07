# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Streaming provider errors retain their reported usage before rail retries."""

from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.errors import FrameworkError
from openjiuwen.core.foundation.llm import UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail, ModelCallInputs
from tests.unit_tests.agent.react_agent.test_react_agent_streaming import _FakeContext, _FakeSession, _make_agent


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason,code", [("error", 504), ("failed", 0), ("stop", 504)])
@pytest.mark.parametrize("retry", [False, True])
async def test_streaming_emits_failed_response_usage_before_exception(finish_reason, code, retry):
    """Retain reported usage once per attempt, including errors before a retry."""
    events = []
    written_frames = []
    call_count = 0

    class CapturingSession(_FakeSession):
        async def write_stream(self, frame):
            written_frames.append(frame)
            if frame.type == "llm_usage":
                events.append(("usage", frame.payload["usage_metadata"]["total_tokens"]))

    class RetryRail(AgentRail):
        async def on_model_exception(self, ctx):
            events.append(("exception", ctx.retry_attempt))
            if retry and ctx.retry_attempt < 2:
                ctx.request_retry()

    async def streaming_chunks(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("synthetic failure without usage")
        yield AssistantMessageChunk(
            content="" if call_count == 1 else "done",
            finish_reason=finish_reason if call_count == 1 else "stop",
            usage_metadata=UsageMetadata(
                input_tokens=7 if call_count == 1 else 3,
                output_tokens=2,
                total_tokens=9 if call_count == 1 else 5,
                code=code if call_count == 1 else 0,
            ),
        )

    await Runner.start()
    try:
        agent = _make_agent("streaming-failed-usage")
        await agent.register_rail(RetryRail())
        ctx = AgentCallbackContext(
            agent=agent,
            session=CapturingSession(),
            context=_FakeContext(),
            inputs=ModelCallInputs(messages=[], tools=[]),
            extra={"_streaming": True},
        )
        with patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=streaming_chunks):
            if retry:
                result = await agent._railed_model_call(ctx)
                assert result.content == "done"
                assert call_count == 3
                assert events == [("usage", 9), ("exception", 0), ("exception", 1), ("usage", 5)]
            else:
                with pytest.raises(FrameworkError):
                    await agent._railed_model_call(ctx)
                assert call_count == 1
                assert events == [("usage", 9), ("exception", 0)]
        usage_frames = [frame for frame in written_frames if frame.type == "llm_usage"]
        for frame in usage_frames:
            assert frame.payload["total_latency_ms"] >= 0
        assert usage_frames[0].payload["usage_metadata"]["code"] == code
    finally:
        await Runner.stop()
