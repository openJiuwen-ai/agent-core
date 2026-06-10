# coding: utf-8
"""Tests for _format_batch and _format_single trajectory formatting."""

from unittest.mock import MagicMock

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
    _clip_text,
    _extract_content,
    _extract_task_description,
)
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
)


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


class TestClipText:
    @staticmethod
    def test_none():
        assert _clip_text(None, 10) == ""

    @staticmethod
    def test_within_limit():
        assert _clip_text("hello", 10) == "hello"

    @staticmethod
    def test_truncation():
        assert _clip_text("hello world", 5) == "hello"


class TestExtractContent:
    @staticmethod
    def test_dict():
        assert _extract_content({"role": "user", "content": "hi"}) == "hi"

    @staticmethod
    def test_object():
        obj = MagicMock()
        obj.content = "hello"
        assert _extract_content(obj) == "hello"

    @staticmethod
    def test_string():
        assert _extract_content("raw text") == "raw text"


class TestExtractTaskDescription:
    @staticmethod
    def test_task_description_key():
        case = Case(
            inputs={"task_description": "do stuff", "q": "other"},
            label={"a": "x"},
            case_id="c1",
        )
        assert "do stuff" in _extract_task_description(case)

    @staticmethod
    def test_instruction_key():
        case = Case(
            inputs={"instruction": "solve this"},
            label={"a": "x"},
            case_id="c2",
        )
        assert "solve this" in _extract_task_description(case)

    @staticmethod
    def test_fallback_to_full_inputs():
        case = Case(inputs={"foo": "bar"}, label={"a": "x"}, case_id="c3")
        desc = _extract_task_description(case)
        assert "foo" in desc


class TestFormatSingle:
    @staticmethod
    def test_llm_step_with_messages():
        opt = _make_optimizer()
        step = TrajectoryStep(
            kind="llm",
            detail=LLMCallDetail(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "Hello"},
                ],
                response=MagicMock(content="Hi there!"),
            ),
        )
        traj = Trajectory(execution_id="e1", steps=[step])
        case = Case(inputs={"q": "test"}, label={"a": "y"}, case_id="c1")
        eval_case = EvaluatedCase(case=case, score=0.8)

        text = opt._format_single(traj, eval_case, case)
        # System message should be skipped
        assert "You are helpful" not in text
        assert "Hello" in text
        assert "Hi there!" in text

    @staticmethod
    def test_tool_step():
        opt = _make_optimizer()
        step = TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name="search",
                call_args='{"query": "test"}',
                call_result="Found 3 results",
            ),
        )
        traj = Trajectory(execution_id="e2", steps=[step])
        case = Case(inputs={"q": "test"}, label={"a": "y"}, case_id="c2")
        eval_case = EvaluatedCase(case=case, score=0.5)

        text = opt._format_single(traj, eval_case, case)
        assert "[action] search" in text
        assert "Found 3 results" in text

    @staticmethod
    def test_middle_truncation():
        opt = _make_optimizer(max_chars_per_traj=100)
        # Create a very long message
        long_content = "x" * 500
        step = TrajectoryStep(
            kind="llm",
            detail=LLMCallDetail(
                model="gpt-4",
                messages=[{"role": "user", "content": long_content}],
                response=MagicMock(content=long_content),
            ),
        )
        traj = Trajectory(execution_id="e3", steps=[step])
        case = Case(inputs={"q": "test"}, label={"a": "y"}, case_id="c3")
        eval_case = EvaluatedCase(case=case, score=0.5)

        text = opt._format_single(traj, eval_case, case)
        assert "[middle truncated]" in text


class TestFormatBatch:
    @staticmethod
    def test_multiple_trajectories():
        opt = _make_optimizer()
        case1 = Case(inputs={"q": "q1"}, label={"a": "a1"}, case_id="c1")
        case2 = Case(inputs={"q": "q2"}, label={"a": "a2"}, case_id="c2")
        eval1 = EvaluatedCase(case=case1, score=0.9, reason="good")
        eval2 = EvaluatedCase(case=case2, score=0.3, reason="bad")

        traj1 = Trajectory(
            execution_id="e1",
            steps=[TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="m",
                    messages=[{"role": "user", "content": "msg1"}],
                    response=MagicMock(content="r1"),
                ),
            )],
        )
        traj2 = Trajectory(execution_id="e2", steps=[])

        batch = [(traj1, eval1, case1), (traj2, eval2, case2)]
        text = opt._format_batch(batch)

        assert "Trajectory 1" in text
        assert "Trajectory 2" in text
        assert "id=c1" in text
        assert "id=c2" in text
        assert "Score: 0.90" in text
        assert "Score: 0.30" in text
        assert "---" in text  # separator

    @staticmethod
    def test_empty_batch():
        opt = _make_optimizer()
        text = opt._format_batch([])
        assert text == ""
