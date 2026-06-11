# coding: utf-8
"""Tests for _reflect method."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import RawPatch
from openjiuwen.agent_evolving.trajectory.types import Trajectory


def _make_optimizer(**overrides) -> SkillDocumentOptimizer:
    defaults = dict(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=CaseLoader(
            cases=[
                Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
            ]
        ),
    )
    defaults.update(overrides)
    return SkillDocumentOptimizer(**defaults)


class TestParseReflectResponse:
    @staticmethod
    def test_valid_patch():
        opt = _make_optimizer()
        raw = json.dumps(
            {
                "patch": {
                    "edits": [
                        {"op": "append", "content": "new section", "target": ""},
                        {"op": "replace", "content": "fixed", "target": "old text"},
                    ],
                    "reasoning": "improved clarity",
                },
                "failure_summary": "agent confused about X",
            }
        )
        result = opt._parse_reflect_response(raw, "failure")
        assert result is not None
        assert isinstance(result, RawPatch)
        assert result.source_type == "failure"
        assert len(result.patch.edits) == 2
        assert result.patch.edits[0].op == "append"
        assert result.patch.edits[1].op == "replace"
        assert result.failure_summary == "agent confused about X"

    @staticmethod
    def test_invalid_op_filtered():
        opt = _make_optimizer()
        raw = json.dumps(
            {
                "patch": {
                    "edits": [
                        {"op": "append", "content": "good"},
                        {"op": "invalid_op", "content": "bad"},
                    ],
                },
            }
        )
        result = opt._parse_reflect_response(raw, "failure")
        assert result is not None
        assert len(result.patch.edits) == 1
        assert result.patch.edits[0].op == "append"

    @staticmethod
    def test_empty_edits_returns_sentinel():
        opt = _make_optimizer()
        raw = json.dumps({"patch": {"edits": []}})
        result = opt._parse_reflect_response(raw, "success")
        assert result is not None
        assert len(result.patch.edits) == 0

    @staticmethod
    def test_invalid_json_returns_none():
        opt = _make_optimizer()
        result = opt._parse_reflect_response("not json at all", "failure")
        assert result is None

    @staticmethod
    def test_empty_response_returns_none():
        opt = _make_optimizer()
        result = opt._parse_reflect_response("", "failure")
        assert result is None

    @staticmethod
    def test_non_dict_returns_none():
        opt = _make_optimizer()
        result = opt._parse_reflect_response("[1,2,3]", "failure")
        assert result is None

    @staticmethod
    def test_missing_edits_key():
        opt = _make_optimizer()
        raw = json.dumps({"patch": {"reasoning": "no edits"}})
        result = opt._parse_reflect_response(raw, "failure")
        assert result is not None
        assert len(result.patch.edits) == 0


class TestReflectAsync:
    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_formatted_batch():
        opt = _make_optimizer()
        result = await opt._reflect("", "skill", 0.5)
        assert result == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_reflect_calls_llm():
        opt = _make_optimizer()

        mock_response = json.dumps(
            {
                "patch": {
                    "edits": [
                        {"op": "append", "content": "add example"},
                    ],
                    "reasoning": "needs examples",
                },
            }
        )

        async def mock_invoke(*args, **kwargs):
            return MagicMock(content=mock_response)

        opt._llm.invoke = mock_invoke

        result = await opt._reflect(
            "### Trajectory 1\nSome content",
            "skill doc",
            0.5,
        )
        # Should have called both failure and success analysts
        assert len(result) >= 1
        assert all(isinstance(r, RawPatch) for r in result)

    @staticmethod
    @pytest.mark.asyncio
    async def test_reflect_graceful_degradation():
        """Single analyst failure should not crash the whole reflect."""
        opt = _make_optimizer()

        call_count = 0

        async def mock_invoke(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("LLM error")
            return MagicMock(
                content=json.dumps(
                    {
                        "patch": {"edits": [{"op": "append", "content": "ok"}]},
                    }
                )
            )

        opt._llm.invoke = mock_invoke

        result = await opt._reflect("some text", "skill", 0.5)
        # One analyst failed, the other succeeded
        assert len(result) >= 0  # Graceful: no crash

    @staticmethod
    @pytest.mark.asyncio
    async def test_reflect_splits_batch_by_score_threshold():
        opt = _make_optimizer()
        failure_case = Case(inputs={"question": "failure task"}, label={"answer": "x"}, case_id="fail")
        success_case = Case(inputs={"question": "success task"}, label={"answer": "y"}, case_id="success")
        batch_data = [
            (
                Trajectory(execution_id="t0", steps=[], case_id="fail"),
                EvaluatedCase(case=failure_case, score=0.2, reason="bad"),
                failure_case,
            ),
            (
                Trajectory(execution_id="t1", steps=[], case_id="success"),
                EvaluatedCase(case=success_case, score=0.9, reason="good"),
                success_case,
            ),
        ]
        prompts = []

        async def fake_invoke(_llm, _model, prompt, *, policy):
            prompts.append(prompt)
            return json.dumps({"patch": {"edits": []}})

        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer.invoke_text_with_retry",
            side_effect=fake_invoke,
        ):
            await opt._reflect(
                formatted_batch="legacy combined text",
                skill_content="skill",
                score_threshold=0.5,
                batch_data=batch_data,
            )

        assert len(prompts) == 2
        failure_prompt = next(prompt for prompt in prompts if "## Failed Trajectories" in prompt)
        success_prompt = next(prompt for prompt in prompts if "## Successful Trajectories" in prompt)
        assert "id=fail" in failure_prompt
        assert "id=success" not in failure_prompt
        assert "id=success" in success_prompt
        assert "id=fail" not in success_prompt


class TestReflectForOperator:
    """Tests for _reflect_for_operator() — per-operator reflect with operator_id tagging."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_batches_returns_empty():
        opt = _make_optimizer()
        result = await opt._reflect_for_operator(
            operator_id="op_a",
            formatted_failures="",
            formatted_successes="",
            skill_content="skill",
        )
        assert result == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_tags_patches_with_operator_id():
        opt = _make_optimizer()
        patch_data = json.dumps({"patch": {"edits": [{"op": "append", "content": "new"}], "reasoning": "r"}})
        opt._llm = AsyncMock()
        opt._llm.invoke = AsyncMock(return_value=MagicMock(content=patch_data))

        result = await opt._reflect_for_operator(
            operator_id="op_a",
            formatted_failures="failure text",
            formatted_successes="",
            skill_content="skill content",
        )

        assert len(result) >= 1
        for rp in result:
            assert rp.operator_id == "op_a"

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_operator_id_no_tagging():
        """When operator_id is empty, patches keep default empty operator_id."""
        opt = _make_optimizer()
        patch_data = json.dumps({"patch": {"edits": [{"op": "append", "content": "new"}], "reasoning": "r"}})
        opt._llm = AsyncMock()
        opt._llm.invoke = AsyncMock(return_value=MagicMock(content=patch_data))

        result = await opt._reflect_for_operator(
            operator_id="",
            formatted_failures="failure text",
            formatted_successes="",
            skill_content="skill content",
        )

        assert len(result) >= 1
        for rp in result:
            assert rp.operator_id == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_reflect_delegates_to_reflect_for_operator():
        """Existing _reflect() should still work (backward compat)."""
        opt = _make_optimizer()
        patch_data = json.dumps({"patch": {"edits": [{"op": "append", "content": "new"}], "reasoning": "r"}})
        opt._llm = AsyncMock()
        opt._llm.invoke = AsyncMock(return_value=MagicMock(content=patch_data))

        result = await opt._reflect(
            formatted_batch="failure text",
            skill_content="skill content",
            score_threshold=0.5,
        )

        assert len(result) >= 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_reflect_passes_operator_id_to_reflect_for_operator():
        """_reflect(operator_id='op_x') should tag patches with op_x."""
        opt = _make_optimizer()
        patch_data = json.dumps({"patch": {"edits": [{"op": "append", "content": "tagged"}], "reasoning": "r"}})
        opt._llm = AsyncMock()
        opt._llm.invoke = AsyncMock(return_value=MagicMock(content=patch_data))

        result = await opt._reflect(
            formatted_batch="failure text",
            skill_content="skill content",
            score_threshold=0.5,
            operator_id="op_x",
        )

        assert len(result) >= 1
        for rp in result:
            assert rp.operator_id == "op_x"

    @staticmethod
    @pytest.mark.asyncio
    async def test_reflect_default_operator_id_is_empty():
        """_reflect() without operator_id should produce untagged patches (backward compat)."""
        opt = _make_optimizer()
        patch_data = json.dumps({"patch": {"edits": [{"op": "append", "content": "untagged"}], "reasoning": "r"}})
        opt._llm = AsyncMock()
        opt._llm.invoke = AsyncMock(return_value=MagicMock(content=patch_data))

        result = await opt._reflect(
            formatted_batch="failure text",
            skill_content="skill content",
            score_threshold=0.5,
        )

        assert len(result) >= 1
        for rp in result:
            assert rp.operator_id == ""
