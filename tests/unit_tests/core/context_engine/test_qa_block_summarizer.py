# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine.processor import base as processor_base
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.summarizer import QABlockSummarizer
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata


@pytest.mark.asyncio
async def test_generate_l1_forwards_llm_usage(monkeypatch):
    usage = UsageMetadata(input_tokens=123, output_tokens=45, total_tokens=168)

    async def fake_invoke_via_stream(*_args, **_kwargs):
        return AssistantMessage(content="summary", usage_metadata=usage)

    monkeypatch.setattr(processor_base, "_invoke_via_stream", fake_invoke_via_stream)
    usage_callback = AsyncMock()
    summarizer = QABlockSummarizer(
        QABlockConfig(
            l1_inline_max_chars=1,
            l1_llm_min_chars=1,
            l1_summary_max_chars=100,
        )
    )
    model = type("StreamingModel", (), {"stream": lambda self: None})()

    result = await summarizer.generate_l1(
        "question",
        "answer",
        model=model,
        usage_callback=usage_callback,
    )

    assert result == ("summary", "compressed")
    usage_callback.assert_awaited_once_with(usage)
