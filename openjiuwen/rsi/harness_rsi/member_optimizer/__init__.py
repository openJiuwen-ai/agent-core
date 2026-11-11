# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public interface for member Expert Harness optimization.

The package-level interface intentionally exposes the facade and artifact
schema only. Attribution, planning, execution, verification, and worktree
modules are internal implementation details of ``MemberOptimizer``.
"""

from openjiuwen.rsi.harness_rsi.member_optimizer.optimizer import MemberOptimizer
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
    ActionDefinition,
    FailureSignature,
    MechanismAttributionReport,
    MechanismType,
    MemberActionExecutionResult,
    MemberActionMergeResult,
    MemberFixResult,
    MemberOptimizationAction,
    MemberOptimizationArtifact,
    MemberOptimizationPlan,
    MemberOptimizationTarget,
    MemberRoleCandidate,
    MemberSelectionReport,
    MemberVerificationResult,
    RepairItem,
    RoleAttributionReport,
    RoleIssueAttribution,
    RoleMechanismAttribution,
    RoleResult,
    RoleVerificationResult,
    TeamIssue,
    UnassignedMemberIssue,
    UnselectedAttribution,
    VerificationCheck,
)

__all__ = [
    # Facade
    "MemberOptimizer",
    # Schema
    "ActionDefinition",
    "FailureSignature",
    "MechanismAttributionReport",
    "MechanismType",
    "MemberActionExecutionResult",
    "MemberActionMergeResult",
    "MemberFixResult",
    "MemberOptimizationAction",
    "MemberOptimizationArtifact",
    "MemberOptimizationPlan",
    "MemberOptimizationTarget",
    "MemberRoleCandidate",
    "MemberSelectionReport",
    "MemberVerificationResult",
    "RepairItem",
    "RoleAttributionReport",
    "RoleIssueAttribution",
    "RoleMechanismAttribution",
    "RoleResult",
    "RoleVerificationResult",
    "TeamIssue",
    "UnassignedMemberIssue",
    "UnselectedAttribution",
    "VerificationCheck",
]
