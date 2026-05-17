# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Central protocol literals for agent evolution contracts."""

from __future__ import annotations

from typing import Final, Literal

APPROVE_ACTION: Final[Literal["approve"]] = "approve"
APPEND_MODE: Final[Literal["append"]] = "append"
CONVERSATION_REVIEW_SIGNAL: Final[Literal["conversation_review"]] = "conversation_review"
EXECUTION_FAILURE_SIGNAL: Final[Literal["execution_failure"]] = "execution_failure"
EXPERIENCES_TARGET: Final[Literal["experiences"]] = "experiences"
EXPERIENCE_ENTRY: Final[Literal["experience_entry"]] = "experience_entry"
LOCAL_APPLY_COMPLETED: Final[Literal["local_apply_completed"]] = "local_apply_completed"
MERGE_MODE: Final[Literal["merge"]] = "merge"
PENDING_CHANGE_EFFECT: Final[Literal["pending_change"]] = "pending_change"
REJECT_ACTION: Final[Literal["reject"]] = "reject"
REPLACE_MODE: Final[Literal["replace"]] = "replace"
RETRY_ACTION: Final[Literal["retry"]] = "retry"
SKILL_EXPERIENCE_ENTRY: Final[Literal["skill_experience_entry"]] = "skill_experience_entry"
STATE_EFFECT: Final[Literal["state"]] = "state"
TOOL_FAILURE_SIGNAL: Final[Literal["tool_failure"]] = "tool_failure"
TRAJECTORY_ISSUE_SIGNAL: Final[Literal["trajectory_issue"]] = "trajectory_issue"
USER_INTENT_SIGNAL: Final[Literal["user_intent"]] = "user_intent"

VALID_PATCH_ACTIONS = frozenset({"append", "merge", "replace", "skip"})
VALID_SECTIONS = frozenset(
    {
        "Instructions",
        "Examples",
        "Troubleshooting",
        "Scripts",
        "Collaboration",
        "Roles",
        "Constraints",
        "Workflow",
    }
)

__all__ = [
    "APPROVE_ACTION",
    "APPEND_MODE",
    "CONVERSATION_REVIEW_SIGNAL",
    "EXECUTION_FAILURE_SIGNAL",
    "EXPERIENCES_TARGET",
    "EXPERIENCE_ENTRY",
    "LOCAL_APPLY_COMPLETED",
    "MERGE_MODE",
    "PENDING_CHANGE_EFFECT",
    "REJECT_ACTION",
    "REPLACE_MODE",
    "RETRY_ACTION",
    "SKILL_EXPERIENCE_ENTRY",
    "STATE_EFFECT",
    "TOOL_FAILURE_SIGNAL",
    "TRAJECTORY_ISSUE_SIGNAL",
    "USER_INTENT_SIGNAL",
    "VALID_PATCH_ACTIONS",
    "VALID_SECTIONS",
]
