# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for commit guard helpers."""

from openjiuwen.auto_harness.infra.commit_guard import (
    derive_allowed_files,
    validate_commit_plan,
)
from openjiuwen.auto_harness.schema import (
    CommitFacts,
    CommitPlan,
)


def _facts() -> CommitFacts:
    facts = CommitFacts(
        task_declared_files=[
            "openjiuwen/auto_harness/schema.py",
        ],
        current_dirty_files=[
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_schema.py",
        ],
        edited_files=[
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_schema.py",
        ],
        derived_test_files=[
            "tests/unit_tests/auto_harness/test_schema.py"
        ],
    )
    facts.allowed_files = derive_allowed_files(facts)
    return facts


def test_derive_allowed_files_includes_derived_tests():
    facts = _facts()
    assert facts.allowed_files == [
        "openjiuwen/auto_harness/schema.py",
        "tests/unit_tests/auto_harness/test_schema.py",
    ]


def test_validate_commit_plan_rejects_empty_files():
    result = validate_commit_plan(
        _facts(),
        CommitPlan(message="fix: x", files=[]),
    )
    assert result.allowed is False


def test_validate_commit_plan_rejects_missing_rationale_for_tests():
    result = validate_commit_plan(
        _facts(),
        CommitPlan(
            message="fix(auto-harness): update schema",
            files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
        ),
    )
    assert result.allowed is False
    assert "explain" in result.reason


def test_validate_commit_plan_allows_declared_and_test_files():
    result = validate_commit_plan(
        _facts(),
        CommitPlan(
            message="fix(auto-harness): update schema",
            files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_schema.py",
            ],
            rationale="Update source and matching test.",
        ),
    )
    assert result.allowed is True


def test_validate_commit_plan_allows_verify_related_legacy_test():
    facts = CommitFacts(
        task_declared_files=[
            "openjiuwen/auto_harness/schema.py",
        ],
        current_dirty_files=[
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_existing_schema.py",
        ],
        edited_files=[
            "openjiuwen/auto_harness/schema.py",
            "tests/unit_tests/auto_harness/test_existing_schema.py",
        ],
        legacy_related_test_files=[
            "tests/unit_tests/auto_harness/test_existing_schema.py"
        ],
        verify_related_files=[
            "tests/unit_tests/auto_harness/test_existing_schema.py"
        ],
    )
    facts.allowed_files = derive_allowed_files(facts)
    result = validate_commit_plan(
        facts,
        CommitPlan(
            message="fix(auto-harness): adapt schema tests",
            files=[
                "openjiuwen/auto_harness/schema.py",
                "tests/unit_tests/auto_harness/test_existing_schema.py",
            ],
            rationale="Adapt verify-related legacy test.",
        ),
    )
    assert result.allowed is True
