# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for llm_controller stream accumulation of the #6101 correction
chunk: a content-free chunk carrying a normalized finish_reason and metadata
must reach the final AssistantMessage (the consumer-side propagation seam)."""
from types import SimpleNamespace

import pytest

from openjiuwen.core.application.llm_agent.llm_controller import LLMController
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk


class _FakeSession:
    def __init__(self):
        self.outputs = []

    async def write_stream(self, output):
        self.outputs.append(output)


@pytest.mark.asyncio
async def test_correction_chunk_reaches_final_assistant_message():
    async def stream(*args, **kwargs):
        yield AssistantMessageChunk(content="被掐断的半句", finish_reason="null")
        yield AssistantMessageChunk(
            content="",
            finish_reason="length",
            metadata={"finish_reason_detail": "gateway_cut"},
        )

    model = SimpleNamespace(stream=stream)
    session = _FakeSession()
    message = await LLMController._call_llm_get_output(
        SimpleNamespace(),
        model=model,
        model_name="test-model",
        llm_inputs=[],
        tools=None,
        session=session,
    )

    assert message.content == "被掐断的半句"
    assert message.finish_reason == "length"
    assert message.metadata.get("finish_reason_detail") == "gateway_cut"


@pytest.mark.asyncio
async def test_normal_stream_without_correction_chunk_stays_untouched():
    async def stream(*args, **kwargs):
        yield AssistantMessageChunk(content="完整回答。", finish_reason="stop")

    model = SimpleNamespace(stream=stream)
    message = await LLMController._call_llm_get_output(
        SimpleNamespace(),
        model=model,
        model_name="test-model",
        llm_inputs=[],
        tools=None,
        session=_FakeSession(),
    )

    assert message.content == "完整回答。"
    assert message.finish_reason == "stop"
    assert message.metadata.get("finish_reason_detail") is None
