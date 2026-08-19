# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the standalone review-feedback attributor."""

import json
from typing import Any

import pytest

from openjiuwen.agent_evolving.signal.base import EvolutionTarget
from openjiuwen.agent_evolving.signal.review_feedback import (
    ReviewFeedbackAction,
    ReviewFeedbackAttribution,
    ReviewFeedbackAttributor,
    ReviewFeedbackClassification,
    ReviewFeedbackContext,
    ReviewFeedbackContextBuilder,
    attribution_to_evolution_signal,
)
from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv


def _tool_trajectory(
    execution_id: str,
    calls: list[tuple[str, dict[str, Any], Any]],
    *,
    session_id: str = "session-1",
) -> Trajectory:
    spans = []
    for index, (tool_name, tool_input, tool_output) in enumerate(calls):
        spans.append(
            {
                "traceId": execution_id,
                "spanId": f"tool-{index}",
                "name": f"tool.{tool_name}",
                "attributes": attributes_from_map(
                    {
                        semconv.GEN_AI_TOOL_NAME: tool_name,
                        semconv.GEN_AI_TOOL_INPUT: tool_input,
                        semconv.GEN_AI_TOOL_OUTPUT: tool_output,
                    }
                ),
            }
        )
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {TRAJECTORY_ID: execution_id, SESSION_ID: session_id}
                        )
                    },
                    "scopeSpans": [{"scope": {}, "spans": spans}],
                }
            ]
        }
    )


class _FakeLLM:
    def __init__(self, payload: dict[str, Any] | str) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return {"content": content}


def _attributor(payload: dict[str, Any] | str) -> tuple[ReviewFeedbackAttributor, _FakeLLM]:
    llm = _FakeLLM(payload)
    return ReviewFeedbackAttributor(llm=llm, model="test-model"), llm


@pytest.mark.asyncio
async def test_empty_feedback_fails_closed_without_calling_llm() -> None:
    attributor, llm = _attributor({})

    result = await attributor.attribute("   ")

    assert result.action == ReviewFeedbackAction.SKIP_UNATTRIBUTED
    assert result.is_skill_actionable is False
    assert llm.calls == []


@pytest.mark.asyncio
async def test_proven_skill_issue_can_target_existing_skill() -> None:
    attributor, llm = _attributor(
        {
            "classification": "skill_issue",
            "skill_name": "xlsx",
            "target": "body",
            "reason": "the workflow omits output validation",
            "reusable_guidance": "Validate formulas and frozen panes before saving.",
            "is_reusable": True,
            "confidence": 0.91,
        }
    )
    context = ReviewFeedbackContext(
        task_id="task-1",
        review_round=2,
        task_objective="Create a financial workbook",
        skill_reads=("xlsx",),
        skill_contents={"xlsx": "Current spreadsheet instructions"},
    )

    result = await attributor.attribute("公式引用错误，且没有冻结表头。", context=context)

    assert result.action == ReviewFeedbackAction.EVOLVE_EXISTING_SKILL
    assert result.classification == ReviewFeedbackClassification.SKILL_ISSUE
    assert result.is_skill_actionable is True
    assert result.skill_name == "xlsx"
    assert result.target == EvolutionTarget.BODY
    assert result.confidence == pytest.approx(0.91)
    assert "xlsx" in llm.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_no_skill_read_forbids_existing_skill_evolution() -> None:
    attributor, _ = _attributor(
        {
            "classification": "skill_issue",
            "skill_name": "xlsx",
            "target": "body",
            "reason": "xlsx should change",
            "reusable_guidance": "Add validation.",
            "is_reusable": True,
            "confidence": 0.99,
        }
    )

    result = await attributor.attribute("公式有错误")

    assert result.action == ReviewFeedbackAction.SKIP_UNATTRIBUTED
    assert result.skill_name is None
    assert result.target is None
    assert "SKILL.md" in result.reason


