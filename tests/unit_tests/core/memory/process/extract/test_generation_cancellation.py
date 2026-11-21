# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType
from openjiuwen.core.memory.process.extract.generation import Generator
from openjiuwen.core.memory.process.extract.memory_analyzer import (
    MemoryAnalyzer,
    MemoryAnalyzerResult,
    VariableResult,
)


def _analyze_result() -> MemoryAnalyzerResult:
    return MemoryAnalyzerResult(
        has_key_information=True,
        variables=[VariableResult(variable_key="姓名", variable_value="Tom")],
        summary="",
    )


def _gen_kwargs() -> dict:
    return dict(
        messages=[BaseMessage(role="user", content="我是Tom")],
        config=AgentMemoryConfig(enable_summary_memory=False),
        base_chat_model=MagicMock(),
        user_id="u1",
        scope_id="s1",
        message_mem_id="1",
        timestamp="2026-01-01 00:00:00",
    )


@pytest.fixture
def generator():
    return Generator(data_id_generator=MagicMock())


@pytest.fixture(autouse=True)
def _mock_analyze():
    with patch.object(MemoryAnalyzer, "analyze", new=AsyncMock(return_value=_analyze_result())):
        yield


class TestGenAllMemoryCancellation:
    @pytest.mark.asyncio
    async def test_task_cancel_during_extraction_propagates(self, generator):
        started = asyncio.Event()
        never = asyncio.Event()

        async def _blocked(**kwargs):
            started.set()
            await never.wait()
            return []

        with patch.object(generator, "_categories_to_memory_unit", side_effect=_blocked):
            task = asyncio.create_task(generator.gen_all_memory(**_gen_kwargs()))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_wait_for_timeout_during_extraction_propagates(self, generator):
        never = asyncio.Event()

        async def _blocked(**kwargs):
            await never.wait()
            return []

        with patch.object(generator, "_categories_to_memory_unit", side_effect=_blocked):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(generator.gen_all_memory(**_gen_kwargs()), timeout=0.05)

    @pytest.mark.asyncio
    async def test_ordinary_exception_still_returns_partial_results(self, generator):
        with patch.object(
                generator, "_categories_to_memory_unit", side_effect=RuntimeError("llm failed")
        ):
            result = await generator.gen_all_memory(**_gen_kwargs())

        variables = result[MemoryType.VARIABLE.value]
        assert len(variables) == 1
        assert variables[0].variable_name == "姓名"
