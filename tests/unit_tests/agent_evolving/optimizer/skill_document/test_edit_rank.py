# coding: utf-8
"""Tests for _select (edit ranking) method."""

import json
from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit


def _make_optimizer(**overrides) -> SkillDocumentOptimizer:
    defaults = dict(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=CaseLoader(cases=[
            Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
        ]),
    )
    defaults.update(overrides)
    return SkillDocumentOptimizer(**defaults)


class TestSelect:
    @staticmethod
    @pytest.mark.asyncio
    async def test_within_budget_returns_unchanged():
        opt = _make_optimizer()
        edits = [
            Edit(op="append", content="a"),
            Edit(op="replace", content="b", target="c"),
        ]
        result = await opt._select(edits, budget=10, skill_content="skill")
        assert result == edits

    @staticmethod
    @pytest.mark.asyncio
    async def test_exactly_at_budget_returns_unchanged():
        opt = _make_optimizer()
        edits = [Edit(op="append", content=f"edit_{i}") for i in range(5)]
        result = await opt._select(edits, budget=5, skill_content="skill")
        assert result == edits
        assert len(result) == 5

    @staticmethod
    @pytest.mark.asyncio
    async def test_over_budget_uses_llm():
        opt = _make_optimizer()

        ranking_response = json.dumps({
            "selected_indices": [2, 0, 4],
        })

        async def mock_invoke(*args, **kwargs):
            return MagicMock(content=ranking_response)

        opt._llm.invoke = mock_invoke

        edits = [Edit(op="append", content=f"edit_{i}") for i in range(6)]
        result = await opt._select(edits, budget=3, skill_content="skill")
        assert len(result) == 3
        # Should be in the order specified by LLM
        assert result[0] == edits[2]
        assert result[1] == edits[0]
        assert result[2] == edits[4]

    @staticmethod
    @pytest.mark.asyncio
    async def test_llm_failure_fallback_truncation():
        opt = _make_optimizer()

        async def mock_invoke(*args, **kwargs):
            raise RuntimeError("LLM error")

        opt._llm.invoke = mock_invoke

        edits = [Edit(op="append", content=f"edit_{i}") for i in range(10)]
        result = await opt._select(edits, budget=3, skill_content="skill")
        assert len(result) == 3
        # Fallback: simple truncation to first N
        assert result[0] == edits[0]

    @staticmethod
    @pytest.mark.asyncio
    async def test_llm_invalid_indices_fallback():
        opt = _make_optimizer()

        ranking_response = json.dumps({
            "selected_indices": [99, -1, "bad"],
        })

        async def mock_invoke(*args, **kwargs):
            return MagicMock(content=ranking_response)

        opt._llm.invoke = mock_invoke

        edits = [Edit(op="append", content=f"edit_{i}") for i in range(5)]
        result = await opt._select(edits, budget=3, skill_content="skill")
        # Invalid indices -> fallback truncation
        assert len(result) == 3

    @staticmethod
    @pytest.mark.asyncio
    async def test_dedup_indices():
        opt = _make_optimizer()

        ranking_response = json.dumps({
            "selected_indices": [0, 0, 1, 1, 2],
        })

        async def mock_invoke(*args, **kwargs):
            return MagicMock(content=ranking_response)

        opt._llm.invoke = mock_invoke

        edits = [Edit(op="append", content=f"edit_{i}") for i in range(5)]
        result = await opt._select(edits, budget=3, skill_content="skill")
        # Duplicates should be filtered
        assert len(result) == 3
        assert result[0] == edits[0]
        assert result[1] == edits[1]
        assert result[2] == edits[2]