@pytest.mark.asyncio
async def test_hallucinated_unread_skill_is_rejected() -> None:
    attributor, _ = _attributor(
        {
            "classification": "skill_issue",
            "skill_name": "pdf",
            "target": "body",
            "reason": "change pdf",
            "reusable_guidance": "Add a check.",
            "is_reusable": True,
            "confidence": 0.8,
        }
    )

    result = await attributor.attribute(
        "Workbook validation failed",
        context=ReviewFeedbackContext(skill_reads=("xlsx",)),
    )

    assert result.action == ReviewFeedbackAction.SKIP_UNATTRIBUTED
    assert result.skill_name is None
    assert "not backed" in result.reason


@pytest.mark.asyncio
async def test_executor_error_is_recorded_without_skill_change() -> None:
    attributor, _ = _attributor(
        {
            "classification": "executor_error",
            "skill_name": "xlsx",
            "target": None,
            "reason": "the Skill required validation but the executor skipped it",
            "reusable_guidance": "",
            "is_reusable": False,
            "confidence": 0.87,
        }
    )

    result = await attributor.attribute(
        "The required validation command was never run.",
        context=ReviewFeedbackContext(skill_reads=("xlsx",)),
    )

    assert result.action == ReviewFeedbackAction.RECORD_TASK_FAILURE
    assert result.should_record_task_failure is True
    assert result.is_skill_actionable is False
    assert result.skill_name is None


@pytest.mark.asyncio
async def test_repeated_reusable_pattern_suggests_new_skill_only() -> None:
    attributor, _ = _attributor(
        {
            "classification": "new_skill_pattern",
            "skill_name": "",
            "target": None,
            "reason": "the same release checklist recurs across tasks",
            "reusable_guidance": "Create a reusable release-validation workflow.",
            "is_reusable": True,
            "confidence": 0.78,
        }
    )
    context = ReviewFeedbackContext(
        repetition_count=3,
        repeated_pattern_evidence=("task-a release checks", "task-b release checks"),
    )

    result = await attributor.attribute("每次发布都遗漏相同的检查步骤。", context=context)

    assert result.action == ReviewFeedbackAction.SUGGEST_NEW_SKILL
    assert result.should_create_skill is True
    assert result.is_skill_actionable is False
    assert result.skill_name is None


@pytest.mark.asyncio
async def test_one_off_pattern_does_not_suggest_new_skill() -> None:
    attributor, _ = _attributor(
        {
            "classification": "new_skill_pattern",
            "skill_name": "",
            "target": None,
            "reason": "possibly reusable",
            "reusable_guidance": "Create a workflow.",
            "is_reusable": True,
            "confidence": 0.7,
        }
    )

    result = await attributor.attribute(
        "This one task needed a special release format.",
        context=ReviewFeedbackContext(repetition_count=1),
    )

    assert result.action == ReviewFeedbackAction.SKIP_UNATTRIBUTED
    assert result.should_create_skill is False
    assert result.classification == ReviewFeedbackClassification.NEW_SKILL_PATTERN
    assert result.reusable_guidance == "Create a workflow."
    assert "repeated reusable evidence is insufficient" in result.reason


@pytest.mark.asyncio
async def test_malformed_model_output_fails_closed() -> None:
    attributor, _ = _attributor("not json")

    result = await attributor.attribute(
        "The output is incomplete.",
        context=ReviewFeedbackContext(skill_reads=("documents",)),
    )

    assert result.action == ReviewFeedbackAction.SKIP_UNATTRIBUTED
    assert result.is_skill_actionable is False
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_context_builder_only_accepts_concrete_skill_read(tmp_path) -> None:
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# XLSX\nValidate output.\n", encoding="utf-8")
    trajectory = _tool_trajectory(
        "exec-1",
        [
            (
                "read_file",
                {"path": str(skill_dir / "SKILL.md")},
                "# XLSX",
            )
        ],
        session_id="session-1",
    )

    context = await ReviewFeedbackContextBuilder(store=EvolutionStore(str(tmp_path))).build(
        task_id="task-1",
        review_round=2,
        task_objective="Create workbook",
        trajectory=trajectory,
    )

    assert context.skill_reads == ("xlsx",)
    assert context.skill_contents["xlsx"].startswith("# XLSX")
    assert "read_file" in context.trajectory_excerpt


