# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Commit guard helpers for auto-harness."""

from __future__ import annotations

from openjiuwen.auto_harness.infra.commit_scope import (
    is_derived_test_file,
)
from openjiuwen.auto_harness.schema import (
    CommitFacts,
    CommitGuardResult,
    CommitPlan,
)


def derive_allowed_files(
    facts: CommitFacts,
) -> list[str]:
    """Compute the files that may legally enter the commit plan."""
    edited_set = set(facts.edited_files)
    if not edited_set:
        return []

    allowed = {
        path for path in facts.task_declared_files
        if path in edited_set
    }

    allowed.update(
        path for path in facts.derived_test_files
        if path in edited_set
        and is_derived_test_file(
            facts.task_declared_files,
            path,
        )
    )
    allowed.update(
        path for path in facts.legacy_related_test_files
        if path in edited_set
    )

    if not facts.task_declared_files:
        allowed = set(edited_set)

    return sorted(allowed)


def validate_commit_plan(
    facts: CommitFacts,
    plan: CommitPlan,
) -> CommitGuardResult:
    """Validate a commit plan against current commit facts."""
    warnings: list[str] = []
    normalized_files = list(
        dict.fromkeys(
            path.strip().replace("\\", "/")
            for path in plan.files
            if path and path.strip()
        )
    )
    if not normalized_files:
        return CommitGuardResult(
            allowed=False,
            reason="Commit plan must include at least one file.",
        )
    if not plan.message.strip():
        return CommitGuardResult(
            allowed=False,
            blocked_files=normalized_files,
            reason="Commit message cannot be empty.",
        )

    current_dirty = set(facts.current_dirty_files)
    allowed_files = set(facts.allowed_files)
    blocked = [
        path for path in normalized_files
        if path not in current_dirty
    ]
    if blocked:
        return CommitGuardResult(
            allowed=False,
            blocked_files=blocked,
            reason=(
                "Commit plan includes files that are not dirty: "
                + ", ".join(blocked)
            ),
        )

    blocked = [
        path for path in normalized_files
        if path not in allowed_files
    ]
    if blocked:
        return CommitGuardResult(
            allowed=False,
            blocked_files=blocked,
            reason=(
                "Commit plan includes files outside allowed scope: "
                + ", ".join(blocked)
            ),
        )

    preexisting = set(facts.preexisting_dirty_files)
    blocked = [
        path for path in normalized_files
        if path in preexisting
    ]
    if blocked:
        return CommitGuardResult(
            allowed=False,
            blocked_files=blocked,
            reason=(
                "Commit plan includes preexisting dirty files: "
                + ", ".join(blocked)
            ),
        )

    missing_edited = sorted(
        set(facts.edited_files)
        .intersection(current_dirty)
        .difference(normalized_files)
    )
    if missing_edited:
        return CommitGuardResult(
            allowed=False,
            blocked_files=missing_edited,
            reason=(
                "Commit plan omits edited files that are still dirty: "
                + ", ".join(missing_edited)
            ),
        )

    unknown_dirty = sorted(
        current_dirty
        .difference(facts.edited_files)
        .difference(preexisting)
    )
    if unknown_dirty:
        return CommitGuardResult(
            allowed=False,
            blocked_files=unknown_dirty,
            reason=(
                "Detected dirty files outside tracked edits: "
                + ", ".join(unknown_dirty)
            ),
        )

    uses_test_context = any(
        path in facts.derived_test_files
        or path in facts.legacy_related_test_files
        for path in normalized_files
    )
    if uses_test_context and not plan.rationale.strip():
        return CommitGuardResult(
            allowed=False,
            blocked_files=normalized_files,
            reason=(
                "Commit plan must explain why test files are included."
            ),
        )

    if ":" not in plan.message and "(" not in plan.message:
        warnings.append(
            "Commit message does not look like a conventional commit."
        )

    return CommitGuardResult(
        allowed=True,
        normalized_files=normalized_files,
        warnings=warnings,
    )
