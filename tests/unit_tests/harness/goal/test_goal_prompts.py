# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for goal prompt builders."""
from __future__ import annotations

from openjiuwen.harness.prompts.sections.goal import (
    build_goal_current_instruction,
    build_goal_task_query,
    build_transcript_assessor_prompt,
)
from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalContract,
    GoalRecord,
)
from openjiuwen.harness.prompts.sections.goal import build_goal_protocol_section


def test_goal_task_query_first_attempt() -> None:
    record = GoalRecord.create(session_id="s1", objective="Build a REST API")
    query = build_goal_task_query(record, "cn")

    assert "<goal_task>" in query
    assert "<objective>" in query
    assert "Build a REST API" in query
    assert "</goal_task>" in query
    assert "submit_goal_report" not in query


def test_goal_task_query_with_assessment() -> None:
    record = GoalRecord.create(session_id="s1", objective="Build a REST API")
    record.last_assessment = GoalAssessment(
        status=GoalAssessmentStatus.CONTINUE,
        evidence="endpoints created",
        next_instruction="add tests",
    )
    query = build_goal_task_query(record, "cn")

    assert "endpoints created" in query
    assert "add tests" in query


def test_goal_task_query_en() -> None:
    record = GoalRecord.create(session_id="s1", objective="Build feature")
    query = build_goal_task_query(record, "en")

    assert "<goal_task>" in query
    assert "submit_goal_report" not in query


def test_goal_task_query_budget_notice() -> None:
    record = GoalRecord.create(session_id="s1", objective="objective", max_attempts=10)
    record.attempt_count = 3
    query = build_goal_task_query(record, "cn")

    assert "7/10" in query


def test_goal_current_instruction_uses_next_instruction() -> None:
    record = GoalRecord.create(session_id="s1", objective="Build a REST API")
    record.last_assessment = GoalAssessment(
        status=GoalAssessmentStatus.CONTINUE,
        evidence="routes created",
        next_instruction="add tests",
    )

    assert build_goal_current_instruction(record, "en") == "add tests"


def test_transcript_assessor_prompt_uses_attempt_context() -> None:
    prompt = build_transcript_assessor_prompt(
        "Build API",
        "add tests",
        '[1] {"role": "tool", "content": "tests passed"}',
        "cn",
    )
    assert "<objective>" in prompt
    assert "Build API" in prompt
    assert "<current_instruction>" in prompt
    assert "add tests" in prompt
    assert "<attempt_context>" in prompt
    assert "tests passed" in prompt
    assert "<agent_report>" not in prompt
    assert "<final_output>" not in prompt


def test_goal_task_query_with_contract() -> None:
    contract = GoalContract(
        verification="tests pass",
        boundaries="services/auth",
        stop_when="schema change needs sign-off",
    )
    record = GoalRecord.create(
        session_id="s1", objective="Migrate auth to JWT", contract=contract
    )
    query = build_goal_task_query(record, "cn")

    assert "<contract>" in query
    assert "验证标准：tests pass" in query
    assert "范围：services/auth" in query
    assert "停止条件：schema change needs sign-off" in query


def test_goal_task_query_empty_contract_shows_none() -> None:
    record = GoalRecord.create(session_id="s1", objective="vague goal")
    query = build_goal_task_query(record, "cn")

    assert "<contract>" in query
    assert "无。" in query  # empty contract renders the placeholder


def test_transcript_assessor_prompt_with_contract() -> None:
    contract = GoalContract(verification="tests pass", boundaries="services/auth")
    prompt = build_transcript_assessor_prompt(
        "Migrate auth",
        "add tests",
        '[1] {"role": "tool", "content": "ok"}',
        "cn",
        contract=contract,
    )
    assert "<contract>" in prompt
    assert "验证标准：tests pass" in prompt
    assert "范围：services/auth" in prompt


def test_transcript_assessor_prompt_empty_contract_omits_block() -> None:
    prompt = build_transcript_assessor_prompt(
        "objective",
        "instruction",
        "context",
        "cn",
        contract=GoalContract(),
    )
    assert "<contract>" not in prompt  # empty contract → no block injected