@pytest.mark.asyncio
async def test_context_builder_does_not_treat_installed_skill_as_read(tmp_path) -> None:
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# XLSX\n", encoding="utf-8")
    trajectory = _tool_trajectory(
        "exec-2",
        [("read_file", {"path": "README.md"}, None)],
    )

    context = await ReviewFeedbackContextBuilder(store=EvolutionStore(str(tmp_path))).build(
        trajectory=trajectory,
    )

    assert context.skill_reads == ()
    assert context.skill_contents == {}


@pytest.mark.asyncio
async def test_context_builder_accepts_only_skill_tool_as_named_skill_read(tmp_path) -> None:
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# XLSX\n", encoding="utf-8")
    trajectory = _tool_trajectory(
        "trace-skill-tools",
        [
            ("evolve_skill_experiences", {"skill_name": "xlsx"}, "saved"),
            ("tools.skill_tool", {"skill_name": "xlsx"}, "# XLSX"),
        ],
    )

    context = await ReviewFeedbackContextBuilder(store=EvolutionStore(str(tmp_path))).build(
        trajectory=trajectory,
    )

    assert context.skill_reads == ("xlsx",)


@pytest.mark.asyncio
async def test_context_builder_rejects_other_skill_named_tools_as_read(tmp_path) -> None:
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# XLSX\n", encoding="utf-8")
    trajectory = _tool_trajectory(
        "trace-non-reader-skill-tool",
        [("evolve_skill_experiences", {"skill_name": "xlsx"}, "saved")],
    )

    context = await ReviewFeedbackContextBuilder(store=EvolutionStore(str(tmp_path))).build(
        trajectory=trajectory,
    )

    assert context.skill_reads == ()


@pytest.mark.asyncio
async def test_context_builder_rejects_write_to_skill_md_as_read_evidence(tmp_path) -> None:
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# xlsx\n", encoding="utf-8")
    trajectory = _tool_trajectory(
        "trace-write",
        [
            (
                "write_file",
                {"path": str(skill_dir / "SKILL.md"), "content": "replacement"},
                "ok",
            )
        ],
    )

    context = await ReviewFeedbackContextBuilder(store=EvolutionStore(str(tmp_path))).build(trajectory=trajectory)

    assert context.skill_reads == ()


@pytest.mark.asyncio
async def test_context_builder_accepts_explicit_shell_read_of_skill_md(tmp_path) -> None:
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# xlsx\n", encoding="utf-8")
    trajectory = _tool_trajectory(
        "trace-shell-read",
        [
            (
                "exec_command",
                {"command": f"sed -n '1,80p' '{skill_dir / 'SKILL.md'}'"},
                "# xlsx",
            )
        ],
    )

    context = await ReviewFeedbackContextBuilder(store=EvolutionStore(str(tmp_path))).build(trajectory=trajectory)

    assert context.skill_reads == ("xlsx",)


def test_actionable_attribution_converts_to_review_feedback_signal() -> None:
    attribution = ReviewFeedbackAttribution(
        action=ReviewFeedbackAction.EVOLVE_EXISTING_SKILL,
        classification=ReviewFeedbackClassification.SKILL_ISSUE,
        is_skill_actionable=True,
        skill_name="xlsx",
        target=EvolutionTarget.BODY,
        reason="validation is missing",
        reusable_guidance="Validate formulas before saving.",
        confidence=0.9,
        feedback_excerpt="Formula output was wrong.",
    )

    signal = attribution_to_evolution_signal(attribution, task_id="task-1", review_round=2)

    assert signal is not None
    assert signal.signal_type == "review_feedback"
    assert signal.skill_name == "xlsx"
    assert signal.context["task_id"] == "task-1"
    assert signal.context["source"] == "scheduler_review_feedback"
