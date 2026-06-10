# coding: utf-8
"""Tests for _aggregate method."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit, Patch, RawPatch


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


def _make_raw_patch(edits, source_type="failure"):
    return RawPatch(
        patch=Patch(edits=edits),
        source_type=source_type,
    )


class TestRuleDedup:
    @staticmethod
    def test_dedup_identical():
        edits = [
            Edit(op="append", content="a"),
            Edit(op="append", content="a"),
        ]
        result = SkillDocumentOptimizer._rule_dedup_edits(edits)
        assert len(result) == 1

    @staticmethod
    def test_dedup_different():
        edits = [
            Edit(op="append", content="a"),
            Edit(op="replace", content="b", target="c"),
        ]
        result = SkillDocumentOptimizer._rule_dedup_edits(edits)
        assert len(result) == 2

    @staticmethod
    def test_empty():
        assert SkillDocumentOptimizer._rule_dedup_edits([]) == []


class TestAggregate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_patches():
        opt = _make_optimizer()
        result = await opt._aggregate([], "skill")
        assert isinstance(result, Patch)
        assert len(result.edits) == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_small_patches_rule_dedup():
        """<=3 total edits should use rule-based dedup (no LLM call)."""
        opt = _make_optimizer()
        patches = [
            _make_raw_patch([Edit(op="append", content="a")], "failure"),
            _make_raw_patch([Edit(op="replace", content="b", target="c")], "success"),
        ]
        result = await opt._aggregate(patches, "skill")
        assert isinstance(result, Patch)
        assert len(result.edits) == 2
        assert "rule-based" in result.reasoning

    @staticmethod
    @pytest.mark.asyncio
    async def test_dedup_removes_duplicates():
        opt = _make_optimizer()
        patches = [
            _make_raw_patch([Edit(op="append", content="a")], "failure"),
            _make_raw_patch([Edit(op="append", content="a")], "failure"),
        ]
        result = await opt._aggregate(patches, "skill")
        assert len(result.edits) == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_large_patches_uses_llm():
        """More than 3 edits should trigger LLM merge."""
        opt = _make_optimizer()

        merged_response = json.dumps({
            "edits": [
                {"op": "append", "content": "merged1"},
                {"op": "replace", "content": "merged2", "target": "x"},
            ],
            "reasoning": "merged",
        })

        async def mock_invoke(*args, **kwargs):
            return MagicMock(content=merged_response)

        opt._llm.invoke = mock_invoke

        # 5 edits total -> needs LLM merge
        patches = [
            _make_raw_patch([
                Edit(op="append", content="a"),
                Edit(op="append", content="b"),
                Edit(op="append", content="c"),
            ], "failure"),
            _make_raw_patch([
                Edit(op="replace", content="d", target="e"),
                Edit(op="replace", content="f", target="g"),
            ], "success"),
        ]
        result = await opt._aggregate(patches, "skill")
        assert isinstance(result, Patch)
        assert len(result.edits) >= 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_llm_failure_fallback():
        """LLM merge failure should fall back to original edits."""
        opt = _make_optimizer()

        async def mock_invoke(*args, **kwargs):
            raise RuntimeError("LLM error")

        opt._llm.invoke = mock_invoke

        patches = [
            _make_raw_patch([
                Edit(op="append", content="a"),
                Edit(op="append", content="b"),
            ], "failure"),
            _make_raw_patch([
                Edit(op="replace", content="c", target="d"),
                Edit(op="replace", content="e", target="f"),
            ], "success"),
        ]
        result = await opt._aggregate(patches, "skill")
        assert isinstance(result, Patch)
        # Should still have edits (fallback concatenation)
        assert len(result.edits) >= 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_only_failure_patches():
        opt = _make_optimizer()
        patches = [
            _make_raw_patch([Edit(op="append", content="a")], "failure"),
        ]
        result = await opt._aggregate(patches, "skill")
        assert len(result.edits) == 1
        assert result.edits[0].source_type == "failure"

    @staticmethod
    @pytest.mark.asyncio
    async def test_only_success_patches():
        opt = _make_optimizer()
        patches = [
            _make_raw_patch([Edit(op="append", content="a", source_type="success")], "success"),
        ]
        result = await opt._aggregate(patches, "skill")
        assert len(result.edits) == 1
        assert result.edits[0].source_type == "success"
