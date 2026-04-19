# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_experience_search_tool — ExperienceSearchTool 单元测试。"""

from __future__ import annotations

import tempfile
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.experience.experience_store import (
    ExperienceStore,
)
from openjiuwen.auto_harness.schema import (
    Experience,
    ExperienceType,
)
from openjiuwen.auto_harness.tools.experience_search_tool import (
    ExperienceSearchTool,
)


class TestExperienceSearchTool(IsolatedAsyncioTestCase):
    async def test_search_returns_results(self):
        with tempfile.TemporaryDirectory() as d:
            store = ExperienceStore(d)
            await store.record(Experience(
                type=ExperienceType.OPTIMIZATION,
                topic="ruff-fix",
                summary="fixed lint errors",
                outcome="success",
            ))
            await store.record(Experience(
                type=ExperienceType.FAILURE,
                topic="timeout-bug",
                summary="task timed out",
                outcome="timeout",
            ))

            tool = ExperienceSearchTool(
                experience_dir=d
            )
            result = await tool.invoke({"query": "ruff"})
            assert result.success is True
            assert len(result.data) >= 1
            assert result.data[0]["topic"] == "ruff-fix"

    async def test_search_empty_query(self):
        with tempfile.TemporaryDirectory() as d:
            tool = ExperienceSearchTool(
                experience_dir=d
            )
            result = await tool.invoke({"query": ""})
            assert result.success is False
            assert "空" in result.error

    async def test_search_no_results(self):
        with tempfile.TemporaryDirectory() as d:
            tool = ExperienceSearchTool(
                experience_dir=d
            )
            result = await tool.invoke(
                {"query": "nonexistent"}
            )
            assert result.success is True
            assert result.data == []

    async def test_card_has_correct_name(self):
        with tempfile.TemporaryDirectory() as d:
            tool = ExperienceSearchTool(
                experience_dir=d
            )
            assert tool.card.name == "experience_search"
            assert "ExperienceSearchTool" in tool.card.id

    async def test_stream_yields_invoke_result(self):
        with tempfile.TemporaryDirectory() as d:
            tool = ExperienceSearchTool(
                experience_dir=d
            )
            chunks = []
            async for chunk in tool.stream(
                {"query": "test"},
            ):
                chunks.append(chunk)
            assert len(chunks) == 1
            assert chunks[0].success is True
