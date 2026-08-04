# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for member harness optimizer (feat_009_member_optimization).

Tests cover the two-step attribution-first pipeline, multi-role artifact
shape, wave dependency resolution, worktree isolation, and no-op scenarios.
Per design.md v1.10 Section 7.1.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from openjiuwen.core.foundation.llm import AssistantMessage, BaseModelClient
from openjiuwen.rsi.config import MemberOptimizerConfig
from openjiuwen.rsi.member_optimizer.action_executor import (
    MemberActionExecutor,
    _normalize_skill_frontmatter_name,
    _runtime_contract_projection,
    _skill_trigger_description,
    _validate_generated_action_resources,
    _validate_generated_skill_contract,
)
from openjiuwen.rsi.member_optimizer.action_groups import (
    build_action_waves,
    build_role_subwaves,
    filter_action_definitions,
    load_action_definitions,
    validate_action_policy,
)
from openjiuwen.rsi.member_optimizer.action_planner import (
    MemberActionPlanner,
    MemberActionPlannerAgent,
    _adapt_surface_for_activation_phase,
    _bind_immutable_hypotheses,
    _validate_action_issue_attribution,
)
from openjiuwen.rsi.member_optimizer.agents.factory import (
    _failure_signature_values,
    _mechanism_type_values,
    _optimization_surface_values,
    load_member_optimizer_model,
)
from openjiuwen.rsi.member_optimizer.agents.output import (
    extract_agent_text,
    invoke_member_optimizer_agent_structured,
    parse_json_object_response,
    parse_yaml_or_json_object_response,
)
from openjiuwen.rsi.member_optimizer.hypothesis import (
    compile_optimization_hypotheses,
    load_optimization_hypotheses,
)
from openjiuwen.rsi.member_optimizer.lever import (
    available_surfaces_for_lever,
    target_ref_lever,
)
from openjiuwen.rsi.member_optimizer.loader import EvalRef
from openjiuwen.rsi.member_optimizer.member_selector import MemberSelector
from openjiuwen.rsi.member_optimizer.optimizer import (
    MemberOptimizer,
    _find_reusable_pending_optimization,
)
from openjiuwen.rsi.member_optimizer.path_layout import (
    MemberOptimizerPathLayout,
)
from openjiuwen.rsi.member_optimizer.role_attributor import RoleAttributor
from openjiuwen.rsi.member_optimizer.schema import (
    MechanismAttributionReport,
    MemberActionExecutionResult,
    MemberOptimizationAction,
    MemberOptimizationPlan,
    MemberOptimizationTarget,
    MemberRoleCandidate,
    RoleAttributionReport,
    RoleIssueAttribution,
    RoleMechanismAttribution,
)
from openjiuwen.rsi.member_optimizer.skill_acquisition import (
    SkillAcquisition,
    SkillAcquisitionResult,
    _run_command,
    scan_skill_directory,
)
from openjiuwen.rsi.member_optimizer.verification import (
    HarnessChangeVerifier,
    _validate_package_python_source,
)
from openjiuwen.rsi.member_optimizer.worktree_coordinator import (
    MemberWorktreeCoordinator,
    integration_worktree_path,
    role_worktree_path,
)
from openjiuwen.rsi.schema import ActionDefinition, TeamIssue


def test_lever_policy_does_not_recast_configuration_as_instruction() -> None:
    lever = target_ref_lever("member_harness.solver.config")

    assert lever == "configuration"
    assert available_surfaces_for_lever(lever, ["prompt", "skill", "tool"]) == []


def test_instruction_lever_exposes_only_instruction_surfaces() -> None:
    lever = target_ref_lever("member_harness.solver.skill")

    assert available_surfaces_for_lever(
        lever,
        ["prompt", "skill", "tool"],
    ) == ["prompt_section", "skill"]


def test_member_optimizer_package_exports_only_facade_and_schema() -> None:
    from openjiuwen.rsi import member_optimizer

    assert "MemberOptimizer" in member_optimizer.__all__
    assert "MemberOptimizationArtifact" in member_optimizer.__all__
    assert "MemberSelector" not in member_optimizer.__all__
    assert "RoleAttributor" not in member_optimizer.__all__
    assert "MemberActionExecutorAgent" not in member_optimizer.__all__
    assert "HarnessChangeVerifier" not in member_optimizer.__all__
    assert "HarnessRepairAgent" not in member_optimizer.__all__
    assert "agents" not in member_optimizer.__all__


def test_action_issue_attribution_requires_specific_issue_for_multi_issue_role() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="",
        attributed_issue_ids=["issue_semantic", "issue_path"],
    )
    action = {"role": "solver"}

    errors = _validate_action_issue_attribution(action, {"solver": target})

    assert errors == ["attributed_issue_ids must be a list"]


def test_action_issue_attribution_accepts_assigned_subset() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="",
        attributed_issue_ids=["issue_semantic", "issue_path"],
    )
    action = {
        "role": "solver",
        "attributed_issue_ids": ["issue_semantic"],
    }

    errors = _validate_action_issue_attribution(action, {"solver": target})

    assert errors == []
    assert action["attributed_issue_ids"] == ["issue_semantic"]


def test_action_issue_attribution_rejects_merged_issue_prose() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="",
        attributed_issue_ids=["issue_semantic", "issue_path"],
    )
    action = {
        "role": "solver",
        "attributed_issue_ids": ["issue_semantic"],
        "rationale": "Both attributed issues require one combined verification skill.",
    }

    errors = _validate_action_issue_attribution(action, {"solver": target})

    assert errors == [
        "action prose merges multiple diagnosed issues despite declaring exactly one; "
        "keep every action field within attributed_issue_ids"
    ]


def test_action_issue_attribution_rejects_out_of_scope_issue_reference() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="",
        attributed_issue_ids=["issue_semantic", "issue_path"],
    )
    action = {
        "role": "solver",
        "attributed_issue_ids": ["issue_semantic"],
        "expected_effect": "Fix issue_path while validating protocol semantics.",
    }

    errors = _validate_action_issue_attribution(action, {"solver": target})

    assert errors == ["action prose references issues outside attributed_issue_ids: ['issue_path']"]


def test_action_issue_attribution_rejects_combining_independent_issues() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="",
        attributed_issue_ids=["issue_semantic", "issue_path"],
    )
    action = {
        "role": "solver",
        "attributed_issue_ids": ["issue_semantic", "issue_path"],
    }

    errors = _validate_action_issue_attribution(action, {"solver": target})

    assert errors == [
        "each optimization action must attribute exactly one diagnosed issue; "
        "separate causal mechanisms require separate actions"
    ]


def test_skill_frontmatter_normalization_repairs_plain_scalar_colon() -> None:
    content = """---
name: generated-name
description: Prevent partial coverage: verify every sibling path before completion.
---

# Shared verification

Check every semantically equivalent path.
"""

    normalized = _normalize_skill_frontmatter_name(
        content,
        skill_name="shared_verification",
        fallback_description="fallback",
    )

    frontmatter_text = normalized.split("---", 2)[1]
    frontmatter = yaml.safe_load(frontmatter_text)
    assert frontmatter == {
        "name": "shared_verification",
        "description": ("Prevent partial coverage: verify every sibling path before completion."),
    }
    assert "# Shared verification" in normalized


def test_find_reusable_pending_optimization_matches_identical_inputs(tmp_path: Path) -> None:
    output_root = tmp_path / "optimization"
    run_dir = output_root / "member_optimization_001"
    run_dir.mkdir(parents=True)
    eval_ref = tmp_path / "eval_ref.yaml"
    analysis_ref = tmp_path / "analysis_ref.yaml"
    harness_refs = tmp_path / "harness_refs.yaml"
    candidate_refs = run_dir / "candidate_harness_refs.yaml"
    for path in (eval_ref, analysis_ref, harness_refs, candidate_refs):
        path.write_text("version: 1\n", encoding="utf-8")
    ref_path = run_dir / "member_optimization_ref.yaml"
    ref_path.write_text(
        yaml.safe_dump(
            {
                "status": "success",
                "promotion_status": "pending_gate",
                "optimized_harness_refs_path": str(candidate_refs),
                "metadata": {
                    "eval_ref_path": str(eval_ref),
                    "analysis_result_path": str(analysis_ref),
                    "source_harness_refs_path": str(harness_refs),
                },
            }
        ),
        encoding="utf-8",
    )

    reusable = _find_reusable_pending_optimization(
        output_root=output_root,
        eval_ref_path=str(eval_ref),
        analysis_result_path=str(analysis_ref),
        harness_refs_path=str(harness_refs),
    )

    assert reusable == str(ref_path.resolve())

    payload = yaml.safe_load(ref_path.read_text(encoding="utf-8"))
    payload["promotion_status"] = "rejected"
    ref_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    assert not _find_reusable_pending_optimization(
        output_root=output_root,
        eval_ref_path=str(eval_ref),
        analysis_result_path=str(analysis_ref),
        harness_refs_path=str(harness_refs),
    )


def test_member_optimizer_attribution_taxonomy_excludes_rail() -> None:
    """Member optimization may use existing rails, but cannot attribute to rail."""
    from openjiuwen.rsi.member_optimizer.mechanism_attributor import (
        FAILURE_SIGNATURE_VALUES,
        MECHANISM_TYPE_VALUES,
        OPTIMIZATION_SURFACE_VALUES,
    )

    assert "rail" not in _mechanism_type_values()
    assert "rail" not in _optimization_surface_values()
    assert "config_or_rail_mismatch" not in _failure_signature_values()
    assert "rail" not in MECHANISM_TYPE_VALUES
    assert "rail" not in OPTIMIZATION_SURFACE_VALUES
    assert "config_or_rail_mismatch" not in FAILURE_SIGNATURE_VALUES


def test_member_optimizer_agent_profiles_are_declared() -> None:
    from openjiuwen.rsi.member_optimizer.agents.profiles import (
        ACTION_EXECUTION,
        ACTION_PLANNING,
        MECHANISM_ATTRIBUTION,
        ROLE_ATTRIBUTION,
        VERIFICATION_REPAIR,
        iter_agent_profiles,
    )

    assert iter_agent_profiles() == (
        ROLE_ATTRIBUTION,
        MECHANISM_ATTRIBUTION,
        ACTION_PLANNING,
        ACTION_EXECUTION,
        VERIFICATION_REPAIR,
    )
    assert ACTION_EXECUTION.max_iterations == 8
    assert ACTION_EXECUTION.enabled_skills == ()
    assert VERIFICATION_REPAIR.prompt_file == "verification_repair.md"


def test_member_optimizer_agent_factory_renders_planner_prompt(monkeypatch, tmp_path: Path) -> None:
    from openjiuwen.rsi.member_optimizer.agents import factory

    model_path = _write_model_config(tmp_path / "model.yaml")
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {"agent": kwargs}

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)

    agent = factory.create_action_planning_agent(
        model_config_ref=str(model_path),
        workspace=tmp_path,
        action_definitions=[
            ActionDefinition(
                name="prompt_modify",
                group="prompt",
                operation="modify",
                function="modify_prompt",
                purpose="Modify prompt",
            )
        ],
    )

    assert agent == {"agent": captured}
    assert captured["card"].name == "member_action_planner"
    assert captured["max_iterations"] == 3
    prompt = str(captured["system_prompt"])
    assert "{{ACTION_POLICY_PROMPT}}" not in prompt
    assert "{{ACTION_DEFINITIONS}}" not in prompt
    assert "Allowed action_group values" not in prompt
    assert "prompt/modify: Modify prompt" not in prompt
    assert "Evidence-To-Component Selection" in prompt
    assert "Do not collapse" in prompt
    assert "`soul.md` or `identity.md`" in prompt
    assert "return an empty plan. Do not guess `soul.md`" in prompt
    assert "workflow" in prompt
    assert "act_s_workflow_prompt_1" not in prompt


def test_member_optimizer_agent_factory_uses_external_skill_roots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openjiuwen.rsi.member_optimizer.agents import factory
    from openjiuwen.rsi.member_optimizer.agents.profiles import (
        MemberOptimizerAgentProfile,
    )

    model_path = _write_model_config(tmp_path / "model.yaml")
    skills_root = tmp_path / "optimizer_skills"
    skill_dir = skills_root / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: demo\n---\n# Demo\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)
    profile = MemberOptimizerAgentProfile(
        key="demo",
        agent_name="demo_agent",
        description="Demo agent",
        prompt_file="role_attribution.md",
        enabled_skills=("demo_skill",),
    )

    factory.create_member_optimizer_agent(
        profile=profile,
        model_config_ref=str(model_path),
        workspace=tmp_path,
        agent_skills_dirs=[str(skills_root)],
    )

    rails = captured["rails"]
    assert rails is not None
    assert len(rails) == 1
    assert str(skills_root) in rails[0].skills_dir
    assert rails[0].enabled_skills == {"demo_skill"}


def test_action_execution_agent_does_not_mount_filesystem_write_tools(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openjiuwen.rsi.member_optimizer.agents import factory

    model_path = _write_model_config(tmp_path / "model.yaml")
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)

    factory.create_action_execution_agent(
        model_config_ref=str(model_path),
        workspace=tmp_path,
    )

    assert captured["card"].name == "member_action_executor"
    assert captured["tools"] in (None, [])
    assert captured["sys_operation"] is None


class _MemberOptimizerMockModelClient(BaseModelClient):
    __client_name__ = "MemberOptimizerMockLLM"

    async def invoke(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        return AssistantMessage(content="{}")

    async def stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        yield AssistantMessage(content="{}")

    async def generate_image(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def generate_speech(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def generate_video(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class _FakeStructuredAgent:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.queries: list[str] = []

    async def invoke(self, inputs, session=None):  # type: ignore[no-untyped-def]
        self.queries.append(str(inputs.get("query", "")))
        if not self.responses:
            raise AssertionError("fake structured agent exhausted")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeRoleAttributorAgent:
    async def attribute_issue(self, evidence_bundle):  # type: ignore[no-untyped-def]
        return {
            "issue_id": evidence_bundle.issue["issue_id"],
            "decision": "unassigned",
            "confidence": 0.2,
            "reason": "candidate role name differs from runtime trace role",
            "rationale": "runtime trace used a generic solver role",
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_role_harness_dir(tmp_path: Path) -> Path:
    """Create a minimal two-role harness directory tree."""
    harness = tmp_path / "harness"
    for role in ("explainer", "diagnostician"):
        role_dir = harness / role
        role_dir.mkdir(parents=True)
        (role_dir / "identity.md").write_text(f"# {role} identity\n", encoding="utf-8")
        (role_dir / "harness.yaml").write_text(yaml.safe_dump({"role": role, "version": "1.0"}), encoding="utf-8")
    return harness


@pytest.fixture
def eval_ref_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "eval_ref.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval_001",
                "team_name": "test_team",
                "team_skill_ref_path": "team_skill.yaml",
                "harness_refs_path": "harness_refs.yaml",
                "eval_dir": ".",
                "case_results_dir": "cases",
                "case_traces_dir": "cases",
                "cases": [
                    {
                        "case_id": "case_001",
                        "status": "failed",
                        "result_path": "cases/case_001/result.json",
                        "trace_path": "cases/case_001/trace.json",
                    },
                    {
                        "case_id": "case_002",
                        "status": "failed",
                        "result_path": "cases/case_002/result.json",
                        "trace_path": "cases/case_002/trace.json",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_role_attributor_maps_generic_solver_to_single_harness_role(tmp_path: Path) -> None:
    eval_ref = EvalRef(
        eval_id="eval_001",
        team_name="ppt_team",
        team_skill_ref_path="team_skill.yaml",
        harness_refs_path="harness_refs.yaml",
        eval_dir=tmp_path,
        case_results_dir=tmp_path / "case_results",
        case_traces_dir=tmp_path / "case_traces",
        summary_path=None,
        cases=[],
    )
    issue = TeamIssue(
        issue_id="issue_001",
        category="member_harness",
        severity="high",
        summary="The runtime solver failed to produce incremental PPT artifacts.",
        optimization_target="member_harness",
        target_members=["solver"],
        recommendation="Improve the responsible harness workflow.",
    )
    candidate = MemberRoleCandidate(
        role="presentation_designer",
        member_name="presentation_designer",
        harness_ref_path="harnesses/presentation_designer",
    )

    report = asyncio.run(
        RoleAttributor(role_attributor_agent=_FakeRoleAttributorAgent()).attribute(
            eval_ref=eval_ref,
            team_issues=[issue],
            candidate_roles=[candidate],
            model_config_ref="model.yaml",
        )
    )

    assert report.unassigned_issues == []
    assert len(report.assigned_role_issues) == 1
    assigned = report.assigned_role_issues[0]
    assert assigned.role == "presentation_designer"
    assert assigned.member_name == "presentation_designer"
    assert assigned.harness_ref_path == "harnesses/presentation_designer"
    assert assigned.evidence[0]["target_match_status"] == "target_members_single_role_alias"


def test_role_attributor_does_not_map_team_alias_to_business_member(tmp_path: Path) -> None:
    eval_ref = EvalRef(
        eval_id="eval_001",
        team_name="web_team",
        team_skill_ref_path="team_skill.yaml",
        harness_refs_path="harness_refs.yaml",
        eval_dir=tmp_path,
        case_results_dir=tmp_path / "case_results",
        case_traces_dir=tmp_path / "case_traces",
        summary_path=None,
        cases=[],
    )
    issue = TeamIssue(
        issue_id="issue_team_protocol",
        category="member_harness",
        severity="high",
        summary="The team completed task-board status before deliverables existed.",
        optimization_target="member_harness",
        target_members=["team"],
        recommendation="Do not guess a business role for a team-level issue.",
    )
    candidates = [
        MemberRoleCandidate(
            role="market-analyst",
            member_name="market-analyst",
            harness_ref_path="harnesses/market-analyst",
        ),
        MemberRoleCandidate(
            role="page-implementer",
            member_name="page-implementer",
            harness_ref_path="harnesses/page-implementer",
        ),
    ]

    report = asyncio.run(
        RoleAttributor(role_attributor_agent=_FakeRoleAttributorAgent()).attribute(
            eval_ref=eval_ref,
            team_issues=[issue],
            candidate_roles=candidates,
            model_config_ref="model.yaml",
        )
    )

    assert report.assigned_role_issues == []
    assert len(report.unassigned_issues) == 1
    assert report.unassigned_issues[0].reason == "target_members_mismatch"


def test_resolve_team_issues_only_returns_member_harness_targets() -> None:
    """MemberOptimizer only consumes analyzer issues targeted at member_harness."""
    from openjiuwen.rsi.member_optimizer.loader import (
        AnalysisRef,
        resolve_team_issues,
    )

    issues = resolve_team_issues(
        AnalysisRef(
            issues=[
                {
                    "issue_id": "team_issue",
                    "optimization_target": "team_skill",
                    "category": "team_coordination",
                },
                {
                    "issue_id": "member_issue",
                    "optimization_target": "member_harness",
                    "category": "member_harness",
                },
            ],
            issues_path=None,
        )
    )

    assert [issue.issue_id for issue in issues] == ["member_issue"]
    assert all(issue.optimization_target == "member_harness" for issue in issues)


@pytest.fixture
def harness_refs_yaml(tmp_path: Path, two_role_harness_dir: Path) -> Path:
    path = tmp_path / "harness_refs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "harness_refs": {
                    "explainer": str(two_role_harness_dir / "explainer"),
                    "diagnostician": str(two_role_harness_dir / "diagnostician"),
                },
                "roles": [
                    {
                        "role": "explainer",
                        "member_name": "explainer",
                        "description": "Explains final answer and reasoning.",
                        "harness_ref_path": str(two_role_harness_dir / "explainer"),
                    },
                    {
                        "role": "diagnostician",
                        "member_name": "diagnostician",
                        "description": "Diagnoses failing traces and suggests fixes.",
                        "harness_ref_path": str(two_role_harness_dir / "diagnostician"),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def case_artifacts(tmp_path: Path) -> None:
    for case_id in ("case_001", "case_002"):
        case_dir = tmp_path / "cases" / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "result.json").write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "status": "failed",
                    "score": 0.0,
                    "response": "The answer failed because explainer did not cite evidence.",
                }
            ),
            encoding="utf-8",
        )
        (case_dir / "trace.json").write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "status": "failed",
                    "input": "Explain the solution",
                    "response": "The answer failed.",
                    "evaluation": "Missing evidence citation.",
                }
            ),
            encoding="utf-8",
        )


@pytest.fixture
def action_group_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "actions.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "actions": [
                    {
                        "name": "prompt_modify",
                        "group": "prompt",
                        "operation": "modify",
                        "function": "modify_prompt",
                        "purpose": "Modify the role prompt",
                        "is_destructive": False,
                    },
                    {
                        "name": "skill_search",
                        "group": "skill",
                        "operation": "search",
                        "function": "skill_search",
                        "purpose": "Search for reusable skills",
                        "requires_search": False,
                    },
                    {
                        "name": "skill_install",
                        "group": "skill",
                        "operation": "install",
                        "function": "skill_install",
                        "purpose": "Install a skill",
                        "requires_search": True,
                        "requires_install": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Test: action_waves DAG -correct DAG
# ---------------------------------------------------------------------------


def test_member_optimizer_action_waves_correct_dag() -> None:
    """T4a: Correct DAG produces parallel waves.

    Actions: a1(deps=[]), a2(deps=[a1]), a3(deps=[]), a4(deps=[a2,a3])
    Expected: [[a1, a3], [a2], [a4]]
    """
    actions = [
        MemberOptimizationAction(
            action_id="a1",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            description="Modify prompt",
            depends_on=[],
        ),
        MemberOptimizationAction(
            action_id="a2",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            description="Refine prompt",
            depends_on=["a1"],
        ),
        MemberOptimizationAction(
            action_id="a3",
            role="explainer",
            action_group="skill",
            operation="modify",
            action_type="skill_refinement",
            target_path="skills/explainer.md",
            description="Modify skill",
            depends_on=[],
        ),
        MemberOptimizationAction(
            action_id="a4",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            description="Final refinement",
            depends_on=["a2", "a3"],
        ),
    ]
    waves = build_action_waves(actions)
    assert waves == [["a1", "a3"], ["a2"], ["a4"]]


# ---------------------------------------------------------------------------
# Test: action_waves DAG -cycle detection
# ---------------------------------------------------------------------------


def test_member_optimizer_action_waves_cycle_detection() -> None:
    """T4b: Circular dependency raises ValueError."""
    actions = [
        MemberOptimizationAction(
            action_id="a1",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            description="A1",
            depends_on=["a3"],
        ),
        MemberOptimizationAction(
            action_id="a2",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            description="A2",
            depends_on=["a1"],
        ),
        MemberOptimizationAction(
            action_id="a3",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            description="A3",
            depends_on=["a2"],
        ),
    ]
    with pytest.raises(ValueError, match="dependency cycle"):
        build_action_waves(actions)


# ---------------------------------------------------------------------------
# Test: action_waves DAG -each action appears exactly once
# ---------------------------------------------------------------------------


def test_member_optimizer_action_waves_no_duplicates() -> None:
    """T4c: Each action appears exactly once across all waves."""
    actions = [
        MemberOptimizationAction(
            action_id=f"action_{i:03d}",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path=f"p{i}.md",
            description=f"Action {i}",
            depends_on=[f"action_{i - 1:03d}"] if i > 0 else [],
        )
        for i in range(5)
    ]
    waves = build_action_waves(actions)
    flattened = [aid for wave in waves for aid in wave]
    assert len(flattened) == len(actions)
    assert set(flattened) == {f"action_{i:03d}" for i in range(5)}


def test_member_optimizer_role_subwaves_split_overlapping_paths() -> None:
    """Role-local actions sharing declared paths are serialized into subwaves."""
    actions = [
        MemberOptimizationAction(
            action_id="a1",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            declared_write_paths=["identity.md"],
        ),
        MemberOptimizationAction(
            action_id="a2",
            role="explainer",
            action_group="skill",
            operation="modify",
            action_type="skill_refinement",
            target_path="skills/style.md",
            declared_write_paths=["skills/style.md"],
        ),
        MemberOptimizationAction(
            action_id="a3",
            role="explainer",
            action_group="skill",
            operation="modify",
            action_type="skill_refinement",
            target_path="skills/style.md",
            declared_write_paths=["skills/"],
        ),
        MemberOptimizationAction(
            action_id="a4",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            declared_write_paths=["identity.md"],
        ),
    ]

    subwaves = build_role_subwaves(actions)

    assert [[action.action_id for action in subwave] for subwave in subwaves] == [
        ["a1", "a2"],
        ["a3", "a4"],
    ]


# ---------------------------------------------------------------------------
# Test: MemberSelector deterministic ordering
# ---------------------------------------------------------------------------


def test_member_selector_stable_ordering() -> None:
    """Selector ordering is stable: score desc, max_severity desc, role asc."""
    role_report = RoleAttributionReport(
        attribution_id="attr_001",
        source_eval_ref_path=".",
        source_analysis_result_path=".",
        harness_refs_path=".",
        candidate_roles=[
            MemberRoleCandidate(role="explainer", harness_ref_path="exp", member_name="exp"),
            MemberRoleCandidate(role="diagnostician", harness_ref_path="diag", member_name="diag"),
        ],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_001",
                role="explainer",
                harness_ref_path="exp",
                confidence=0.82,
                evidence=[{"summary": "Prompt misses evidence.", "severity": "high"}],
                trace_refs=[],
            ),
            RoleIssueAttribution(
                issue_id="issue_002",
                role="diagnostician",
                harness_ref_path="diag",
                confidence=0.75,
                evidence=[{"summary": "Skill misses diagnosis.", "severity": "medium"}],
                trace_refs=[],
            ),
        ],
    )
    mech_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_001",
        source_role_attribution_path=".",
        role_mechanisms={
            "explainer": [
                RoleMechanismAttribution(
                    issue_id="issue_001",
                    role="explainer",
                    mechanism_type="prompt",
                    failure_signature="prompt_instruction_mismatch",
                    confidence=0.74,
                    evidence=[],
                    evidence_refs=[],
                    rationale="",
                ),
            ],
            "diagnostician": [
                RoleMechanismAttribution(
                    issue_id="issue_002",
                    role="diagnostician",
                    mechanism_type="skill",
                    failure_signature="skill_code_failure",
                    confidence=0.65,
                    evidence=[],
                    evidence_refs=[],
                    rationale="",
                ),
            ],
        },
    )

    selector = MemberSelector()
    report = selector.select(
        role_attribution_report=role_report,
        mechanism_attribution_report=mech_report,
        min_attribution_confidence=0.5,
        max_roles_per_run=2,
    )

    assert len(report.targets) == 2
    assert report.targets[0].role == "explainer"
    assert report.targets[1].role == "diagnostician"


def test_member_selector_carries_optimization_surfaces() -> None:
    """Selector preserves optimization surface separately from failure mechanism."""
    role_report = RoleAttributionReport(
        attribution_id="attr_surface",
        candidate_roles=[
            MemberRoleCandidate(
                role="solver",
                harness_ref_path="solver",
                member_name="solver",
            )
        ],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-wal",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"severity": "high", "summary": "WAL recovery needs a reusable method."}],
            )
        ],
    )
    mech_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_surface",
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="ISS-wal",
                    role="solver",
                    mechanism_type="workflow",
                    failure_signature="missed_exploration_or_capability",
                    confidence=0.85,
                    optimization_surface="skill",
                    rationale="The failure appears in workflow, but the fix is a reusable recovery method.",
                )
            ]
        },
    )

    report = MemberSelector().select(
        role_attribution_report=role_report,
        mechanism_attribution_report=mech_report,
    )

    assert report.targets[0].mechanism_types == ["workflow"]
    assert report.targets[0].optimization_surfaces == ["skill"]


def test_member_selector_uses_attribution_evidence_severity_before_confidence() -> None:
    role_report = RoleAttributionReport(
        attribution_id="attr_severity",
        candidate_roles=[
            MemberRoleCandidate(role="critical_role", harness_ref_path="critical", member_name="critical"),
            MemberRoleCandidate(role="confident_role", harness_ref_path="confident", member_name="confident"),
        ],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_critical",
                role="critical_role",
                harness_ref_path="critical",
                confidence=0.51,
                evidence=[{"summary": "Critical issue.", "severity": "critical"}],
            ),
            RoleIssueAttribution(
                issue_id="issue_confident",
                role="confident_role",
                harness_ref_path="confident",
                confidence=0.99,
                evidence=[{"summary": "Low issue.", "severity": "low"}],
            ),
        ],
    )
    mech_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_severity",
        role_mechanisms={},
    )

    report = MemberSelector().select(
        role_attribution_report=role_report,
        mechanism_attribution_report=mech_report,
        min_attribution_confidence=0.5,
        max_roles_per_run=2,
    )

    assert [target.role for target in report.targets] == ["critical_role", "confident_role"]


# ---------------------------------------------------------------------------
# Test: MemberSelector no targets when confidence too low
# ---------------------------------------------------------------------------


def test_member_selector_no_targets_low_confidence() -> None:
    """When all confidence scores are below threshold, no targets are selected."""
    role_report = RoleAttributionReport(
        attribution_id="attr_002",
        source_eval_ref_path=".",
        source_analysis_result_path=".",
        harness_refs_path=".",
        candidate_roles=[
            MemberRoleCandidate(role="explainer", harness_ref_path="exp", member_name="exp"),
        ],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_001",
                role="explainer",
                harness_ref_path="exp",
                confidence=0.3,
                evidence=[],
                trace_refs=[],
            ),
        ],
    )
    mech_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_002",
        source_role_attribution_path=".",
        role_mechanisms={},
    )

    selector = MemberSelector()
    report = selector.select(
        role_attribution_report=role_report,
        mechanism_attribution_report=mech_report,
        min_attribution_confidence=0.5,
        max_roles_per_run=2,
    )

    assert len(report.targets) == 0


def test_member_action_planner_returns_empty_plan_for_insufficient_role_evidence_only() -> None:
    """Planner must not turn insufficient evidence into a direct file modification."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        MemberActionPlanner,
    )

    class FailingPlannerAgent:
        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("planner agent should not be called for inactionable targets")

    target = MemberOptimizationTarget(
        role="solver",
        member_name="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["ISS-timeout"],
        confidence=0.8,
        mechanism_types=["insufficient_role_evidence"],
    )
    role_report = RoleAttributionReport(
        attribution_id="attr_insufficient",
        candidate_roles=[MemberRoleCandidate(role="solver", harness_ref_path="solver", member_name="solver")],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-timeout",
                role="solver",
                harness_ref_path="solver",
                confidence=0.8,
                evidence=[{"summary": "Verifier timeout appears in evaluator metadata."}],
            )
        ],
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_insufficient",
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="ISS-timeout",
                    role="solver",
                    mechanism_type="insufficient_role_evidence",
                    failure_signature="insufficient_role_evidence",
                    confidence=0.6,
                    rationale="Timeout is not confirmed as a role package field.",
                )
            ]
        },
    )

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=FailingPlannerAgent()).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[],
            model_config_ref="unused",
        )
    )

    assert plan.actions == []
    assert plan.action_waves == []
    assert plan.metadata["filtered_inactionable_issue_ids"] == ["ISS-timeout"]


def test_member_selector_records_insufficient_evidence_as_deferred_contract_issue() -> None:
    role_report = RoleAttributionReport(
        attribution_id="attr_contract",
        candidate_roles=[MemberRoleCandidate(role="ui-builder", harness_ref_path="ui-builder")],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-dom-contract",
                role="ui-builder",
                harness_ref_path="ui-builder",
                confidence=0.5,
                evidence=[{"summary": "JS queries a selector that is absent from HTML."}],
            )
        ],
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_contract",
        role_mechanisms={
            "ui-builder": [
                RoleMechanismAttribution(
                    issue_id="ISS-dom-contract",
                    role="ui-builder",
                    mechanism_type="insufficient_role_evidence",
                    failure_signature="insufficient_role_evidence",
                    confidence=0.3,
                    rationale="Cross-file contract spans multiple members.",
                )
            ]
        },
    )

    report = MemberSelector().select(
        role_attribution_report=role_report,
        mechanism_attribution_report=mechanism_report,
        min_attribution_confidence=0.1,
        max_roles_per_run=2,
    )

    assert report.targets == []
    assert report.metadata["deferred_contract_issue_ids"] == ["ISS-dom-contract"]
    assert report.metadata["deferred_contract_route"] == "team_skill"


def test_member_action_planner_rejects_specific_workflow_in_soul_md() -> None:
    """Concrete workflow/checklist repairs must use prompt sections, not soul.md."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        MemberActionPlanner,
    )

    class SoulPlannerAgent:
        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "plan_id": "plan_bad_soul",
                "targets": [{"role": "solver"}],
                "actions": [
                    {
                        "action_id": "act_solver_prompt_1",
                        "role": "solver",
                        "action_group": "prompt",
                        "operation": "modify",
                        "action_type": "prompt_improvement",
                        "target_path": "soul.md",
                        "declared_write_paths": ["soul.md"],
                        "description": "Add a git merge verification checklist with concrete steps.",
                        "rationale": "The solver committed before completing verification.",
                        "depends_on": [],
                        "allowed_skills": [],
                        "allowed_tools": ["read_file", "edit_file"],
                        "candidate_query": "",
                        "install_ref": "",
                        "expected_effect": "The solver follows the git verification procedure.",
                        "risk_notes": [],
                        "constraints": {"surface_scope": "durable_operating_principle"},
                    }
                ],
                "action_waves": [["act_solver_prompt_1"]],
            }

    target = MemberOptimizationTarget(
        role="solver",
        member_name="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["ISS-git"],
        confidence=0.9,
        mechanism_types=["instruction"],
    )
    role_report = RoleAttributionReport(
        attribution_id="attr_git",
        candidate_roles=[MemberRoleCandidate(role="solver", harness_ref_path="solver", member_name="solver")],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-git",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"summary": "Solver committed unresolved merge conflicts."}],
            )
        ],
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_git",
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="ISS-git",
                    role="solver",
                    mechanism_type="instruction",
                    failure_signature="prompt_instruction_mismatch",
                    confidence=0.8,
                    rationale="Missing git verification procedure.",
                )
            ]
        },
    )

    with pytest.raises(RuntimeError, match="prompt_sections/files"):
        asyncio.run(
            MemberActionPlanner(planner_agent=SoulPlannerAgent()).plan(
                targets=[target],
                role_attribution_report=role_report,
                mechanism_attribution_report=mechanism_report,
                action_definitions=[],
                model_config_ref="unused",
            )
        )


def test_member_action_planner_accepts_specific_workflow_prompt_section() -> None:
    """Specific workflow repairs can be planned as mounted prompt sections."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        MemberActionPlanner,
    )

    class PromptSectionPlannerAgent:
        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "plan_id": "plan_prompt_section",
                "targets": [{"role": "solver"}],
                "actions": [
                    {
                        "action_id": "act_solver_prompt_section_1",
                        "role": "solver",
                        "action_group": "prompt",
                        "operation": "modify",
                        "action_type": "prompt_improvement",
                        "target_path": "prompt_sections/files/git_conflict_resolution.md",
                        "declared_write_paths": [
                            "prompt_sections/files/git_conflict_resolution.md",
                        ],
                        "description": "Add a git conflict-resolution verification procedure.",
                        "rationale": "The solver committed unresolved merge conflicts.",
                        "depends_on": [],
                        "allowed_skills": [],
                        "allowed_tools": ["read_file", "edit_file"],
                        "candidate_query": "",
                        "install_ref": "",
                        "expected_effect": "The solver verifies conflict markers before commit.",
                        "risk_notes": [],
                        "constraints": {
                            "section_name": "git_conflict_resolution",
                            "priority": 60,
                        },
                    }
                ],
                "action_waves": [["act_solver_prompt_section_1"]],
            }

    target = MemberOptimizationTarget(
        role="solver",
        member_name="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["ISS-git"],
        confidence=0.9,
        mechanism_types=["instruction"],
    )
    role_report = RoleAttributionReport(
        attribution_id="attr_git",
        candidate_roles=[MemberRoleCandidate(role="solver", harness_ref_path="solver", member_name="solver")],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-git",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"summary": "Solver committed unresolved merge conflicts."}],
            )
        ],
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_git",
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="ISS-git",
                    role="solver",
                    mechanism_type="instruction",
                    failure_signature="prompt_instruction_mismatch",
                    confidence=0.8,
                    rationale="Missing git verification procedure.",
                )
            ]
        },
    )

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=PromptSectionPlannerAgent()).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[
                ActionDefinition(
                    name="prompt_modify",
                    group="prompt",
                    operation="modify",
                    function="modify_prompt",
                    purpose="Modify a package-local prompt section",
                )
            ],
            model_config_ref="unused",
        )
    )

    assert len(plan.actions) == 1
    assert plan.actions[0].target_path == "prompt_sections/files/git_conflict_resolution.md"
    assert plan.actions[0].declared_write_paths == [
        "prompt_sections/files/git_conflict_resolution.md",
        "prompt_sections/sections.yaml",
    ]


def test_member_action_planner_rejects_prompt_when_surface_is_skill() -> None:
    """A workflow failure can still require a skill optimization surface."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        MemberActionPlanner,
    )

    class PromptPlannerAgent:
        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "plan_id": "plan_wrong_surface",
                "targets": [{"role": "solver"}],
                "actions": [
                    {
                        "action_id": "act_solver_prompt_section_1",
                        "role": "solver",
                        "action_group": "prompt",
                        "operation": "add",
                        "action_type": "prompt_improvement",
                        "target_path": "prompt_sections/files/wal_recovery.md",
                        "declared_write_paths": [
                            "prompt_sections/files/wal_recovery.md",
                            "prompt_sections/sections.yaml",
                        ],
                        "description": "Add WAL recovery steps as a prompt checklist.",
                        "rationale": "The solver lacks a reusable WAL recovery method.",
                        "depends_on": [],
                        "allowed_skills": [],
                        "allowed_tools": ["read_file", "write_file"],
                        "candidate_query": "",
                        "install_ref": "",
                        "expected_effect": "The solver follows WAL recovery guidance.",
                        "risk_notes": [],
                        "constraints": {
                            "section_name": "wal_recovery",
                            "surface_choice_reason": "Workflow issue needs guidance.",
                        },
                    }
                ],
                "action_waves": [["act_solver_prompt_section_1"]],
            }

    target = MemberOptimizationTarget(
        role="solver",
        member_name="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["ISS-wal"],
        confidence=0.9,
        mechanism_types=["workflow"],
        optimization_surfaces=["skill"],
    )
    role_report = RoleAttributionReport(
        attribution_id="attr_wal",
        candidate_roles=[MemberRoleCandidate(role="solver", harness_ref_path="solver", member_name="solver")],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-wal",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"summary": "Solver needs a reusable WAL recovery method."}],
            )
        ],
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_wal",
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="ISS-wal",
                    role="solver",
                    mechanism_type="workflow",
                    failure_signature="missed_exploration_or_capability",
                    confidence=0.85,
                    optimization_surface="skill",
                    rationale="The failure appears in workflow, but the fix is a reusable method.",
                )
            ]
        },
    )

    with pytest.raises(RuntimeError, match="optimization_surface"):
        asyncio.run(
            MemberActionPlanner(planner_agent=PromptPlannerAgent()).plan(
                targets=[target],
                role_attribution_report=role_report,
                mechanism_attribution_report=mechanism_report,
                action_definitions=[],
                model_config_ref="unused",
            )
        )


def test_member_action_planner_replans_when_action_surface_mismatches_diagnosis() -> None:
    """Planner should repair semantic surface mismatches instead of aborting immediately."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        MemberActionPlanner,
    )

    class RepairingPlannerAgent:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {
                    "plan_id": "plan_wrong_surface",
                    "targets": [{"role": "qa-tester"}],
                    "actions": [
                        {
                            "action_id": "act_qa_tester_prompt_1",
                            "role": "qa-tester",
                            "action_group": "prompt",
                            "operation": "add",
                            "action_type": "prompt_improvement",
                            "target_path": "prompt_sections/files/runtime_checklist.md",
                            "declared_write_paths": [
                                "prompt_sections/files/runtime_checklist.md",
                                "prompt_sections/sections.yaml",
                            ],
                            "description": "Add runtime validation instructions.",
                            "rationale": "The role needs deterministic runtime validation.",
                            "depends_on": [],
                            "allowed_skills": [],
                            "allowed_tools": ["read_file", "write_file"],
                            "candidate_query": "",
                            "install_ref": "",
                            "expected_effect": "The role validates runtime behavior.",
                            "risk_notes": [],
                            "constraints": {"section_name": "runtime_checklist"},
                        }
                    ],
                    "action_waves": [["act_qa_tester_prompt_1"]],
                }
            return {
                "plan_id": "plan_tool_surface",
                "targets": [{"role": "qa-tester"}],
                "actions": [
                    {
                        "action_id": "act_qa_tester_tool_1",
                        "role": "qa-tester",
                        "action_group": "tool",
                        "operation": "add",
                        "action_type": "tool_creation",
                        "target_path": "tools/runtime_state_validator.py",
                        "declared_write_paths": [
                            "tools/runtime_state_validator.py",
                            "tools/tools.yaml",
                        ],
                        "description": "Add a deterministic runtime state validator tool.",
                        "rationale": "The diagnosed optimization surface is tool.",
                        "depends_on": [],
                        "allowed_skills": [],
                        "allowed_tools": ["read_file", "write_file"],
                        "candidate_query": "",
                        "install_ref": "",
                        "expected_effect": "The role can validate runtime state deterministically.",
                        "risk_notes": [],
                        "constraints": {"class_name": "RuntimeStateValidator"},
                    }
                ],
                "action_waves": [["act_qa_tester_tool_1"]],
            }

    target = MemberOptimizationTarget(
        role="qa-tester",
        member_name="qa-tester",
        harness_ref_path="qa-tester",
        attributed_issue_ids=["ISS-runtime-tool"],
        confidence=0.9,
        mechanism_types=["tool"],
        optimization_surfaces=["tool"],
    )
    role_report = RoleAttributionReport(
        attribution_id="attr_runtime_tool",
        candidate_roles=[MemberRoleCandidate(role="qa-tester", harness_ref_path="qa-tester", member_name="qa-tester")],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-runtime-tool",
                role="qa-tester",
                harness_ref_path="qa-tester",
                confidence=0.9,
                evidence=[{"summary": "Runtime behavior needs deterministic validation."}],
            )
        ],
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_runtime_tool",
        role_mechanisms={
            "qa-tester": [
                RoleMechanismAttribution(
                    issue_id="ISS-runtime-tool",
                    role="qa-tester",
                    mechanism_type="tool",
                    failure_signature="missing_deterministic_tool",
                    confidence=0.85,
                    optimization_surface="tool",
                    rationale="The fix needs a package-local executable checker.",
                )
            ]
        },
    )
    planner_agent = RepairingPlannerAgent()

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=planner_agent).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[
                ActionDefinition(
                    name="tool_add",
                    group="tool",
                    operation="add",
                    function="add_tool",
                    purpose="Add a package-local deterministic tool",
                )
            ],
            model_config_ref="unused",
        )
    )

    assert len(planner_agent.calls) == 2
    assert planner_agent.calls[0].get("validation_errors") in (None, [])
    assert any(
        "actual=prompt_section" in error and "expected_one_of=['tool']" in error
        for error in planner_agent.calls[1]["validation_errors"]  # type: ignore[index]
    )
    assert len(plan.actions) == 1
    assert plan.actions[0].action_group == "tool"
    assert plan.actions[0].target_path == "tools/runtime_state_validator.py"


def test_member_action_planner_accepts_package_local_rail_actions() -> None:
    """Control diagnoses can publish a loadable package-local Rail."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        MemberActionPlanner,
    )

    class MixedSurfacePlannerAgent:
        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "plan_id": "plan_mixed_surface",
                "targets": [{"role": "solver"}],
                "actions": [
                    {
                        "action_id": "act_solver_rail_1",
                        "role": "solver",
                        "action_group": "rail",
                        "operation": "add",
                        "action_type": "rail_guard",
                        "target_path": "rails/conflict_marker_guard.py",
                        "declared_write_paths": [
                            "rails/conflict_marker_guard.py",
                            "rails/rails.yaml",
                        ],
                        "description": "Add a conflict marker output guard.",
                        "rationale": "Output contained unresolved conflict markers.",
                        "depends_on": [],
                        "allowed_skills": [],
                        "allowed_tools": ["read_file", "write_file"],
                        "candidate_query": "",
                        "install_ref": "",
                        "expected_effect": "Runtime blocks unresolved conflict markers.",
                        "risk_notes": [],
                        "constraints": {"class_name": "ConflictMarkerGuardRail"},
                    },
                ],
                "action_waves": [["act_solver_rail_1"]],
            }

    target = MemberOptimizationTarget(
        role="solver",
        member_name="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["ISS-git"],
        confidence=0.9,
        mechanism_types=["control", "output_guard"],
        optimization_surfaces=["rail"],
    )
    role_report = RoleAttributionReport(
        attribution_id="attr_git",
        candidate_roles=[MemberRoleCandidate(role="solver", harness_ref_path="solver", member_name="solver")],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="ISS-git",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"summary": "Solver left conflict markers in output."}],
            )
        ],
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_git",
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="ISS-git",
                    role="solver",
                    mechanism_type="control",
                    failure_signature="missing_conflict_marker_check",
                    confidence=0.8,
                    optimization_surface="rail",
                    rationale="Needs a conflict marker handling improvement.",
                )
            ]
        },
    )

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=MixedSurfacePlannerAgent()).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[
                ActionDefinition(
                    name="rail_add",
                    group="rail",
                    operation="add",
                    function="add_rail",
                    purpose="Add a package-local runtime control Rail",
                )
            ],
            model_config_ref="unused",
            allowed_action_groups=["rail"],
        )
    )

    assert [action.action_group for action in plan.actions] == ["rail"]
    assert plan.actions[0].target_path == "rails/conflict_marker_guard.py"


# ---------------------------------------------------------------------------
# Test: run directory allocation -next version
# ---------------------------------------------------------------------------


def test_member_optimizer_allocates_next_version(tmp_path: Path) -> None:
    """E1: When member_optimization_001 exists, allocate 002."""
    output_dir = tmp_path / "member_optimizations"
    output_dir.mkdir(parents=True)
    (output_dir / "member_optimization_001").mkdir()

    from openjiuwen.rsi.member_optimizer.optimizer import (
        _allocate_optimization_dir,
    )

    run_dir = _allocate_optimization_dir(output_dir)
    assert run_dir.name == "member_optimization_002"


# ---------------------------------------------------------------------------
# Test: run directory allocation -starts at 001
# ---------------------------------------------------------------------------


def test_member_optimizer_allocates_starts_at_001(tmp_path: Path) -> None:
    """Empty output directory gets member_optimization_001."""
    output_dir = tmp_path / "member_optimizations"
    output_dir.mkdir(parents=True)

    from openjiuwen.rsi.member_optimizer.optimizer import (
        _allocate_optimization_dir,
    )

    run_dir = _allocate_optimization_dir(output_dir)
    assert run_dir.name == "member_optimization_001"


def test_member_optimizer_fails_when_candidate_harness_ref_is_missing(
    tmp_path: Path,
    eval_ref_yaml: Path,
    case_artifacts: None,
) -> None:
    """Resolved candidate roles must point at pre-initialized ExpertHarness refs."""
    missing_refs = tmp_path / "missing_harness_refs.yaml"
    missing_refs.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "roles": [
                    {
                        "role": "explainer",
                        "member_name": "explainer",
                        "description": "Explains final answer and reasoning.",
                        "harness_ref_path": "does_not_exist",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    analysis_path = tmp_path / "analysis_ref.yaml"
    analysis_path.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "issue_id": "issue_001",
                        "category": "member_harness",
                        "severity": "high",
                        "summary": "Explainer prompt misses evidence.",
                        "affected_cases": ["case_001"],
                        "suspected_team_scope": "member",
                        "optimization_target": "member_harness",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    model_path = _write_model_config(tmp_path / "model.yaml")
    optimizer = MemberOptimizer(MemberOptimizerConfig(model_config_ref=str(model_path)))

    import asyncio

    with pytest.raises(RuntimeError, match="pre-initialized role ExpertHarness refs"):
        asyncio.run(
            optimizer.optimize(
                eval_ref_path=str(eval_ref_yaml),
                analysis_result_path=str(analysis_path),
                harness_refs_path=str(missing_refs),
                output_dir=str(tmp_path / "member_optimizations"),
            )
        )


# ---------------------------------------------------------------------------
# Test: worktree isolation
# ---------------------------------------------------------------------------


def test_member_optimizer_worktree_isolation(tmp_path: Path) -> None:
    """T5 / C1-C5: Each action gets an isolated worktree directory."""
    coordinator = MemberWorktreeCoordinator()

    action = MemberOptimizationAction(
        action_id="action_001",
        role="explainer",
        action_group="prompt",
        operation="modify",
        action_type="prompt_refinement",
        target_path="identity.md",
        description="Modify prompt",
        depends_on=[],
    )

    worktrees_dir = tmp_path / "worktrees"
    integration_dir = coordinator.prepare_integration_worktree(
        role="explainer",
        harness_ref_path="",
        worktrees_dir=str(worktrees_dir),
    )

    assert integration_dir.exists()
    assert integration_dir == integration_worktree_path(worktrees_dir, "explainer")
    assert integration_dir.name == "i"
    assert integration_dir.parent == role_worktree_path(worktrees_dir, "explainer")

    action_wt = coordinator.prepare_action_worktree(
        action=action,
        integration_worktree=integration_dir,
        worktrees_dir=str(worktrees_dir),
        wave_index=0,
    )

    assert action_wt.exists()
    assert action_wt.parent == role_worktree_path(worktrees_dir, "explainer") / "a" / "0"
    assert "explainer" not in str(action_wt.relative_to(worktrees_dir))
    assert len(action_wt.name) == 8


def test_member_optimizer_worktree_paths_stay_short_for_long_action_names(tmp_path: Path) -> None:
    coordinator = MemberWorktreeCoordinator()
    action = MemberOptimizationAction(
        action_id="act_pres_designer_prompt_mandatory_content_checklist_001",
        role="presentation_designer",
        action_group="prompt",
        operation="add",
        action_type="prompt_improvement",
        target_path="prompt_sections/files/mandatory_content_checklist.md",
        declared_write_paths=[
            "prompt_sections/files/mandatory_content_checklist.md",
            "prompt_sections/sections.yaml",
        ],
    )
    worktrees_dir = tmp_path / "member_optimization_003" / "wt"
    integration = coordinator.prepare_integration_worktree(
        role=action.role,
        harness_ref_path="",
        worktrees_dir=str(worktrees_dir),
    )

    action_wt = coordinator.prepare_action_worktree(
        action=action,
        integration_worktree=integration,
        worktrees_dir=str(worktrees_dir),
        wave_index=12,
    )

    target = action_wt / action.target_path
    old_style_target = (
        tmp_path
        / "member_optimization_003"
        / "worktrees"
        / action.role
        / "w"
        / "012"
        / "a_1234567890"
        / action.target_path
    )
    assert len(str(target)) + 25 <= len(str(old_style_target))


# ---------------------------------------------------------------------------
# Test: worktree path validation -forbidden paths
# ---------------------------------------------------------------------------


def test_member_optimizer_worktree_forbidden_paths(tmp_path: Path) -> None:
    """target_path with .. traversal or absolute path must be rejected."""
    coordinator = MemberWorktreeCoordinator()

    action_bad = MemberOptimizationAction(
        action_id="bad_001",
        role="explainer",
        action_group="prompt",
        operation="modify",
        action_type="prompt_refinement",
        target_path="../../etc/passwd",
        description="Bad path",
        depends_on=[],
    )

    worktrees_dir = tmp_path / "worktrees"
    integration = coordinator.prepare_integration_worktree(
        role="explainer",
        harness_ref_path="",
        worktrees_dir=str(worktrees_dir),
    )

    with pytest.raises(ValueError, match="outside role worktree"):
        coordinator.prepare_action_worktree(
            action=action_bad,
            integration_worktree=integration,
            worktrees_dir=str(worktrees_dir),
            wave_index=0,
        )


# ---------------------------------------------------------------------------
# Test: schema -all dataclasses are frozen
# ---------------------------------------------------------------------------


def test_schema_dataclasses_are_frozen() -> None:
    """All schema dataclasses must be immutable (frozen=True)."""
    from openjiuwen.rsi.member_optimizer.schema import (
        MemberOptimizationArtifact,
    )

    with pytest.raises(Exception):
        MemberOptimizationArtifact(
            optimization_id="x",
            output_dir="x",
            status="x",
            optimized_harness_refs_path="x",
            roles=[],
            published_roles=[],
            failed_roles=[],
            skipped_roles=[],
        ).optimization_id = "y"


# ---------------------------------------------------------------------------
# Test: MemberSelector max_roles_per_run limit
# ---------------------------------------------------------------------------


def test_member_selector_respects_max_roles() -> None:
    """Selector must not select more than max_roles_per_run targets."""
    candidates = [
        MemberRoleCandidate(role=f"role_{i}", harness_ref_path=f"ref_{i}", member_name=f"m_{i}") for i in range(5)
    ]
    issues = [
        RoleIssueAttribution(
            issue_id=f"issue_{i}",
            role=f"role_{i}",
            harness_ref_path=f"ref_{i}",
            confidence=0.8,
            evidence=[{"summary": f"Issue {i}", "severity": "medium"}],
            trace_refs=[],
        )
        for i in range(5)
    ]

    role_report = RoleAttributionReport(
        attribution_id="attr_limit",
        source_eval_ref_path=".",
        source_analysis_result_path=".",
        harness_refs_path=".",
        candidate_roles=candidates,
        assigned_role_issues=issues,
    )

    mech_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_limit",
        source_role_attribution_path=".",
        role_mechanisms={f"role_{i}": [] for i in range(5)},
    )

    selector = MemberSelector()
    report = selector.select(
        role_attribution_report=role_report,
        mechanism_attribution_report=mech_report,
        min_attribution_confidence=0.5,
        max_roles_per_run=2,
    )

    assert len(report.targets) <= 2


def test_member_selector_unselected_roles_keep_attribution_confidence() -> None:
    """Unselected role issues keep their real attribution confidence in reports."""
    role_report = RoleAttributionReport(
        attribution_id="attr_unselected_confidence",
        source_eval_ref_path=".",
        source_analysis_result_path=".",
        harness_refs_path=".",
        candidate_roles=[
            MemberRoleCandidate(
                role="frontend-coder",
                harness_ref_path="frontend",
                member_name="frontend",
            ),
            MemberRoleCandidate(
                role="content-author",
                harness_ref_path="content",
                member_name="content",
            ),
        ],
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_frontend",
                role="frontend-coder",
                harness_ref_path="frontend",
                confidence=0.9,
                evidence=[{"summary": "Layout broken.", "severity": "high"}],
                trace_refs=[],
            ),
            RoleIssueAttribution(
                issue_id="issue_content",
                role="content-author",
                harness_ref_path="content",
                confidence=0.7,
                evidence=[{"summary": "Content brief missed required sections.", "severity": "medium"}],
                trace_refs=[],
            ),
        ],
    )
    mech_report = MechanismAttributionReport(
        mechanism_attribution_id="mech_unselected_confidence",
        source_role_attribution_path=".",
        role_mechanisms={
            "frontend-coder": [],
            "content-author": [],
        },
    )

    report = MemberSelector().select(
        role_attribution_report=role_report,
        mechanism_attribution_report=mech_report,
        min_attribution_confidence=0.5,
        max_roles_per_run=1,
    )

    assert [target.role for target in report.targets] == ["frontend-coder"]
    assert len(report.unselected_attributions) == 1
    assert report.unselected_attributions[0].role == "content-author"
    assert report.unselected_attributions[0].issue_id == "issue_content"
    assert report.unselected_attributions[0].confidence == 0.7


# ---------------------------------------------------------------------------
# Test: current auto harness action policy
# ---------------------------------------------------------------------------


def test_member_action_policy_rejects_search_install_and_future_groups() -> None:
    """Current auto harness executable actions reject future extension actions."""
    rejected = [
        MemberOptimizationAction(
            action_id="search_001",
            role="explainer",
            action_group="skill",
            operation="search",
            action_type="skill_search",
            target_path="skills/",
        ),
        MemberOptimizationAction(
            action_id="install_001",
            role="explainer",
            action_group="skill",
            operation="install",
            action_type="skill_install",
            target_path="skills/",
            install_ref="skill://demo",
        ),
        MemberOptimizationAction(
            action_id="doc_001",
            role="explainer",
            action_group="documentation",
            operation="modify",
            action_type="doc_update",
            target_path="README.md",
        ),
    ]

    for action in rejected:
        assert not validate_action_policy(action, {"explainer"}).valid


def test_member_action_policy_accepts_current_expert_harness_surfaces() -> None:
    """prompt/tool/skill local ExpertHarness package changes are allowed."""
    valid_actions = [
        ("prompt", "identity.md", ["identity.md"], {}),
        (
            "prompt",
            "prompt_sections/files/style.md",
            ["prompt_sections/files/style.md", "prompt_sections/sections.yaml"],
            {"section_name": "style"},
        ),
        ("tool", "tools/tools.yaml", ["tools/tools.yaml"], {}),
        ("skill", "skills/answering/SKILL.md", ["skills/answering/SKILL.md", "skills/skills.yaml"], {}),
    ]

    for group, path, declared_paths, constraints in valid_actions:
        action = MemberOptimizationAction(
            action_id=f"{group}_001",
            role="explainer",
            action_group=group,
            operation="modify",
            action_type=f"{group}_modify",
            target_path=path,
            declared_write_paths=declared_paths,
            allowed_tools=["read_file", "edit_file"],
            constraints=constraints,
        )
        assert validate_action_policy(action, {"explainer"}).valid


def test_member_action_policy_requires_prompt_section_manifest_update() -> None:
    action = MemberOptimizationAction(
        action_id="prompt_section_001",
        role="explainer",
        action_group="prompt",
        operation="modify",
        action_type="prompt_modify",
        target_path="prompt_sections/files/style.md",
        declared_write_paths=["prompt_sections/files/style.md"],
        allowed_tools=["read_file", "edit_file"],
    )

    check = validate_action_policy(action, {"explainer"})

    assert not check.valid
    assert any("prompt_sections/sections.yaml" in error for error in check.errors)


def test_member_action_policy_requires_skill_manifest_update() -> None:
    action = MemberOptimizationAction(
        action_id="skill_001",
        role="explainer",
        action_group="skill",
        operation="modify",
        action_type="skill_modify",
        target_path="skills/answering/SKILL.md",
        declared_write_paths=["skills/answering/SKILL.md"],
        allowed_tools=["read_file", "edit_file"],
    )

    check = validate_action_policy(action, {"explainer"})

    assert not check.valid
    assert any("skills/skills.yaml" in error for error in check.errors)


def test_member_action_policy_requires_tool_manifest_update_for_tool_code() -> None:
    action = MemberOptimizationAction(
        action_id="tool_code_001",
        role="explainer",
        action_group="tool",
        operation="modify",
        action_type="tool_modify",
        target_path="tools/generated/demo.py",
        declared_write_paths=["tools/generated/demo.py"],
        allowed_tools=["read_file", "edit_file"],
    )

    check = validate_action_policy(action, {"explainer"})

    assert not check.valid
    assert any("tools/tools.yaml" in error for error in check.errors)


def test_member_action_policy_requires_rail_manifest_update_for_rail_code() -> None:
    action = MemberOptimizationAction(
        action_id="rail_code_001",
        role="explainer",
        action_group="rail",
        operation="modify",
        action_type="rail_modify",
        target_path="rails/demo.py",
        declared_write_paths=["rails/demo.py"],
        allowed_tools=["read_file", "edit_file"],
    )

    check = validate_action_policy(action, {"explainer"})

    assert not check.valid
    assert any("rails/rails.yaml" in error for error in check.errors)


def test_member_action_policy_accepts_loadable_rail_add() -> None:
    action = MemberOptimizationAction(
        action_id="rail_add_001",
        role="explainer",
        action_group="rail",
        operation="add",
        action_type="rail_add",
        target_path="rails/action_commitment.py",
        declared_write_paths=[
            "rails/action_commitment.py",
            "rails/rails.yaml",
        ],
        constraints={"class_name": "ActionCommitmentRail"},
    )

    assert validate_action_policy(action, {"explainer"}).valid


def test_member_action_policy_rejects_non_expert_harness_prompt_path() -> None:
    action = MemberOptimizationAction(
        action_id="bad_prompt",
        role="explainer",
        action_group="prompt",
        operation="modify",
        action_type="prompt_modify",
        target_path="prompts/system.md",
        declared_write_paths=["prompts/system.md"],
    )

    check = validate_action_policy(action, {"explainer"})

    assert not check.valid
    assert any("not allowed for action_group 'prompt'" in error for error in check.errors)


def test_member_action_policy_rejects_unmounted_prompt_section_root_file() -> None:
    action = MemberOptimizationAction(
        action_id="bad_prompt_section",
        role="explainer",
        action_group="prompt",
        operation="modify",
        action_type="prompt_modify",
        target_path="prompt_sections/instructions.md",
        declared_write_paths=["prompt_sections/instructions.md"],
    )

    check = validate_action_policy(action, {"explainer"})

    assert not check.valid
    assert any("not allowed for action_group 'prompt'" in error for error in check.errors)


def test_action_config_cannot_expand_current_policy() -> None:
    definitions = [
        ActionDefinition(
            name="prompt_modify",
            group="prompt",
            operation="modify",
            function="modify_prompt",
            purpose="Modify prompt",
        ),
        ActionDefinition(
            name="skill_search",
            group="skill",
            operation="search",
            function="search_skill",
            purpose="Search skills",
        ),
        ActionDefinition(
            name="dependency_add",
            group="dependency",
            operation="add",
            function="add_dependency",
            purpose="Add dependency",
        ),
    ]

    filtered = filter_action_definitions(definitions)

    assert [definition.name for definition in filtered] == ["prompt_modify", "skill_search"]


def test_action_definition_loader_filters_config_policy(action_group_yaml: Path) -> None:
    """Action config loading keeps only current Auto Harness-compatible actions."""
    definitions = load_action_definitions([str(action_group_yaml)])

    assert [definition.name for definition in definitions] == ["prompt_modify", "skill_search"]


def test_action_definition_loader_tolerates_missing_config(tmp_path: Path) -> None:
    """Missing action config files preserve existing lenient loading semantics."""
    definitions = load_action_definitions([str(tmp_path / "missing.yaml")])

    assert definitions == []


def test_action_definition_loader_resolves_builtin_group_names() -> None:
    """Symbolic group names must expose the package's executable operations."""
    definitions = load_action_definitions(["prompt", "skill", "tool"])

    assert {(item.group, item.operation) for item in definitions} == {
        ("prompt", "add"),
        ("prompt", "modify"),
        ("prompt", "remove"),
        ("skill", "add"),
        ("skill", "modify"),
        ("skill", "remove"),
        ("skill", "search"),
        ("tool", "add"),
        ("tool", "modify"),
        ("tool", "remove"),
    }
    assert (
        next(item for item in definitions if item.group == "skill" and item.operation == "search").requires_search
        is True
    )

    rail_definitions = load_action_definitions(["rail"])
    assert {(item.group, item.operation) for item in rail_definitions} == {
        ("rail", "add"),
        ("rail", "modify"),
        ("rail", "remove"),
    }


def _write_model_config(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "model_client_config": {
                    "client_provider": "MemberOptimizerMockLLM",
                    "api_key": "sk-test",
                    "api_base": "http://localhost",
                    "verify_ssl": False,
                },
                "model_request_config": {
                    "model": "member-optimizer-mock",
                    "temperature": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_member_optimizer_model_config_loads_standalone_yaml(tmp_path: Path) -> None:
    model_path = _write_model_config(tmp_path / "model.yaml")

    model = load_member_optimizer_model(str(model_path))

    assert model.model_client_config.client_provider == "MemberOptimizerMockLLM"
    assert model.model_config.model_name == "member-optimizer-mock"


def test_member_optimizer_model_config_disables_inner_sdk_retries(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model.yaml"
    model_path.write_text(
        yaml.safe_dump(
            {
                "model_client_config": {
                    "client_provider": "MemberOptimizerMockLLM",
                    "api_key": "sk-test",
                    "api_base": "http://localhost",
                    "verify_ssl": False,
                    "timeout": 120,
                    "max_retries": 20,
                },
                "model_request_config": {
                    "model": "member-optimizer-mock",
                    "temperature": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )

    model = load_member_optimizer_model(str(model_path))

    assert model.model_client_config.max_retries == 0


def test_member_optimizer_model_config_rejects_invalid_refs(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="model_config_ref is required"):
        load_member_optimizer_model("")

    with pytest.raises(RuntimeError, match="not found"):
        load_member_optimizer_model(str(tmp_path / "missing.yaml"))

    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text(yaml.safe_dump({"model": {}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="model_client_config"):
        load_member_optimizer_model(str(invalid_path))


def test_member_verifier_repairability_accepts_only_worktree_failures() -> None:
    from openjiuwen.rsi.member_optimizer.schema import (
        VerificationCheck,
    )
    from openjiuwen.rsi.member_optimizer.verification import (
        _role_is_repairable,
    )

    checks = [
        VerificationCheck(name="yaml_parse:explainer/harness.yaml", status="failed"),
        VerificationCheck(name="python_compile:explainer/tools/tool.py", status="failed"),
        VerificationCheck(name="expert_harness_load:explainer", status="failed"),
    ]

    assert _role_is_repairable(checks) is True


@pytest.mark.parametrize(
    "failed_check_name",
    [
        "action_result:action_001",
        "plan_schema",
        "execution_schema",
        "unknown_check:explainer",
    ],
)
def test_member_verifier_repairability_rejects_unrepairable_or_unknown_failures(
    failed_check_name: str,
) -> None:
    from openjiuwen.rsi.member_optimizer.schema import (
        VerificationCheck,
    )
    from openjiuwen.rsi.member_optimizer.verification import (
        _role_is_repairable,
    )

    assert (
        _role_is_repairable(
            [
                VerificationCheck(name=failed_check_name, status="failed"),
            ]
        )
        is False
    )


def test_member_verifier_repairability_rejects_mixed_failures() -> None:
    from openjiuwen.rsi.member_optimizer.schema import (
        VerificationCheck,
    )
    from openjiuwen.rsi.member_optimizer.verification import (
        _role_is_repairable,
    )

    checks = [
        VerificationCheck(name="yaml_parse:explainer/harness.yaml", status="failed"),
        VerificationCheck(name="action_result:action_001", status="failed"),
    ]

    assert _role_is_repairable(checks) is False


def test_member_optimizer_agent_output_parsers_extract_json_and_yaml_objects() -> None:
    assert parse_json_object_response('prefix ```json\n{"ok": true}\n``` suffix') == {
        "ok": True,
    }
    assert parse_json_object_response('{"ok": true}') == {"ok": True}
    assert parse_yaml_or_json_object_response("role: explainer\nconfidence: 0.7") == {
        "role": "explainer",
        "confidence": 0.7,
    }
    assert parse_yaml_or_json_object_response("```yaml\nrole: explainer\n```") == {
        "role": "explainer",
    }


def test_member_optimizer_extract_agent_text_prefers_non_empty_response_fields() -> None:
    response = {
        "text": "",
        "content": "",
        "answer": '{"status": "succeeded"}',
        "result_type": "answer",
    }

    assert extract_agent_text(response) == '{"status": "succeeded"}'


def test_member_optimizer_agent_output_structured_invoke_retries_invalid_json() -> None:
    agent = _FakeStructuredAgent(
        [
            {"text": "not json"},
            {"text": '{"status": "ok"}'},
        ]
    )

    result = asyncio.run(
        invoke_member_optimizer_agent_structured(
            agent=agent,
            agent_name="fake_agent",
            user_message="initial request",
            session_id="fake_session",
            retry_limit=1,
            parse_response=parse_json_object_response,
        )
    )

    assert result == {"status": "ok"}
    assert agent.queries[0] == "initial request"
    assert "Validation Error" in agent.queries[1]


def test_member_optimizer_agent_output_structured_invoke_retries_mojibake_json() -> None:
    agent = _FakeStructuredAgent(
        [
            {"text": '{"status": "ok", "summary": "\u7487\u8702\u8d1f\u93c2\u62cc\u5165"}'},
            {"text": '{"status": "ok"}'},
        ]
    )

    result = asyncio.run(
        invoke_member_optimizer_agent_structured(
            agent=agent,
            agent_name="fake_agent",
            user_message="initial request",
            session_id="fake_session",
            retry_limit=1,
            parse_response=parse_json_object_response,
        )
    )

    assert result == {"status": "ok"}
    assert "Validation Error" in agent.queries[1]


def test_member_optimizer_agent_output_structured_invoke_retries_validation_error() -> None:
    agent = _FakeStructuredAgent(
        [
            {"text": '{"status": "bad"}'},
            {"text": '{"status": "ok"}'},
        ]
    )

    result = asyncio.run(
        invoke_member_optimizer_agent_structured(
            agent=agent,
            agent_name="fake_agent",
            user_message="initial request",
            session_id="fake_session",
            retry_limit=1,
            parse_response=parse_json_object_response,
            validate_response=lambda raw: [] if raw.get("status") == "ok" else ["status must be ok"],
            build_retry_message=lambda previous, error: f"retry previous={previous.get('status')} error={error}",
        )
    )

    assert result == {"status": "ok"}
    assert agent.queries[1] == ("retry previous=bad error=validation errors: status must be ok")


def test_member_optimizer_agent_output_structured_invoke_raises_after_retry_exhaustion() -> None:
    agent = _FakeStructuredAgent(
        [
            {"text": "not json"},
            {"text": "still not json"},
        ]
    )

    with pytest.raises(RuntimeError, match="fake_agent failed after 2 attempts"):
        asyncio.run(
            invoke_member_optimizer_agent_structured(
                agent=agent,
                agent_name="fake_agent",
                user_message="initial request",
                session_id="fake_session",
                retry_limit=1,
                parse_response=parse_json_object_response,
            )
        )


class _FakeRoleAttributor:
    async def attribute(self, *, eval_ref, team_issues, candidate_roles, **kwargs):  # type: ignore[no-untyped-def]
        return RoleAttributionReport(
            attribution_id="attr_fake",
            candidate_roles=candidate_roles,
            assigned_role_issues=[
                RoleIssueAttribution(
                    issue_id=team_issues[0].issue_id,
                    role="explainer",
                    harness_ref_path=candidate_roles[0].harness_ref_path,
                    confidence=0.9,
                    evidence=[
                        {
                            "summary": team_issues[0].summary,
                            "severity": team_issues[0].severity,
                        }
                    ],
                    trace_refs=[{"case_id": "case_001"}],
                    rationale="fake attribution",
                    member_name="explainer",
                )
            ],
        )

    def write_report(self, report: RoleAttributionReport, output_dir: Path) -> Path:
        path = output_dir / "role_attribution.yaml"
        path.write_text(yaml.safe_dump({"assigned_role_issues": ["issue_001"]}), encoding="utf-8")
        return path


class _FakeMechanismAttributor:
    async def attribute(self, *, role_attribution_report, **kwargs):  # type: ignore[no-untyped-def]
        return MechanismAttributionReport(
            mechanism_attribution_id="mech_fake",
            role_mechanisms={
                "explainer": [
                    RoleMechanismAttribution(
                        issue_id="issue_001",
                        role="explainer",
                        mechanism_type="prompt",
                        failure_signature="prompt_instruction_mismatch",
                        confidence=0.8,
                    )
                ]
            },
        )

    def write_report(self, report: MechanismAttributionReport, output_dir: Path) -> Path:
        path = output_dir / "mechanism_attribution.yaml"
        path.write_text(yaml.safe_dump({"role_mechanisms": ["explainer"]}), encoding="utf-8")
        return path


class _FakePlanner:
    async def plan(self, *, targets, **kwargs):  # type: ignore[no-untyped-def]
        action = MemberOptimizationAction(
            action_id="action_001",
            role="explainer",
            action_group="prompt",
            operation="modify",
            action_type="prompt_refinement",
            target_path="identity.md",
            declared_write_paths=["identity.md"],
            description="Improve prompt",
        )
        return MemberOptimizationPlan(
            plan_id="plan_fake",
            targets=targets,
            actions=[action],
            action_waves=[["action_001"]],
        )

    def write_plan(self, plan: MemberOptimizationPlan, output_dir: Path) -> Path:
        path = output_dir / "plan.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "actions": [{"action_id": "action_001"}],
                    "action_waves": [["action_001"]],
                }
            ),
            encoding="utf-8",
        )
        return path


class _FakeExecutor:
    async def execute(self, *, plan, output_dir, **kwargs):  # type: ignore[no-untyped-def]
        run_dir = Path(output_dir)
        worktrees_dir = Path(kwargs["worktrees_dir"])
        integration = integration_worktree_path(worktrees_dir, "explainer")
        integration.mkdir(parents=True)
        (integration / "identity.md").write_text("# improved prompt\n", encoding="utf-8")
        (integration / "harness.yaml").write_text(
            yaml.safe_dump({"role": "explainer"}),
            encoding="utf-8",
        )
        return [
            MemberActionExecutionResult(
                action_id="action_001",
                role="explainer",
                status="succeeded",
                worktree_path=str(integration),
                artifact_path=str(run_dir / "actions" / "explainer" / "action_001" / "execution.json"),
                changed_files=["identity.md"],
                declared_write_paths=["identity.md"],
                merge_status="merged",
                merge_artifact_path=str(run_dir / "merges" / "explainer" / "merge.json"),
            )
        ]


class _RejectedPlanner:
    async def plan(self, *, targets, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            "Member action plan validation failed: action surface does not match diagnosed optimization_surface"
        )


@pytest.mark.parametrize("defer_publish", [False, True])
def test_member_optimizer_full_pipeline_fake_agents_closes_loop(
    tmp_path: Path,
    eval_ref_yaml: Path,
    harness_refs_yaml: Path,
    case_artifacts: None,
    defer_publish: bool,
) -> None:
    analysis_path = tmp_path / "analysis_ref.yaml"
    analysis_path.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "issue_id": "issue_001",
                        "category": "member_harness",
                        "severity": "high",
                        "summary": "Explainer prompt misses evidence.",
                        "affected_cases": ["case_001"],
                        "suspected_team_scope": "member",
                        "optimization_target": "member_harness",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "member_optimizations"
    model_path = _write_model_config(tmp_path / "model.yaml")
    optimizer = MemberOptimizer(
        MemberOptimizerConfig(model_config_ref=str(model_path)),
        experience_learner=object(),
        role_attributor=_FakeRoleAttributor(),
        mechanism_attributor=_FakeMechanismAttributor(),
        action_planner=_FakePlanner(),
        action_executor=_FakeExecutor(),
    )

    import asyncio

    ref_path = asyncio.run(
        optimizer.optimize(
            eval_ref_path=str(eval_ref_yaml),
            analysis_result_path=str(analysis_path),
            harness_refs_path=str(harness_refs_yaml),
            output_dir=str(output_dir),
            defer_publish=defer_publish,
        )
    )

    artifact = yaml.safe_load(Path(ref_path).read_text(encoding="utf-8"))
    refs_path = Path(artifact["optimized_harness_refs_path"])
    current_refs = yaml.safe_load(refs_path.read_text(encoding="utf-8"))
    assert artifact["status"] == "success"
    assert artifact["published_roles"] == ["explainer"]
    assert artifact["staged_roles"] == ["explainer"]
    assert artifact["verified_roles"] == ["explainer"]
    assert artifact["promoted_roles"] == []
    assert current_refs["staged_roles"] == ["explainer"]
    assert current_refs["verified_roles"] == ["explainer"]
    assert current_refs["promoted_roles"] == []
    layout = MemberOptimizerPathLayout.from_output_root(output_dir)
    expected_harness_dir = (
        layout.candidate_harness_dir(artifact["optimization_id"], "explainer")
        if defer_publish
        else layout.current_harness_dir("explainer")
    )
    assert Path(current_refs["harness_refs"]["explainer"]) == expected_harness_dir
    assert (output_dir / "current_harness_refs.yaml").exists() is (not defer_publish)
    if defer_publish:
        assert refs_path.name == "candidate_harness_refs.yaml"
        assert current_refs["last_attempt"]["published_roles"] == ["explainer"]
    assert "diagnostician" in current_refs["harness_refs"]


def test_member_optimizer_rejects_invalid_llm_plan_without_aborting_run(
    tmp_path: Path,
    eval_ref_yaml: Path,
    harness_refs_yaml: Path,
    case_artifacts: None,
) -> None:
    analysis_path = tmp_path / "analysis_ref.yaml"
    analysis_path.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "issue_id": "issue_001",
                        "category": "member_harness",
                        "severity": "high",
                        "summary": "The solver needs a prompt-section correction.",
                        "affected_cases": ["case_001"],
                        "suspected_team_scope": "member",
                        "optimization_target": "member_harness",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "member_optimizations"
    optimizer = MemberOptimizer(
        MemberOptimizerConfig(model_config_ref=str(_write_model_config(tmp_path / "model.yaml"))),
        experience_learner=object(),
        role_attributor=_FakeRoleAttributor(),
        mechanism_attributor=_FakeMechanismAttributor(),
        action_planner=_RejectedPlanner(),
        action_executor=_FakeExecutor(),
    )

    import asyncio

    ref_path = asyncio.run(
        optimizer.optimize(
            eval_ref_path=str(eval_ref_yaml),
            analysis_result_path=str(analysis_path),
            harness_refs_path=str(harness_refs_yaml),
            output_dir=str(output_dir),
            defer_publish=True,
        )
    )

    artifact = yaml.safe_load(Path(ref_path).read_text(encoding="utf-8"))
    assert artifact["status"] == "planning_rejected"
    assert artifact["published_roles"] == []
    assert artifact["skipped_roles"] == ["explainer"]
    assert "Member action plan validation failed" in artifact["metadata"]["reason"]
    assert (Path(ref_path).parent / "action_planning_error.json").is_file()


def test_deferred_failed_attempt_does_not_rewrite_accepted_refs(tmp_path: Path) -> None:
    """A failed later batch must not overwrite an already accepted refs artifact."""
    accepted_refs = tmp_path / "accepted_candidate_refs.yaml"
    accepted_payload = {
        "version": 1,
        "harness_refs": {"solver": "harnesses/solver-v2"},
        "published_roles": ["solver"],
        "failed_roles": [],
    }
    accepted_refs.write_text(
        yaml.safe_dump(accepted_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    original = accepted_refs.read_bytes()
    optimizer = MemberOptimizer(MemberOptimizerConfig(model_config_ref="unused"))

    import asyncio

    ref_path = asyncio.run(
        optimizer._write_noop_artifact(
            eval_ref_path="eval_ref.yaml",
            analysis_result_path="analysis_ref.yaml",
            harness_refs_path=str(accepted_refs),
            output_dir=str(tmp_path / "member_optimizations"),
            status="failed",
            reason="verification failed",
            roles=["solver"],
            defer_publish=True,
        )
    )

    artifact = yaml.safe_load(Path(ref_path).read_text(encoding="utf-8"))
    assert accepted_refs.read_bytes() == original
    assert artifact["optimized_harness_refs_path"] == str(accepted_refs)
    assert not (tmp_path / "member_optimizations" / "current_harness_refs.yaml").exists()


class _NoopExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _InspectingToolScaffoldExecutorAgent:
    def __init__(self) -> None:
        self.saw_tool_scaffold = False

    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        tool_path = Path(action_worktree) / action.target_path
        manifest_path = Path(action_worktree) / "tools" / "tools.yaml"
        tool_text = tool_path.read_text(encoding="utf-8") if tool_path.is_file() else ""
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        self.saw_tool_scaffold = (
            "ToolCard(" in tool_text
            and "input_params={" in tool_text
            and "'type': 'object'" in tool_text
            and isinstance(manifest, dict)
            and bool(manifest.get("tools"))
        )
        if not self.saw_tool_scaffold:
            return {
                "status": "failed",
                "declared_write_paths": action.declared_write_paths or [action.target_path],
                "error": "tool scaffold was not available before action execution",
            }
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _DangerousToolWritingExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        tool_path = Path(action_worktree) / action.target_path
        tool_path.parent.mkdir(parents=True, exist_ok=True)
        tool_path.write_text(
            "\n".join(
                [
                    "import os",
                    "",
                    "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                    "",
                    "",
                    "class RiskChecker(Tool):",
                    "    def __init__(self):",
                    "        super().__init__(",
                    "            ToolCard(",
                    "                id='risk_checker',",
                    "                name='risk_checker',",
                    "                description='Check delivery risk.',",
                    "                input_params={",
                    "                    'type': 'object',",
                    "                    'properties': {},",
                    "                    'required': [],",
                    "                },",
                    "            )",
                    "        )",
                    "",
                    "    async def invoke(self, inputs, **kwargs):",
                    "        return {'cwd': os.getcwd()}",
                    "",
                    "    async def stream(self, inputs, **kwargs):",
                    "        yield await self.invoke(inputs, **kwargs)",
                ]
            ),
            encoding="utf-8",
        )
        manifest_path = Path(action_worktree) / "tools" / "tools.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            yaml.safe_dump({"tools": [{"file": action.target_path, "class_name": "RiskChecker"}]}),
            encoding="utf-8",
        )
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _SafeContentToolWritingExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        tool_path = Path(action_worktree) / action.target_path
        tool_path.parent.mkdir(parents=True, exist_ok=True)
        tool_path.write_text(
            "\n".join(
                [
                    "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                    "",
                    "",
                    "class RiskChecker(Tool):",
                    "    def __init__(self):",
                    "        super().__init__(",
                    "            ToolCard(",
                    "                id='risk_checker',",
                    "                name='risk_checker',",
                    "                description='Check supplied artifact text for required evidence.',",
                    "                input_params={",
                    "                    'type': 'object',",
                    "                    'properties': {",
                    "                        'artifact_text': {'type': 'string'},",
                    "                        'required_phrase': {'type': 'string'},",
                    "                    },",
                    "                    'required': ['artifact_text', 'required_phrase'],",
                    "                },",
                    "            )",
                    "        )",
                    "",
                    "    async def invoke(self, inputs, **kwargs):",
                    "        artifact_text = str(inputs.get('artifact_text', '')) if isinstance(inputs, dict) else ''",
                    "        required_phrase = str(inputs.get('required_phrase', '')) if isinstance(inputs, dict) else ''",
                    "        found = bool(required_phrase and required_phrase in artifact_text)",
                    "        return {'status': 'passed' if found else 'failed', 'found': found}",
                    "",
                    "    async def stream(self, inputs, **kwargs):",
                    "        yield await self.invoke(inputs, **kwargs)",
                ]
            ),
            encoding="utf-8",
        )
        manifest_path = Path(action_worktree) / "tools" / "tools.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            yaml.safe_dump({"tools": [{"file": action.target_path, "class_name": "RiskChecker"}]}),
            encoding="utf-8",
        )
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _OutOfBoundsExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        (Path(action_worktree) / "other.md").write_text("unexpected\n", encoding="utf-8")
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _WritingExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        target = Path(action_worktree) / action.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# changed\n", encoding="utf-8")
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _SkillWritingExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        target = Path(action_worktree) / action.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(
                [
                    "---",
                    "name: preserve-analyze-fix",
                    "description: Preserve original files, analyze state, then apply fixes.",
                    "---",
                    "",
                    "# Preserve Analyze Fix",
                    "",
                    "Back up original files before editing and inspect state before repairs.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _SkillWritingNoCompletionExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        target_path = Path(action_worktree) / action.target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            "---\n"
            "name: turn_state_transition_verification\n"
            "description: Verify complete turn state transitions.\n"
            "---\n\n"
            "# Turn State Transition Verification\n\n"
            "Enumerate every per-turn flag and verify each reset.\n",
            encoding="utf-8",
        )
        return {
            "status": "failed",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "response_text": "Max iterations reached without completion",
            "error": "agent response must be a structured file_writes JSON object",
        }


class _FailingSkillAcquisition:
    def acquire(self, *, action_worktree, query):  # type: ignore[no-untyped-def]
        return SkillAcquisitionResult(
            status="failed",
            query=query,
            error="all skill candidates were rejected",
        )


class _RailShortManifestExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        rail_path = Path(action_worktree) / action.target_path
        rail_path.parent.mkdir(parents=True, exist_ok=True)
        rail_path.write_text(
            "\n".join(
                [
                    "from openjiuwen.core.single_agent.rail.base import AgentRail",
                    "",
                    "",
                    "class ConflictMarkerGuardRail(AgentRail):",
                    "    async def before_model_call(self, ctx):",
                    "        return None",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (Path(action_worktree) / "rails" / "rails.yaml").write_text(
            yaml.safe_dump(
                {
                    "rails": [
                        {
                            "file": "conflict_marker_guard.py",
                            "class_name": "ConflictMarkerGuardRail",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _BrokenRailExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        rail_path = Path(action_worktree) / action.target_path
        rail_path.parent.mkdir(parents=True, exist_ok=True)
        rail_path.write_text(
            "\n".join(
                [
                    "from openjiuwen.core.single_agent.rail.base import AgentRail",
                    "",
                    "",
                    "class BrokenRail(AgentRail):",
                    "    def description(self):",
                    '        return "unterminated',
                ]
            ),
            encoding="utf-8",
        )
        (Path(action_worktree) / "rails" / "rails.yaml").write_text(
            yaml.safe_dump(
                {
                    "rails": [
                        {
                            "file": "rails/broken.py",
                            "class_name": "BrokenRail",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "succeeded",
            "declared_write_paths": action.declared_write_paths or [action.target_path],
            "error": "",
        }


class _RepairingHarnessRepairAgent:
    def __init__(
        self,
        *,
        repair_text: str = "repair attempted",
        raise_on_repair: bool = False,
    ) -> None:
        self.repair_text = repair_text
        self.raise_on_repair = raise_on_repair
        self.repair_calls: list[dict[str, object]] = []

    async def repair_role(
        self,
        role_integration_worktree: Path,
        role: str,
        failed_checks: list[dict[str, object]],
    ) -> dict[str, object]:
        self.repair_calls.append(
            {
                "role": role,
                "worktree": role_integration_worktree,
                "failed_checks": failed_checks,
            }
        )
        if self.raise_on_repair:
            raise RuntimeError("repair failed")
        return {
            "status": "attempted",
            "repairs": [
                {
                    "role": role,
                    "action": "deepagent_repair",
                    "description": self.repair_text,
                    "status": "attempted",
                    "error": "",
                }
            ],
        }


class _YamlRepairingHarnessRepairAgent(_RepairingHarnessRepairAgent):
    async def repair_role(
        self,
        role_integration_worktree: Path,
        role: str,
        failed_checks: list[dict[str, object]],
    ) -> dict[str, object]:
        result = await super().repair_role(role_integration_worktree, role, failed_checks)
        (role_integration_worktree / "harness.yaml").write_text(
            yaml.safe_dump({"role": role, "version": "1.0"}),
            encoding="utf-8",
        )
        return result


class _PythonRepairingHarnessRepairAgent(_RepairingHarnessRepairAgent):
    async def repair_role(
        self,
        role_integration_worktree: Path,
        role: str,
        failed_checks: list[dict[str, object]],
    ) -> dict[str, object]:
        result = await super().repair_role(role_integration_worktree, role, failed_checks)
        broken = role_integration_worktree / "tools" / "broken.py"
        broken.write_text("def ok():\n    return 1\n", encoding="utf-8")
        return result


def _single_action_plan(harness_dir: Path) -> MemberOptimizationPlan:
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="action_001",
        role="explainer",
        action_group="prompt",
        operation="modify",
        action_type="prompt_refinement",
        target_path="identity.md",
        declared_write_paths=["identity.md"],
    )
    return MemberOptimizationPlan(
        plan_id="plan_001",
        targets=[target],
        actions=[action],
        action_waves=[["action_001"]],
    )


def _illegal_action_plan(harness_dir: Path) -> MemberOptimizationPlan:
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="action_illegal",
        role="explainer",
        action_group="skill",
        operation="install",
        action_type="skill_install",
        target_path="skills/",
        declared_write_paths=["skills/"],
        install_ref="skill://external",
    )
    return MemberOptimizationPlan(
        plan_id="plan_illegal",
        targets=[target],
        actions=[action],
        action_waves=[["action_illegal"]],
    )


def _static_role_plan(harness_dir: Path, role: str = "explainer") -> MemberOptimizationPlan:
    return MemberOptimizationPlan(
        plan_id=f"plan_static_{role}",
        targets=[
            MemberOptimizationTarget(
                role=role,
                harness_ref_path=str(harness_dir / role),
                attributed_issue_ids=["issue_001"],
                confidence=0.9,
            )
        ],
        actions=[],
        action_waves=[],
    )


def test_member_executor_writes_under_numbered_run_dir(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    executor = MemberActionExecutor(executor_agent=_WritingExecutorAgent())

    import asyncio

    results = asyncio.run(
        executor.execute(
            plan=_single_action_plan(two_role_harness_dir),
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert integration_worktree_path(run_dir / "wt", "explainer").is_dir()
    assert (run_dir / "execution_results.json").is_file()
    assert results[0].status == "succeeded"
    assert results[0].merge_status == "merged"


def test_member_executor_fails_success_without_real_declared_change(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    executor = MemberActionExecutor(executor_agent=_NoopExecutorAgent())

    import asyncio

    results = asyncio.run(
        executor.execute(
            plan=_single_action_plan(two_role_harness_dir),
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "failed"
    assert "no declared_write_paths changed" in results[0].error


def test_member_executor_default_agent_fails_success_without_real_declared_change(
    monkeypatch,
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    from openjiuwen.rsi.member_optimizer import action_executor as action_executor_module

    class NoopDeepAgent:
        async def invoke(self, inputs, session=None):  # type: ignore[no-untyped-def]
            return {"text": "done"}

    def fake_create_action_execution_agent(**kwargs):  # type: ignore[no-untyped-def]
        return NoopDeepAgent()

    monkeypatch.setattr(
        action_executor_module,
        "create_action_execution_agent",
        fake_create_action_execution_agent,
    )
    run_dir = tmp_path / "member_optimization_001"
    executor = MemberActionExecutor()

    import asyncio

    results = asyncio.run(
        executor.execute(
            plan=_single_action_plan(two_role_harness_dir),
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "failed"
    assert "structured file_writes JSON object" in results[0].error
    payload = json.loads(Path(results[0].artifact_path).read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["changed_files"] == []


def test_member_executor_scaffolds_prompt_section_when_agent_writes_no_files(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="act_prompt_section",
        role="explainer",
        action_group="prompt",
        operation="add",
        action_type="prompt_improvement",
        target_path="prompt_sections/files/slide_labeling_rules.md",
        description="Always assign every slide a required topic label.",
        rationale="The evaluator penalized structural slides without required-topic labels.",
        declared_write_paths=[
            "prompt_sections/files/slide_labeling_rules.md",
            "prompt_sections/sections.yaml",
        ],
        constraints={"section_name": "slide_labeling_rules", "priority": 30},
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_prompt_section",
        targets=[target],
        actions=[action],
        action_waves=[["act_prompt_section"]],
    )
    executor = MemberActionExecutor(executor_agent=_NoopExecutorAgent())

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "succeeded"
    integration = integration_worktree_path(run_dir / "wt", "explainer")
    section_path = integration / "prompt_sections" / "files" / "slide_labeling_rules.md"
    assert "Always assign every slide" in section_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load((integration / "prompt_sections" / "sections.yaml").read_text(encoding="utf-8"))
    assert {
        "name": "slide_labeling_rules",
        "file": "prompt_sections/files/slide_labeling_rules.md",
        "priority": 30,
    } in manifest["sections"]


def test_member_executor_scaffolds_skill_when_agent_writes_no_files(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="act_skill_add",
        role="explainer",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/visual_design_spec/SKILL.md",
        description="Create a reusable visual design specification method.",
        rationale="The evaluator found missing layout, hierarchy, and chart mapping capability.",
        declared_write_paths=[
            "skills/visual_design_spec/SKILL.md",
            "skills/skills.yaml",
        ],
        constraints={"skill_name": "visual_design_spec"},
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_skill_add",
        targets=[target],
        actions=[action],
        action_waves=[["act_skill_add"]],
    )
    executor = MemberActionExecutor(executor_agent=_NoopExecutorAgent())

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "succeeded"
    integration = integration_worktree_path(run_dir / "wt", "explainer")
    skill_path = integration / "skills" / "visual_design_spec" / "SKILL.md"
    skill_text = skill_path.read_text(encoding="utf-8")
    assert "name: visual_design_spec" in skill_text
    registry = yaml.safe_load((integration / "skills" / "skills.yaml").read_text(encoding="utf-8"))
    assert "skills/visual_design_spec" in registry["skills"]


def test_member_executor_rejects_file_written_without_final_file_writes_json(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="act_skill_add",
        role="explainer",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/turn_state_transition_verification/SKILL.md",
        description="Create a reusable turn-state transition verification method.",
        rationale="The evaluator found incomplete turn-state reset logic.",
        declared_write_paths=[
            "skills/turn_state_transition_verification/SKILL.md",
            "skills/skills.yaml",
        ],
        constraints={"skill_name": "turn_state_transition_verification"},
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_skill_add",
        targets=[target],
        actions=[action],
        action_waves=[["act_skill_add"]],
    )
    executor = MemberActionExecutor(executor_agent=_SkillWritingNoCompletionExecutorAgent())

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "failed"
    assert "structured file_writes JSON object" in results[0].error
    integration = integration_worktree_path(run_dir / "wt", "explainer")
    assert not (integration / "skills" / "turn_state_transition_verification" / "SKILL.md").exists()


def _valid_chunked_skill_content(name: str) -> str:
    return f"""---
name: {name}
description: >-
  Verify enum and protocol contract changes before implementation and again at
  final regression validation when boundary behavior can be silently lost.
---

# Enum Contract Verification

## Decision Capsule

- Invariant: Every explicitly required protocol operation remains observable.
- Discriminator: The exact requested operation separates a complete patch from a convenience subset.
- Positive case: The requested operation returns the expected value.
- Boundary case: The operation raises the protocol-defined terminal exception.
- Acceptance probe: Invoke the requested operation directly and retain its exit status.
- Invalid substitute: A nearby operation on a wrapper or returned helper is not equivalent.
- Action trigger: Once the direct probe selects the contract, implement the smallest edit before further exploration.

## Decision Rule

Consult this Skill before selecting an implementation. Distinguish a complete
protocol contract from a partial convenience implementation with positive,
boundary, and observable cases.

## Procedure

1. Enumerate each explicitly requested protocol operation.
2. Build a positive and boundary probe for each operation.
3. Select the implementation only after the observables distinguish the options.

## Verification

Run the contract probe and authoritative tests, retaining commands, exit status,
and checked assertions as evidence.
"""


def test_action_executor_accepts_complete_skill_as_structured_file_write() -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    action = MemberOptimizationAction(
        action_id="act_skill_add",
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/enum_contract_verify/SKILL.md",
        description="Verify enum contracts.",
        declared_write_paths=[
            "skills/enum_contract_verify/SKILL.md",
            "skills/skills.yaml",
        ],
    )
    content = _valid_chunked_skill_content("enum_contract_verify")
    parsed = MemberActionExecutorAgent("unused")._parse_action_response(
        json.dumps(
            {
                "action_id": action.action_id,
                "status": "succeeded",
                "file_writes": [{"path": action.target_path, "content": content}],
                "errors": [],
            }
        )
    )

    assert parsed["file_writes"][0]["content"] == content


def test_action_executor_writes_complete_skill_from_one_model_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    action_id = "act_skill_add"
    target_path = "skills/enum_contract_verify/SKILL.md"
    content = _valid_chunked_skill_content("enum_contract_verify")
    action_worktree = tmp_path / "action_wt"
    action_worktree.mkdir()
    calls: list[str] = []

    async def invoke_direct(self, message):  # type: ignore[no-untyped-def]
        calls.append(message)
        assert not (action_worktree / target_path).exists()
        return json.dumps(
            {
                "action_id": action_id,
                "status": "succeeded",
                "file_writes": [{"path": target_path, "content": content}],
                "errors": [],
            }
        )

    monkeypatch.setattr(MemberActionExecutorAgent, "_invoke_direct_action", invoke_direct)
    action = MemberOptimizationAction(
        action_id=action_id,
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path=target_path,
        description="Verify enum contracts.",
        declared_write_paths=[target_path, "skills/skills.yaml"],
    )

    result = asyncio.run(
        MemberActionExecutorAgent("unused").execute_action(
            action_worktree=action_worktree,
            action=action,
            plan_summary="Create a reusable enum contract verification Skill.",
            allowed_skills=[],
            allowed_tools=[],
        )
    )

    assert result["status"] == "succeeded"
    assert len(calls) == 1
    assert "artifact_chunk" not in calls[0]
    written_skill = (action_worktree / target_path).read_text(encoding="utf-8")
    assert yaml.safe_load(written_skill.split("---", 2)[1])["name"] == "enum_contract_verify"
    expected_body = content[content.index("# Enum Contract Verification") :]
    assert written_skill[written_skill.index("# Enum Contract Verification") :] == expected_body
    registry = yaml.safe_load((action_worktree / "skills" / "skills.yaml").read_text(encoding="utf-8"))
    assert "skills/enum_contract_verify" in registry["skills"]


def test_action_executor_retries_malformed_complete_skill_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    action_id = "act_skill_add"
    target_path = "skills/enum_contract_verify/SKILL.md"
    content = _valid_chunked_skill_content("enum_contract_verify")
    messages: list[str] = []

    async def invoke_direct(self, message):  # type: ignore[no-untyped-def]
        messages.append(message)
        if len(messages) == 1:
            return '{"action_id":"act_skill_add","file_writes":"truncated'
        assert "Previous Invalid Output" in message
        return json.dumps(
            {
                "action_id": action_id,
                "status": "succeeded",
                "file_writes": [{"path": target_path, "content": content}],
                "errors": [],
            }
        )

    monkeypatch.setattr(MemberActionExecutorAgent, "_invoke_direct_action", invoke_direct)
    action_worktree = tmp_path / "action_wt"
    action_worktree.mkdir()
    action = MemberOptimizationAction(
        action_id=action_id,
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path=target_path,
        description="Verify enum contracts.",
        declared_write_paths=[target_path, "skills/skills.yaml"],
    )

    result = asyncio.run(
        MemberActionExecutorAgent("unused").execute_action(
            action_worktree=action_worktree,
            action=action,
            plan_summary="Create a reusable enum contract verification Skill.",
            allowed_skills=[],
            allowed_tools=[],
        )
    )

    assert result["status"] == "succeeded"
    assert len(messages) == 2


def test_action_executor_does_not_publish_invalid_assembled_skill(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    action_id = "act_skill_add"
    target_path = "skills/incomplete_skill/SKILL.md"

    async def invoke_direct(self, message):  # type: ignore[no-untyped-def]
        return json.dumps(
            {
                "action_id": action_id,
                "status": "succeeded",
                "file_writes": [
                    {
                        "path": target_path,
                        "content": "---\nname: incomplete_skill\ndescription: incomplete\n---\n",
                    }
                ],
                "errors": [],
            }
        )

    monkeypatch.setattr(MemberActionExecutorAgent, "_invoke_direct_action", invoke_direct)
    action_worktree = tmp_path / "action_wt"
    action_worktree.mkdir()
    action = MemberOptimizationAction(
        action_id=action_id,
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path=target_path,
        description="Incomplete Skill.",
        declared_write_paths=[target_path, "skills/skills.yaml"],
    )

    result = asyncio.run(
        MemberActionExecutorAgent("unused").execute_action(
            action_worktree=action_worktree,
            action=action,
            plan_summary="Create a Skill.",
            allowed_skills=[],
            allowed_tools=[],
        )
    )

    assert result["status"] == "failed"
    assert "must contain runtime instructions" in result["error"]
    assert not (action_worktree / target_path).exists()
    assert not (action_worktree / "skills" / "skills.yaml").exists()


def test_action_execution_prompt_requires_native_complete_skill_artifact() -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    action = MemberOptimizationAction(
        action_id="act_skill_add",
        role="explainer",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/turn_state_transition_verification/SKILL.md",
        declared_write_paths=[
            "skills/turn_state_transition_verification/SKILL.md",
            "skills/skills.yaml",
        ],
    )
    message = MemberActionExecutorAgent("unused")._build_user_message(
        action,
        plan_summary="Create a turn-state verification skill.",
        allowed_skills=[],
        allowed_tools=["read_file", "write_file", "edit_file"],
    )

    assert "Do not call write_file or edit_file to apply the change" in message
    assert "complete replacement content in `file_writes`" in message
    assert '"file_writes"' in message
    assert "artifact_chunk" not in message
    assert "decision contract's causal distinction" in message
    assert "public task only for broad task-area vocabulary" in message
    assert "must not broaden to a sibling mechanism" in message
    assert "native reusable Skill" in message
    assert "Use any Markdown structure" in message
    assert "not an optimizer audit report" in message
    assert "preserve required_behavior and avoid forbidden_behavior" in message
    assert "observable that ends investigation" in message
    assert "Do not promote syntax from the failed patch into a general rule" in message
    assert "non-tautological acceptance probe" in message
    assert "Merely avoiding an exception does not establish" in message
    assert "do not pad the Skill for a fixed template" in message
    assert "case ids, optimizer provenance, evaluator-only names" in message
    assert "## Evidence Boundary" not in message
    assert "## Regression Attribution Gate" not in message
    assert "Allowed Tools: []" in message


def test_action_execution_projects_only_public_runtime_contract() -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    action = MemberOptimizationAction(
        action_id="act_private_boundary",
        role="solver",
        action_group="prompt",
        operation="add",
        action_type="prompt_section_creation",
        target_path="prompt_sections/files/protocol.md",
        description="Fix private test_secret_contract after verifier failure.",
        rationale="The hidden verifier test_secret_contract is authoritative.",
        risk_notes=["Do not regress issue_private."],
        declared_write_paths=[
            "prompt_sections/files/protocol.md",
            "prompt_sections/sections.yaml",
        ],
        constraints={
            "section_name": "protocol",
            "optimization_contracts": [
                {
                    "hypothesis_id": "hyp_abcdef123456",
                    "source_issue_id": "issue_private",
                    "target_case_ids": ["repo__project-123"],
                    "public_trigger": [
                        {
                            "case_id": "repo__project-123",
                            "task": "Make the object support direct iteration.",
                        }
                    ],
                    "required_behavior": "Implement the directly requested protocol",
                    "forbidden_behavior": ["Do not substitute a convenience wrapper"],
                    "decisive_probe": {
                        "verifier_observations": {
                            "failed_tests": ["tests/test_api.py::test_secret_contract"],
                        },
                    },
                }
            ],
        },
    )

    projection = _runtime_contract_projection(action)
    message = MemberActionExecutorAgent("unused")._build_user_message(
        action,
        plan_summary="Private plan mentions test_secret_contract.",
        allowed_skills=[],
        allowed_tools=[],
    )

    assert projection == [
        {
            "public_tasks": ["Make the object support direct iteration"],
            "required_behavior": "Implement the directly requested protocol",
            "forbidden_behavior": ["Do not substitute a convenience wrapper"],
        }
    ]
    assert "Make the object support direct iteration" in message
    assert "Implement the directly requested protocol" in message
    assert "convenience wrapper" in message
    assert "test_secret_contract" not in message
    assert "repo__project-123" not in message
    assert "hyp_abcdef123456" not in message
    assert "issue_private" not in message
    assert "Private plan" not in message
    assert "hidden verifier" not in message


def test_action_execution_preserves_sanitized_directional_decision_contract() -> None:
    action = MemberOptimizationAction(
        action_id="act_decision_contract",
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/iterator_lifecycle/SKILL.md",
        constraints={
            "optimization_contracts": [
                {
                    "public_trigger": [
                        {
                            "task": "Make the value object iterable and support direct next().",
                        }
                    ],
                    "required_behavior": "Preserve the object's direct iterator lifecycle.",
                    "forbidden_behavior": [],
                    "decision_contract": {
                        "wrong_decision": (
                            "The solver added __iter__ but omitted __next__; test_next "
                            "still failed (trace_id=case__solver__case, role=solver, "
                            "message_index=17, step=step_18)."
                        ),
                        "causal_distinction": (
                            "Returning an external iterator is not equivalent to making "
                            "the object own its direct-next lifecycle."
                        ),
                        "required_action": (
                            "Implement __iter__ initialization and stateful __next__ consumption on the object."
                        ),
                        "acceptance_observable": (
                            "After initialization, repeated next() calls yield values and "
                            "then terminate; direct next() before initialization preserves "
                            "the specified boundary behavior."
                        ),
                        "scope_boundary": ["Do not treat __iter__ returning iter(str(obj)) as equivalent."],
                    },
                }
            ],
        },
    )

    projection = _runtime_contract_projection(action)
    decision = projection[0]["decision_contract"]

    assert "__iter__" in decision["wrong_decision"]
    assert "__next__" in decision["wrong_decision"]
    assert "test_next" not in json.dumps(decision)
    assert "trace_id" not in json.dumps(decision)
    assert decision["required_action"].startswith("Implement __iter__ initialization")
    assert "external iterator" in decision["causal_distinction"]
    assert "iter(str(obj))" in decision["scope_boundary"][0]


def test_action_execution_compiles_evaluator_evidence_into_runtime_semantics() -> None:
    action = MemberOptimizationAction(
        action_id="act_contaminated_contract",
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/protocol/SKILL.md",
        constraints={
            "optimization_contracts": [
                {
                    "public_trigger": [{"task": "Support direct iteration."}],
                    "required_behavior": (
                        "Make test_secret_contract pass without copying FAIL_TO_PASS "
                        "metadata into the runtime artifact after reading test_patch "
                        "from the official verifier."
                    ),
                    "forbidden_behavior": ["Do not special-case repo__project-123 or hidden tests."],
                }
            ],
        },
    )

    projection = _runtime_contract_projection(action)
    serialized = json.dumps(projection, ensure_ascii=False)

    assert projection[0]["required_behavior"].startswith("Make the observed check pass")
    assert "required checks metadata" in projection[0]["required_behavior"]
    assert "supplied acceptance-test contract" in projection[0]["required_behavior"]
    assert "acceptance evaluation" in projection[0]["required_behavior"]
    assert projection[0]["forbidden_behavior"] == ["Do not special-case the task or unseen checks"]
    assert "test_secret_contract" not in serialized
    assert "FAIL_TO_PASS" not in serialized
    assert "repo__project-123" not in serialized
    assert "hidden tests" not in serialized
    assert "test_patch" not in serialized
    assert "official verifier" not in serialized


def test_generated_runtime_artifact_leaves_semantic_judgment_to_evaluation(
    tmp_path: Path,
) -> None:
    target = "prompt_sections/files/protocol.md"
    path = tmp_path / target
    path.parent.mkdir(parents=True)
    path.write_text(
        "Implement the protocol so test_secret_contract passes.\n",
        encoding="utf-8",
    )
    action = MemberOptimizationAction(
        action_id="act_private_boundary",
        role="solver",
        action_group="prompt",
        operation="add",
        action_type="prompt_section_creation",
        target_path=target,
        declared_write_paths=[target],
        constraints={
            "optimization_contracts": [
                {
                    "public_trigger": [{"task": "Support direct iteration."}],
                    "required_behavior": "Implement direct iteration.",
                    "forbidden_behavior": [],
                    "decisive_probe": {
                        "verifier_observations": {
                            "failed_tests": ["tests/test_api.py::test_secret_contract"],
                        },
                    },
                }
            ],
        },
    )

    errors = _validate_generated_action_resources(tmp_path, action, [target])

    assert errors == []


def test_generated_skill_contract_does_not_gate_optimizer_provenance_text() -> None:
    content = """\
## Causal Discriminator
Distinguish the semantic outcomes.
## Evidence Boundary
- [authoritative task input] The solver used a getattr fallback.
- [verifier result] Populate at runtime from the current verifier output; no current-case verifier fact is embedded here.
- [evaluated-agent trajectory] Treat optimizer rationale as a hypothesis until the runtime role reproduces it.
## Transferability
Applies to serializers and renderers.
Proceed to `## Regression Attribution Gate` and `## Acceptance Loop` after the probe.
## Regression Attribution Gate
Use set -o pipefail, compare the patched workspace with a clean baseline, treat
every nonzero result as a blocker, and never dismiss FAIL_TO_PASS tests.
## Acceptance Loop
Run a discriminating probe.
"""

    _validate_generated_skill_contract(content)


def test_optimization_hypothesis_is_immutable_and_case_bound(tmp_path: Path) -> None:
    analysis_ref = tmp_path / "analysis_ref.yaml"
    analysis_ref.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "issue_id": "issue_protocol",
                        "category": "semantic_contract",
                        "severity": "high",
                        "summary": "The direct iterator protocol remains incomplete.",
                        "affected_cases": ["case_pydicom"],
                        "evidence": [{"case_id": "case_pydicom", "step_pointer": "step_25"}],
                        "optimization_target": "member_harness",
                        "recommendation": "Implement and probe stateful direct __next__ semantics.",
                        "metadata": {
                            "forbidden_behavior": ["Treat __iter__ alone as sufficient."],
                            "attribution": {
                                "target_ref": "member_harness.solver.skill",
                                "general_mechanism": (
                                    "A directly requested stateful protocol must implement and "
                                    "probe its direct operation."
                                ),
                                "critical_mistake": ("Do not substitute a returned iterator for direct __next__."),
                            },
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    hypothesis_path = compile_optimization_hypotheses(
        analysis_ref_path=str(analysis_ref),
        cases=[
            {
                "case_id": "case_pydicom",
                "input": (
                    "SWE-bench Lite instance: case_pydicom\n"
                    "Repository: example/project\n"
                    "Base commit: abc123\n\n"
                    "Support next(person_name).\n\n"
                    "Work in the checked-out repository."
                ),
            }
        ],
        output_path=tmp_path / "optimization_hypotheses.yaml",
    )

    hypotheses = load_optimization_hypotheses(hypothesis_path)

    assert len(hypotheses) == 1
    assert hypotheses[0]["target_case_ids"] == ["case_pydicom"]
    assert hypotheses[0]["required_behavior"] == (
        "A directly requested stateful protocol must implement and probe its direct operation."
    )
    assert hypotheses[0]["forbidden_behavior"] == [
        "Treat __iter__ alone as sufficient.",
    ]
    assert hypotheses[0]["decision_contract"] == {
        "wrong_decision": "Do not substitute a returned iterator for direct __next__.",
        "causal_distinction": ("A directly requested stateful protocol must implement and probe its direct operation."),
        "required_action": "Implement and probe stateful direct __next__ semantics.",
        "acceptance_observable": "The direct iterator protocol remains incomplete.",
        "scope_boundary": ["Treat __iter__ alone as sufficient."],
        "activation_phase": "task_start",
    }
    assert hypotheses[0]["public_trigger"] == [
        {
            "case_id": "case_pydicom",
            "task": "Support next(person_name).",
        }
    ]
    assert hypotheses[0]["lever_policy"]["recommended_lever"] == "instruction"
    assert hypotheses[0]["lever_policy"]["predicted_affected_case_ids"] == ["case_pydicom"]
    assert "action" in hypotheses[0]["lever_policy"]["why_not_other_levers"]

    tampered = yaml.safe_load(Path(hypothesis_path).read_text(encoding="utf-8"))
    tampered["hypotheses"][0]["required_behavior"] = "Only implement __iter__."
    Path(hypothesis_path).write_text(
        yaml.safe_dump(tampered, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="content digest mismatch"):
        load_optimization_hypotheses(hypothesis_path)


def test_planner_binding_restores_analyzer_semantics_after_model_drift() -> None:
    plan_data = {
        "actions": [
            {
                "action_id": "a1",
                "attributed_issue_ids": ["issue_protocol"],
                "expected_effect": "Only __iter__ is required.",
                "constraints": {},
            }
        ],
    }
    hypotheses = [
        {
            "hypothesis_id": "hyp_protocol",
            "content_sha256": "abc123",
            "source_issue_id": "issue_protocol",
            "required_behavior": "Implement stateful direct __next__ semantics.",
            "forbidden_behavior": ["Treat __iter__ alone as sufficient."],
            "public_trigger": [],
            "decisive_probe": {"recommendation": "Call next(obj) twice."},
        }
    ]

    _bind_immutable_hypotheses(plan_data, hypotheses)

    action = plan_data["actions"][0]
    assert action["expected_effect"] == "Implement stateful direct __next__ semantics."
    assert action["constraints"]["optimization_contracts"] == hypotheses
    assert plan_data["metadata"]["semantic_authority"] == ("immutable_optimization_hypotheses")


def test_planner_binding_records_lever_decision_without_exposing_it_as_skill() -> None:
    plan_data = {
        "actions": [
            {
                "action_id": "a1",
                "action_group": "skill",
                "target_path": "skills/protocol/SKILL.md",
                "attributed_issue_ids": ["issue_protocol"],
                "constraints": {},
            }
        ],
    }
    hypotheses = [
        {
            "hypothesis_id": "hyp_protocol",
            "content_sha256": "abc123",
            "source_issue_id": "issue_protocol",
            "required_behavior": "Implement the requested direct protocol.",
            "forbidden_behavior": [],
            "public_trigger": [],
            "decisive_probe": {},
            "lever_policy": {
                "recommended_lever": "instruction",
                "why_this_lever": "The defect is a reusable method.",
                "why_not_other_levers": {"action": "No capability is missing."},
                "predicted_affected_case_ids": ["case_target"],
                "retroactive_check": {"falsification_rule": "Target must improve."},
            },
        }
    ]

    _bind_immutable_hypotheses(plan_data, hypotheses)

    decision = plan_data["actions"][0]["constraints"]["lever_decision"]
    assert decision["selected_lever"] == "instruction"
    assert decision["selected_surface"] == "skill"
    assert decision["predicted_affected_case_ids"] == ["case_target"]


def test_planner_binding_rejects_cross_lever_compensation() -> None:
    plan_data = {
        "actions": [
            {
                "action_id": "a1",
                "action_group": "skill",
                "target_path": "skills/tool_substitute/SKILL.md",
                "attributed_issue_ids": ["issue_tool"],
                "constraints": {},
            }
        ],
    }
    hypotheses = [
        {
            "hypothesis_id": "hyp_tool",
            "content_sha256": "abc123",
            "source_issue_id": "issue_tool",
            "required_behavior": "Provide a deterministic parser.",
            "forbidden_behavior": [],
            "public_trigger": [],
            "decisive_probe": {},
            "lever_policy": {
                "recommended_lever": "action",
                "why_this_lever": "Executable capability is missing.",
                "why_not_other_levers": {},
                "predicted_affected_case_ids": ["case_target"],
                "retroactive_check": {},
            },
        }
    ]

    with pytest.raises(RuntimeError, match="crosses the diagnosed optimization lever"):
        _bind_immutable_hypotheses(plan_data, hypotheses)


def test_generated_skill_contract_accepts_native_flexible_skill_body() -> None:
    content = """\
## Why
The requested protocol surface, not a nearby convenience protocol, defines success.
## Method
Enumerate requested operations, run a positive and boundary probe, then implement.
## Done
Retain the command, exit status, and checked assertions.
"""

    _validate_generated_skill_contract(content)


def test_generated_skill_contract_defers_decision_quality_to_evaluation() -> None:
    content = """\
## Method
Inspect the protocol and implement a nearby convenience operation.
"""
    _validate_generated_skill_contract(content)


def test_generated_skill_contract_accepts_lossless_free_form_handoff() -> None:
    content = """\
## Why
An external iterable is not the object's **direct-next lifecycle**.

## Method
Implement stateful direct `next` on the object.

Stop when repeated next calls terminate at exhaustion.
Returning a separate iterator is not an equivalent substitute.
"""
    _validate_generated_skill_contract(content)


def test_generated_skill_contract_does_not_gate_description_wording() -> None:
    content = """---
name: protocol_check
description: Create a skill that verifies the protocol.
---

## Method
Run the direct operation.
"""

    _validate_generated_skill_contract(content)


def test_skill_trigger_description_uses_public_runtime_trigger() -> None:
    action = MemberOptimizationAction(
        action_id="act_skill",
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/protocol_check/SKILL.md",
        description="Create a skill that verifies the protocol.",
        constraints={
            "optimization_contracts": [
                {
                    "public_trigger": ["direct next behavior is requested"],
                }
            ],
        },
    )

    assert _skill_trigger_description(action) == ("Use when direct next behavior is requested.")


def test_skill_trigger_description_prefers_causal_trigger_over_task_symptom() -> None:
    action = MemberOptimizationAction(
        action_id="act_skill",
        role="solver",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/output_channel/SKILL.md",
        constraints={
            "optimization_contracts": [
                {
                    "public_trigger": [
                        {
                            "task": "Suppress a future warning emitted by dynamic lookup.",
                        }
                    ],
                    "decision_contract": {
                        "causal_distinction": (
                            "Stream output must be redirected to logging rather than treated as a Warning object."
                        ),
                    },
                }
            ],
        },
    )

    assert _skill_trigger_description(action) == (
        "Use when a task requires deciding whether Stream output must be "
        "redirected to logging rather than treated as a Warning object."
    )


def test_post_diagnosis_contract_is_deferred_to_runtime_control() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["issue_1"],
        optimization_surfaces=["skill"],
    )
    mechanism = RoleMechanismAttribution(
        issue_id="issue_1",
        role="solver",
        mechanism_type="reasoning_policy",
        failure_signature="stops after diagnosis",
        confidence=0.9,
        optimization_surface="skill",
    )

    targets, report, adaptations = _adapt_surface_for_activation_phase(
        targets=[target],
        mechanism_report=MechanismAttributionReport(
            role_mechanisms={"solver": [mechanism]},
        ),
        optimization_hypotheses=[
            {
                "source_issue_id": "issue_1",
                "decision_contract": {"activation_phase": "post_diagnosis"},
            }
        ],
    )

    assert targets[0].optimization_surfaces == ["control"]
    assert report.role_mechanisms["solver"][0].optimization_surface == ("control")
    assert adaptations[0]["reason"] == (
        "required_action_is_not_knowable_at_task_start_and_must_not_be_recast_as_static_instruction"
    )


def test_post_diagnosis_prompt_contract_is_deferred_to_runtime_control() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["issue_1"],
        optimization_surfaces=["prompt_section"],
    )
    mechanism = RoleMechanismAttribution(
        issue_id="issue_1",
        role="solver",
        mechanism_type="instruction",
        failure_signature="diagnosed_but_did_not_edit",
        confidence=0.9,
        optimization_surface="prompt_section",
    )

    targets, report, _ = _adapt_surface_for_activation_phase(
        targets=[target],
        mechanism_report=MechanismAttributionReport(
            role_mechanisms={"solver": [mechanism]},
        ),
        optimization_hypotheses=[
            {
                "source_issue_id": "issue_1",
                "decision_contract": {"activation_phase": "post_diagnosis"},
            }
        ],
    )

    assert targets[0].optimization_surfaces == ["control"]
    assert report.role_mechanisms["solver"][0].optimization_surface == "control"


def test_investigation_prompt_contract_routes_as_skill() -> None:
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["issue_1"],
        optimization_surfaces=["prompt_section"],
    )
    mechanism = RoleMechanismAttribution(
        issue_id="issue_1",
        role="solver",
        mechanism_type="instruction",
        failure_signature="wrong_output_channel_hypothesis",
        confidence=0.9,
        optimization_surface="prompt_section",
    )

    targets, report, adaptations = _adapt_surface_for_activation_phase(
        targets=[target],
        mechanism_report=MechanismAttributionReport(
            role_mechanisms={"solver": [mechanism]},
        ),
        optimization_hypotheses=[
            {
                "source_issue_id": "issue_1",
                "decision_contract": {"activation_phase": "during_investigation"},
            }
        ],
    )

    assert targets[0].optimization_surfaces == ["skill"]
    assert report.role_mechanisms["solver"][0].optimization_surface == "skill"
    assert adaptations[0]["reason"] == (
        "investigation_method_requires_task_relevant_routing_instead_of_global_static_prompt_injection"
    )


def test_generated_skill_contract_accepts_bold_capsule_labels() -> None:
    content = """## Decision Capsule
- **Invariant:** direct operation semantics must hold
- **Discriminator:** test the object itself, not a convenience wrapper
- **Positive case:** the direct operation succeeds
- **Boundary case:** the terminal state is explicit
- **Acceptance probe:** execute the direct operation
- **Invalid substitute:** a nearby convenience behavior is insufficient
- **Action trigger:** once the probe selects the contract, implement the edit before more exploration

## Method
Run the probe before and after the patch.
"""

    _validate_generated_skill_contract(content)


def test_generated_skill_contract_accepts_native_body_without_optimizer_sections() -> None:
    _validate_generated_skill_contract("## Method\nRun a useful probe.\n")


def test_generated_skill_contract_defers_probe_semantics_to_evaluation() -> None:
    content = """---
name: membership_probe
description: Use when membership behavior must be verified directly.
---

```python
assert 'S' in value is True
```
"""

    _validate_generated_skill_contract(content)


@pytest.mark.parametrize(
    "assertion",
    ["assert 'S' in value", "assert ('S' in value) is True"],
)
def test_generated_skill_contract_accepts_unambiguous_boolean_assertions(
    assertion: str,
) -> None:
    content = f"""---
name: membership_probe
description: Use when membership behavior must be verified directly.
---

```python
{assertion}
```
"""

    _validate_generated_skill_contract(content)


def test_generated_skill_contract_leaves_semantic_judgment_to_candidate_evaluation() -> None:
    content = """## Decision Capsule
- Invariant: direct __next__ state semantics must hold
- Discriminator: direct __next__ distinguishes an iterator from an iterable helper
- Positive case: direct __next__ returns the next value after initialization
- Boundary case: direct __next__ preserves the specified pre-init and terminal behavior
- Acceptance probe: call __iter__, repeated __next__, and the boundary cases
- Invalid substitute: returning iter(str(obj)) is not equivalent
- Action trigger: once the lifecycle probe selects the contract, implement __next__ before more exploration

## Alternatives
For an ordinary iterable, __next__ may be absent.
"""

    _validate_generated_skill_contract(content)


def test_generated_skill_contract_does_not_require_optimizer_action_labels() -> None:
    content = """## Decision Capsule
- Invariant: direct behavior must hold
- Discriminator: the direct operation selects the contract
- Positive case: the operation succeeds
- Boundary case: the terminal state is explicit
- Acceptance probe: execute the operation
- Invalid substitute: a wrapper is insufficient
- Action trigger: once the probe passes, continue investigating the repository
"""

    _validate_generated_skill_contract(content)


def test_generated_skill_contract_rejects_empty_runtime_body() -> None:
    content = """\
---
name: empty_skill
description: A structurally valid but empty skill.
---
"""

    with pytest.raises(ValueError, match="must contain runtime instructions"):
        _validate_generated_skill_contract(content)


def test_action_execution_prompt_requires_safe_mountable_tool_contract() -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    action = MemberOptimizationAction(
        action_id="act_tool_add",
        role="explainer",
        action_group="tool",
        operation="add",
        action_type="tool_creation",
        target_path="tools/risk_checker.py",
        declared_write_paths=[
            "tools/risk_checker.py",
            "tools/tools.yaml",
        ],
        constraints={"class_name": "RiskChecker"},
    )
    message = MemberActionExecutorAgent("unused")._build_user_message(
        action,
        plan_summary="Create a deterministic risk checker tool.",
        allowed_skills=[],
        allowed_tools=["read_file", "write_file", "edit_file"],
    )

    assert "inherits from `openjiuwen.core.foundation.tool.Tool`" in message
    assert "`tools/tools.yaml` with a `tools` entry containing `file` and `class_name`" in message
    assert "ToolCard must define `input_params` as a JSON Schema" in message
    assert "do not import httpx, os, pathlib" in message
    assert "do not call __import__, compile, eval, exec, input, or open" in message
    assert "operate on explicit input payloads supplied by the agent" in message
    assert "progressive tool search" in message
    assert "called by the role" in message
    assert "machine-checkable result" in message
    assert "weaker string heuristic" in message


def _tool_source_with_import_call() -> str:
    return "\n".join(
        [
            "from openjiuwen.core.foundation.tool import Tool, ToolCard",
            "",
            "",
            "class RiskChecker(Tool):",
            "    def __init__(self):",
            "        super().__init__(",
            "            ToolCard(",
            "                id='risk_checker',",
            "                name='risk_checker',",
            "                description='Check supplied artifact text for required evidence.',",
            "                input_params={",
            "                    'type': 'object',",
            "                    'properties': {'artifact_text': {'type': 'string'}},",
            "                    'required': ['artifact_text'],",
            "                },",
            "            )",
            "        )",
            "",
            "    async def invoke(self, inputs, **kwargs):",
            "        module = __import__('json')",
            "        return {'status': 'passed', 'module': module.__name__}",
            "",
            "    async def stream(self, inputs, **kwargs):",
            "        yield await self.invoke(inputs, **kwargs)",
        ]
    )


def _safe_payload_tool_source() -> str:
    return "\n".join(
        [
            "from openjiuwen.core.foundation.tool import Tool, ToolCard",
            "",
            "",
            "class RiskChecker(Tool):",
            "    def __init__(self):",
            "        super().__init__(",
            "            ToolCard(",
            "                id='risk_checker',",
            "                name='risk_checker',",
            "                description='Check supplied artifact text for required evidence.',",
            "                input_params={",
            "                    'type': 'object',",
            "                    'properties': {'artifact_text': {'type': 'string'}},",
            "                    'required': ['artifact_text'],",
            "                },",
            "            )",
            "        )",
            "",
            "    async def invoke(self, inputs, **kwargs):",
            "        artifact_text = str(inputs.get('artifact_text', '')) if isinstance(inputs, dict) else ''",
            "        return {'status': 'passed' if artifact_text else 'failed'}",
            "",
            "    async def stream(self, inputs, **kwargs):",
            "        yield await self.invoke(inputs, **kwargs)",
        ]
    )


def test_action_executor_retries_tool_generation_after_safety_validation_error(
    tmp_path: Path,
) -> None:
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    class _RetryingToolExecutionAgent(MemberActionExecutorAgent):
        def __init__(self) -> None:
            super().__init__("unused")
            self.messages: list[str] = []

        async def _invoke_direct_tool_action(self, message: str) -> str:
            self.messages.append(message)
            if len(self.messages) == 1:
                return json.dumps(
                    {
                        "action_id": "act_tool_add",
                        "status": "succeeded",
                        "file_writes": [
                            {
                                "path": "tools/risk_checker.py",
                                "content": _tool_source_with_import_call(),
                            },
                            {
                                "path": "tools/tools.yaml",
                                "content": yaml.safe_dump(
                                    {
                                        "tools": [
                                            {
                                                "file": "tools/risk_checker.py",
                                                "class_name": "RiskChecker",
                                            }
                                        ]
                                    }
                                ),
                            },
                        ],
                        "errors": [],
                    }
                )
            assert "dangerous call '__import__'" in message
            return json.dumps(
                {
                    "action_id": "act_tool_add",
                    "status": "succeeded",
                    "file_writes": [
                        {
                            "path": "tools/risk_checker.py",
                            "content": _safe_payload_tool_source(),
                        },
                        {
                            "path": "tools/tools.yaml",
                            "content": yaml.safe_dump(
                                {
                                    "tools": [
                                        {
                                            "file": "tools/risk_checker.py",
                                            "class_name": "RiskChecker",
                                        }
                                    ]
                                }
                            ),
                        },
                    ],
                    "errors": [],
                }
            )

    action_worktree = tmp_path / "action_wt"
    action_worktree.mkdir()
    action = MemberOptimizationAction(
        action_id="act_tool_add",
        role="explainer",
        action_group="tool",
        operation="add",
        action_type="tool_creation",
        target_path="tools/risk_checker.py",
        declared_write_paths=[
            "tools/risk_checker.py",
            "tools/tools.yaml",
        ],
        constraints={"class_name": "RiskChecker"},
    )
    agent = _RetryingToolExecutionAgent()

    result = asyncio.run(
        agent.execute_action(
            action_worktree=action_worktree,
            action=action,
            plan_summary="Create a deterministic risk checker tool.",
            allowed_skills=[],
            allowed_tools=[],
        )
    )

    assert result["status"] == "succeeded"
    assert len(agent.messages) == 2
    final_tool = (action_worktree / "tools" / "risk_checker.py").read_text(encoding="utf-8")
    assert "__import__" not in final_tool


def test_package_python_safety_allows_non_builtin_compile_method() -> None:
    source = "import re\npattern = re.compile(r'^[a-z]+$')\n"

    assert _validate_package_python_source(source, path="tools/checker.py") == []


@pytest.mark.parametrize("owner", ["builtins", "__builtins__"])
def test_package_python_safety_rejects_explicit_builtin_compile(owner: str) -> None:
    source = f"import builtins\nresult = {owner}.compile('1 + 1', '<test>', 'eval')\n"

    errors = _validate_package_python_source(source, path="tools/checker.py")

    assert errors == ["dangerous call 'compile' in tools/checker.py"]


def test_member_executor_preseeds_tool_add_with_valid_object_schema(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    """tool/add should start from a valid ToolCard schema, not rely on repair later."""
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="act_tool_add",
        role="explainer",
        action_group="tool",
        operation="add",
        action_type="tool_creation",
        target_path="tools/risk_checker.py",
        description="Create a deterministic risk checker tool.",
        rationale="The evaluator found missing deterministic risk checks.",
        declared_write_paths=[
            "tools/risk_checker.py",
            "tools/tools.yaml",
        ],
        constraints={"class_name": "RiskChecker"},
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_tool_add",
        targets=[target],
        actions=[action],
        action_waves=[["act_tool_add"]],
    )
    inspecting_agent = _InspectingToolScaffoldExecutorAgent()
    executor = MemberActionExecutor(executor_agent=inspecting_agent)

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "succeeded"
    assert inspecting_agent.saw_tool_scaffold is True
    integration = integration_worktree_path(run_dir / "wt", "explainer")
    tool_text = (integration / "tools" / "risk_checker.py").read_text(encoding="utf-8")
    manifest = yaml.safe_load((integration / "tools" / "tools.yaml").read_text(encoding="utf-8"))
    assert "'type': 'object'" in tool_text
    assert {"file": "tools/risk_checker.py", "class_name": "RiskChecker"} in manifest["tools"]


def test_member_executor_preseeds_missing_tool_modify_with_valid_object_schema(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    """tool/modify on a missing package-local tool is add-like in scenario action groups."""
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="act_tool_modify_missing",
        role="explainer",
        action_group="tool",
        operation="modify",
        action_type="tool_add",
        target_path="tools/dom_runtime_validator.py",
        description="Create a deterministic DOM runtime validator tool.",
        rationale="The evaluator found missing deterministic DOM checks.",
        declared_write_paths=[
            "tools/dom_runtime_validator.py",
            "tools/tools.yaml",
        ],
        constraints={"class_name": "DomRuntimeValidator"},
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_tool_modify_missing",
        targets=[target],
        actions=[action],
        action_waves=[["act_tool_modify_missing"]],
    )
    inspecting_agent = _InspectingToolScaffoldExecutorAgent()
    executor = MemberActionExecutor(executor_agent=inspecting_agent)

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "succeeded"
    assert inspecting_agent.saw_tool_scaffold is True
    integration = integration_worktree_path(run_dir / "wt", "explainer")
    tool_text = (integration / "tools" / "dom_runtime_validator.py").read_text(encoding="utf-8")
    manifest = yaml.safe_load((integration / "tools" / "tools.yaml").read_text(encoding="utf-8"))
    assert "'type': 'object'" in tool_text
    assert {"file": "tools/dom_runtime_validator.py", "class_name": "DomRuntimeValidator"} in manifest["tools"]


def test_member_executor_rejects_dangerous_tool_before_merge(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="act_tool_add",
        role="explainer",
        action_group="tool",
        operation="add",
        action_type="tool_creation",
        target_path="tools/risk_checker.py",
        description="Create a deterministic risk checker tool.",
        rationale="The evaluator found missing deterministic risk checks.",
        declared_write_paths=[
            "tools/risk_checker.py",
            "tools/tools.yaml",
        ],
        constraints={"class_name": "RiskChecker"},
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_tool_add",
        targets=[target],
        actions=[action],
        action_waves=[["act_tool_add"]],
    )
    executor = MemberActionExecutor(executor_agent=_DangerousToolWritingExecutorAgent())

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "failed"
    assert "dangerous import 'os'" in results[0].error
    integration = integration_worktree_path(run_dir / "wt", "explainer")
    assert not (integration / "tools" / "risk_checker.py").exists()


def test_member_executor_generated_safe_tool_is_mounted_and_callable(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    from openjiuwen.harness.resources import (
        find_plugin_manifest,
        load_plugin_package,
        resolve_plugin_parts,
    )
    from openjiuwen.harness.schema.build_context import BuildContext

    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    action = MemberOptimizationAction(
        action_id="act_tool_add",
        role="explainer",
        action_group="tool",
        operation="add",
        action_type="tool_creation",
        target_path="tools/risk_checker.py",
        description="Create a deterministic risk checker tool.",
        rationale="The evaluator found missing deterministic risk checks.",
        declared_write_paths=[
            "tools/risk_checker.py",
            "tools/tools.yaml",
        ],
        constraints={"class_name": "RiskChecker"},
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_tool_add",
        targets=[target],
        actions=[action],
        action_waves=[["act_tool_add"]],
    )
    executor = MemberActionExecutor(executor_agent=_SafeContentToolWritingExecutorAgent())

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )
    integration = integration_worktree_path(run_dir / "wt", "explainer")
    verification = asyncio.run(
        HarnessChangeVerifier().verify(
            plan=plan,
            worktrees_dir=run_dir / "wt",
            output_path=str(run_dir / "verification.json"),
        )
    )
    spec = load_plugin_package(find_plugin_manifest(integration))
    context = BuildContext(language="en")
    resolved = resolve_plugin_parts(spec, context)
    tool = next(item for item in resolved.tools if item.card.name == "risk_checker")
    tool_result = asyncio.run(
        tool.invoke(
            {
                "artifact_text": "risk: stable runtime evidence",
                "required_phrase": "stable runtime",
            }
        )
    )

    assert results[0].status == "succeeded"
    assert verification.status == "passed"
    assert tool_result == {"status": "passed", "found": True}


def test_member_executor_fails_out_of_bounds_write(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    executor = MemberActionExecutor(executor_agent=_OutOfBoundsExecutorAgent())

    import asyncio

    results = asyncio.run(
        executor.execute(
            plan=_single_action_plan(two_role_harness_dir),
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "failed"
    assert "outside declared_write_paths" in results[0].error


def test_member_executor_rejects_illegal_action_before_execution(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    executor = MemberActionExecutor(executor_agent=_WritingExecutorAgent())

    import asyncio

    results = asyncio.run(
        executor.execute(
            plan=_illegal_action_plan(two_role_harness_dir),
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "failed"
    assert "unsupported operation 'install'" in results[0].error
    assert not (role_worktree_path(run_dir / "wt", "explainer") / "waves").exists()


def test_member_executor_executes_package_local_rail_action(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    """A valid package-local Rail is generated, normalized, and merged."""
    run_dir = tmp_path / "member_optimization_001"
    action = MemberOptimizationAction(
        action_id="rail_explainer_001",
        role="explainer",
        action_group="rail",
        operation="add",
        action_type="rail_guard",
        target_path="rails/conflict_marker_guard.py",
        declared_write_paths=["rails/conflict_marker_guard.py", "rails/rails.yaml"],
        description="Add conflict marker guard.",
        constraints={"class_name": "ConflictMarkerGuardRail"},
    )
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_rail",
        targets=[target],
        actions=[action],
        action_waves=[[action.action_id]],
    )
    executor = MemberActionExecutor(executor_agent=_RailShortManifestExecutorAgent())

    import asyncio

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "succeeded"
    assert "rails/conflict_marker_guard.py" in results[0].changed_files
    manifest = Path(results[0].worktree_path) / "rails" / "rails.yaml"
    assert yaml.safe_load(manifest.read_text(encoding="utf-8"))["rails"][0]["file"] == (
        "rails/conflict_marker_guard.py"
    )


def test_member_executor_validates_generated_rail_source(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    """A syntactically broken Rail reaches and fails resource validation."""
    run_dir = tmp_path / "member_optimization_001"
    action = MemberOptimizationAction(
        action_id="rail_broken_001",
        role="explainer",
        action_group="rail",
        operation="add",
        action_type="rail_guard",
        target_path="rails/broken.py",
        declared_write_paths=["rails/broken.py", "rails/rails.yaml"],
        description="Add a broken rail.",
        constraints={"class_name": "BrokenRail"},
    )
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_broken_rail",
        targets=[target],
        actions=[action],
        action_waves=[[action.action_id]],
    )
    executor = MemberActionExecutor(executor_agent=_BrokenRailExecutorAgent())

    import asyncio

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    assert results[0].status == "failed"
    assert "python_compile:rails/broken.py" in results[0].error
    assert results[0].merge_status == "pending"
    assert results[0].worktree_path
    assert not (run_dir / "merges" / "explainer" / "wave_000_subwave_000.json").exists()


def test_member_repair_prompt_includes_failed_file_contents(tmp_path: Path) -> None:
    """Repair agent receives bounded file content for directly failed files."""
    from openjiuwen.rsi.member_optimizer.verification import (
        HarnessRepairAgent,
    )

    integration = tmp_path / "integration"
    (integration / "rails").mkdir(parents=True)
    (integration / "rails" / "broken.py").write_text(
        'class Broken:\n    def description(self):\n        return "unterminated\n',
        encoding="utf-8",
    )
    agent = HarnessRepairAgent(model_config_ref="unused")

    message = agent._build_repair_user_message(
        role="solver",
        role_integration_worktree=integration,
        failed_checks=[
            {
                "name": "python_compile:solver/rails/broken.py",
                "status": "failed",
                "error": "unterminated string literal (detected at line 3)",
            }
        ],
    )

    assert "## Failed File Contents" in message
    assert "### rails/broken.py" in message
    assert '0003:         return "unterminated' in message


def test_member_executor_agent_includes_declared_file_contents_in_prompt(tmp_path: Path) -> None:
    """Executor must give the model current file contents before asking for replacement content."""
    from openjiuwen.rsi.member_optimizer.action_executor import (
        MemberActionExecutorAgent,
    )

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "harness_config.yaml").write_text("command_timeout_sec: 300\n", encoding="utf-8")
    action = MemberOptimizationAction(
        action_id="act_config",
        role="solver",
        action_group="config",
        operation="modify",
        action_type="config_improvement",
        target_path="harness_config.yaml",
        declared_write_paths=["harness_config.yaml"],
    )

    message = MemberActionExecutorAgent("unused")._build_user_message(
        action,
        plan_summary="plan",
        allowed_skills=[],
        allowed_tools=["read_file", "write_file", "edit_file"],
        action_worktree=worktree,
    )

    assert "## Current Declared File Contents" in message
    assert "### harness_config.yaml" in message
    assert "command_timeout_sec: 300" in message


class _MixedSubwaveExecutorAgent:
    async def execute_action(self, *, action_worktree, action, plan_summary, allowed_skills, allowed_tools):  # type: ignore[no-untyped-def]
        declared_paths = action.declared_write_paths or [action.target_path]
        if action.action_id == "act_config":
            return {
                "status": "failed",
                "declared_write_paths": declared_paths,
                "error": "config field not confirmed",
            }
        target = Path(action_worktree) / action.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# bash-only solver\n", encoding="utf-8")
        return {
            "status": "succeeded",
            "declared_write_paths": declared_paths,
            "error": "",
        }


def test_member_executor_merges_successful_non_overlapping_action_when_peer_fails(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    """A failed config action must not discard a successful prompt action touching another file."""
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_prompt", "issue_config"],
        confidence=0.9,
    )
    prompt_action = MemberOptimizationAction(
        action_id="act_prompt",
        role="explainer",
        action_group="prompt",
        operation="modify",
        action_type="prompt_refinement",
        target_path="identity.md",
        declared_write_paths=["identity.md"],
    )
    config_action = MemberOptimizationAction(
        action_id="act_config",
        role="explainer",
        action_group="config",
        operation="modify",
        action_type="config_improvement",
        target_path="harness_config.yaml",
        declared_write_paths=["harness_config.yaml"],
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_mixed",
        targets=[target],
        actions=[prompt_action, config_action],
        action_waves=[["act_prompt", "act_config"]],
    )
    executor = MemberActionExecutor(executor_agent=_MixedSubwaveExecutorAgent())

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    by_id = {result.action_id: result for result in results}
    assert by_id["act_config"].status == "failed"
    assert by_id["act_prompt"].status == "succeeded"
    assert by_id["act_prompt"].merge_status == "merged"
    assert (integration_worktree_path(run_dir / "wt", "explainer") / "identity.md").read_text(
        encoding="utf-8"
    ) == "# bash-only solver\n"


def test_member_executor_runs_fallback_skill_add_after_skill_search_failure(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    """A failed skill/search should not block a dependency_failed skill/add fallback."""
    run_dir = tmp_path / "member_optimization_001"
    target = MemberOptimizationTarget(
        role="explainer",
        harness_ref_path=str(two_role_harness_dir / "explainer"),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
    )
    search_action = MemberOptimizationAction(
        action_id="act_skill_search",
        role="explainer",
        action_group="skill",
        operation="search",
        action_type="skill_search",
        target_path="skills/",
        candidate_query="preserve analyze fix",
        declared_write_paths=["skills", "skills/skills.yaml"],
    )
    add_action = MemberOptimizationAction(
        action_id="act_skill_add",
        role="explainer",
        action_group="skill",
        operation="add",
        action_type="skill_creation",
        target_path="skills/preserve_analyze_fix/SKILL.md",
        depends_on=["act_skill_search"],
        run_if="dependency_failed",
        declared_write_paths=[
            "skills/preserve_analyze_fix/SKILL.md",
            "skills/skills.yaml",
        ],
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_skill_fallback",
        targets=[target],
        actions=[search_action, add_action],
        # Persisted/model-authored wave metadata may be stale. The executor must
        # still honor depends_on and run the fallback only after search fails.
        action_waves=[["act_skill_search", "act_skill_add"]],
    )
    executor = MemberActionExecutor(
        executor_agent=_SkillWritingExecutorAgent(),
        skill_acquisition=_FailingSkillAcquisition(),
    )

    results = asyncio.run(
        executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            model_config_ref="unused-by-fake",
        )
    )

    by_id = {result.action_id: result for result in results}
    assert by_id["act_skill_search"].status == "failed"
    assert by_id["act_skill_add"].status == "succeeded"
    assert by_id["act_skill_add"].merge_status == "merged"
    assert (
        integration_worktree_path(run_dir / "wt", "explainer") / "skills" / "preserve_analyze_fix" / "SKILL.md"
    ).is_file()
    skill_text = (
        integration_worktree_path(run_dir / "wt", "explainer") / "skills" / "preserve_analyze_fix" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "name: preserve_analyze_fix" in skill_text


def test_member_verifier_fails_role_when_planned_action_failed(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text(yaml.safe_dump({"role": "explainer"}), encoding="utf-8")
    plan = _single_action_plan(two_role_harness_dir)
    (run_dir / "plan.yaml").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.yaml").write_text(
        yaml.safe_dump({"actions": [{"action_id": "action_001"}], "action_waves": [["action_001"]]}),
        encoding="utf-8",
    )
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": "action_001",
                        "role": "explainer",
                        "status": "failed",
                        "merge_status": "",
                        "error": "no declared change",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    verifier = HarnessChangeVerifier()

    import asyncio

    result = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )

    assert result.status == "failed"
    assert result.role_results["explainer"].status == "failed"
    assert any(
        check.name == "action_result:action_001" and check.status == "failed"
        for check in result.role_results["explainer"].checks
    )


def test_member_verifier_fails_illegal_action_even_if_execution_succeeded(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text(yaml.safe_dump({"role": "explainer"}), encoding="utf-8")
    (integration / "identity.md").write_text("# explainer\n", encoding="utf-8")
    plan = _illegal_action_plan(two_role_harness_dir)
    (run_dir / "plan.yaml").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.yaml").write_text(
        yaml.safe_dump({"actions": [{"action_id": "action_illegal"}], "action_waves": [["action_illegal"]]}),
        encoding="utf-8",
    )
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": "action_illegal",
                        "role": "explainer",
                        "status": "succeeded",
                        "merge_status": "merged",
                        "declared_write_paths": ["skills/"],
                        "error": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    verifier = HarnessChangeVerifier()

    import asyncio

    result = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )

    assert result.status == "failed"
    assert any(
        check.name == "action_policy:action_illegal" and check.status == "failed"
        for check in result.role_results["explainer"].checks
    )


def test_member_verifier_passes_when_integration_is_loadable_expert_harness(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text(yaml.safe_dump({"role": "explainer"}), encoding="utf-8")
    (integration / "identity.md").write_text("# changed\n", encoding="utf-8")
    plan = _single_action_plan(two_role_harness_dir)
    (run_dir / "plan.yaml").write_text(
        yaml.safe_dump({"actions": [{"action_id": "action_001"}], "action_waves": [["action_001"]]}),
        encoding="utf-8",
    )
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": "action_001",
                        "role": "explainer",
                        "status": "succeeded",
                        "merge_status": "merged",
                        "declared_write_paths": ["identity.md"],
                        "error": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    verifier = HarnessChangeVerifier()

    import asyncio

    result = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )

    assert result.status == "passed"
    assert any(
        check.name == "expert_harness_load:explainer" and check.status == "passed"
        for check in result.role_results["explainer"].checks
    )


def test_member_verifier_resolves_package_relative_tool(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "architect" / "integration"
    integration.mkdir(parents=True)
    action = MemberOptimizationAction(
        action_id="tool_architect_001",
        role="architect",
        action_group="tool",
        operation="modify",
        action_type="tool_modify",
        target_path="tools/architecture_decision_tool.py",
        declared_write_paths=["tools/architecture_decision_tool.py", "tools/tools.yaml"],
        description="Add architecture decision tool.",
    )
    shutil.copytree(two_role_harness_dir / "explainer", integration, dirs_exist_ok=True)
    (integration / "tools").mkdir(exist_ok=True)
    (integration / "tools" / "architecture_decision_tool.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from typing import Any, AsyncIterator",
                "",
                "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                "",
                "",
                "class ArchitectureDecisionTool(Tool):",
                "    def __init__(self) -> None:",
                "        super().__init__(",
                "            ToolCard(",
                "                id='architecture_decision_tool',",
                "                name='architecture_decision_tool',",
                "                description='Add architecture decision tool.',",
                "                input_params={",
                "                    'type': 'object',",
                "                    'properties': {",
                "                        'decision_context': {",
                "                            'type': 'string',",
                "                            'description': 'Architecture decision context to inspect.',",
                "                        }",
                "                    },",
                "                    'required': ['decision_context'],",
                "                },",
                "            )",
                "        )",
                "",
                "    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> dict[str, Any]:",
                "        return {'status': 'ok'}",
                "",
                "    async def stream(",
                "        self,",
                "        inputs: dict[str, Any],",
                "        **kwargs: Any,",
                "    ) -> AsyncIterator[dict[str, Any]]:",
                "        yield await self.invoke(inputs, **kwargs)",
            ]
        ),
        encoding="utf-8",
    )
    (integration / "tools" / "tools.yaml").write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "file": "tools/architecture_decision_tool.py",
                        "class_name": "ArchitectureDecisionTool",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_tool",
        targets=[
            MemberOptimizationTarget(
                role="architect",
                member_name="architect",
                harness_ref_path=str(integration),
                confidence=0.9,
            )
        ],
        actions=[action],
        action_waves=[[action.action_id]],
    )
    (run_dir / "plan.yaml").write_text(
        yaml.safe_dump({"actions": [{"action_id": action.action_id}], "action_waves": [[action.action_id]]}),
        encoding="utf-8",
    )
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": action.action_id,
                        "role": "architect",
                        "status": "succeeded",
                        "merge_status": "merged",
                        "declared_write_paths": action.declared_write_paths,
                        "error": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    verifier = HarnessChangeVerifier()

    import asyncio

    result = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )

    assert result.status == "passed"
    checks = result.role_results["architect"].checks
    assert any(check.name == "expert_harness_resolve:architect" and check.status == "passed" for check in checks)
    assert any("tool_file_ref:architect:" in check.name and check.status == "passed" for check in checks)


def test_member_verifier_rejects_package_tool_without_object_input_schema(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    """Package-local tools must be safe to register as OpenAI function schemas."""
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "architect" / "integration"
    shutil.copytree(two_role_harness_dir / "explainer", integration, dirs_exist_ok=True)
    (integration / "tools").mkdir(exist_ok=True)
    (integration / "tools" / "risk_checker.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from typing import Any, AsyncIterator",
                "",
                "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                "",
                "",
                "class RiskChecker(Tool):",
                "    def __init__(self) -> None:",
                "        super().__init__(",
                "            ToolCard(",
                "                id='risk_checker',",
                "                name='risk_checker',",
                "                description='Check delivery risk.',",
                "                input_params={'risk_text': {'type': 'string'}},",
                "            )",
                "        )",
                "",
                "    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> dict[str, Any]:",
                "        return {'status': 'ok'}",
                "",
                "    async def stream(",
                "        self,",
                "        inputs: dict[str, Any],",
                "        **kwargs: Any,",
                "    ) -> AsyncIterator[dict[str, Any]]:",
                "        yield await self.invoke(inputs, **kwargs)",
            ]
        ),
        encoding="utf-8",
    )
    (integration / "tools" / "tools.yaml").write_text(
        yaml.safe_dump({"tools": [{"file": "tools/risk_checker.py", "class_name": "RiskChecker"}]}),
        encoding="utf-8",
    )
    action = MemberOptimizationAction(
        action_id="tool_architect_001",
        role="architect",
        action_group="tool",
        operation="modify",
        action_type="tool_modify",
        target_path="tools/risk_checker.py",
        declared_write_paths=["tools/risk_checker.py", "tools/tools.yaml"],
        description="Add risk checker tool.",
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_tool",
        targets=[
            MemberOptimizationTarget(
                role="architect",
                member_name="architect",
                harness_ref_path=str(integration),
                confidence=0.9,
            )
        ],
        actions=[action],
        action_waves=[[action.action_id]],
    )
    (run_dir / "plan.yaml").write_text(
        yaml.safe_dump({"actions": [{"action_id": action.action_id}], "action_waves": [[action.action_id]]}),
        encoding="utf-8",
    )
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": action.action_id,
                        "role": "architect",
                        "status": "succeeded",
                        "merge_status": "merged",
                        "declared_write_paths": action.declared_write_paths,
                        "error": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        HarnessChangeVerifier().verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )

    assert result.status == "failed"
    checks = result.role_results["architect"].checks
    assert any(
        check.name == "tool_schema:architect:risk_checker"
        and check.status == "failed"
        and "type: object" in str(check.error)
        for check in checks
    )


def test_member_verifier_rejects_dangerous_package_relative_tool(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "architect" / "integration"
    shutil.copytree(two_role_harness_dir / "explainer", integration)
    (integration / "tools").mkdir(exist_ok=True)
    (integration / "tools" / "dangerous_tool.py").write_text(
        "\n".join(
            [
                "import os",
                "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                "class DangerousTool(Tool):",
                "    def __init__(self):",
                "        super().__init__(ToolCard(id='dangerous_tool', name='dangerous_tool', description='bad'))",
                "    async def invoke(self, inputs, **kwargs):",
                "        return os.getcwd()",
                "    async def stream(self, inputs, **kwargs):",
                "        if False:",
                "            yield inputs",
            ]
        ),
        encoding="utf-8",
    )
    (integration / "tools" / "tools.yaml").write_text(
        yaml.safe_dump({"tools": [{"file": "tools/dangerous_tool.py", "class_name": "DangerousTool"}]}),
        encoding="utf-8",
    )
    action = MemberOptimizationAction(
        action_id="tool_architect_001",
        role="architect",
        action_group="tool",
        operation="modify",
        action_type="tool_modify",
        target_path="tools/dangerous_tool.py",
        declared_write_paths=["tools/dangerous_tool.py", "tools/tools.yaml"],
    )
    plan = MemberOptimizationPlan(
        plan_id="plan_tool",
        targets=[
            MemberOptimizationTarget(
                role="architect",
                member_name="architect",
                harness_ref_path=str(integration),
                confidence=0.9,
            )
        ],
        actions=[action],
        action_waves=[[action.action_id]],
    )
    (run_dir / "plan.yaml").write_text(
        yaml.safe_dump({"actions": [{"action_id": action.action_id}], "action_waves": [[action.action_id]]}),
        encoding="utf-8",
    )
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": action.action_id,
                        "role": "architect",
                        "status": "succeeded",
                        "merge_status": "merged",
                        "declared_write_paths": action.declared_write_paths,
                        "error": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    import asyncio

    result = asyncio.run(
        HarnessChangeVerifier().verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )

    assert result.status == "failed"
    checks = result.role_results["architect"].checks
    assert any("tool_file_ref:architect:" in check.name and check.status == "failed" for check in checks)


def test_member_verifier_repairs_yaml_parse_failure(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text("role: [\n", encoding="utf-8")
    plan = _static_role_plan(two_role_harness_dir)
    repair_agent = _YamlRepairingHarnessRepairAgent()
    verifier = HarnessChangeVerifier(repair_agent=repair_agent)

    initial = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )
    fix = asyncio.run(
        verifier.repair(
            verification_result=initial,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "fix_result.json"),
            model_config_ref="unused-by-fake",
            stage_retry_limit=2,
        )
    )

    assert initial.status == "failed"
    assert initial.role_results["explainer"].repairable is True
    assert fix.status == "completed"
    assert fix.final_verification_status == "passed"
    assert fix.repairs[0].status == "succeeded"
    assert repair_agent.repair_calls
    assert yaml.safe_load((integration / "harness.yaml").read_text(encoding="utf-8"))["role"] == "explainer"


def test_member_verifier_repairs_python_compile_failure(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text(yaml.safe_dump({"role": "explainer"}), encoding="utf-8")
    (integration / "tools").mkdir()
    (integration / "tools" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    plan = _static_role_plan(two_role_harness_dir)
    repair_agent = _PythonRepairingHarnessRepairAgent()
    verifier = HarnessChangeVerifier(repair_agent=repair_agent)

    initial = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )
    fix = asyncio.run(
        verifier.repair(
            verification_result=initial,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "fix_result.json"),
            model_config_ref="unused-by-fake",
            stage_retry_limit=1,
        )
    )

    assert initial.status == "failed"
    assert any(
        check.name.startswith("python_compile:explainer/tools/broken.py") and check.status == "failed"
        for check in initial.role_results["explainer"].checks
    )
    assert fix.final_verification_status == "passed"
    assert fix.repairs[0].status == "succeeded"


def test_member_verifier_repair_text_without_file_change_still_fails(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text("role: [\n", encoding="utf-8")
    plan = _static_role_plan(two_role_harness_dir)
    repair_agent = _RepairingHarnessRepairAgent(repair_text="success")
    verifier = HarnessChangeVerifier(repair_agent=repair_agent)

    initial = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )
    fix = asyncio.run(
        verifier.repair(
            verification_result=initial,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "fix_result.json"),
            model_config_ref="unused-by-fake",
            stage_retry_limit=1,
        )
    )

    assert fix.final_verification_status == "failed"
    assert fix.repairs[0].status == "failed"
    assert fix.metadata["remaining_failed_checks"]["explainer"]


def test_member_verifier_does_not_repair_failed_action_result(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text(yaml.safe_dump({"role": "explainer"}), encoding="utf-8")
    plan = _single_action_plan(two_role_harness_dir)
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": "action_001",
                        "role": "explainer",
                        "status": "failed",
                        "merge_status": "",
                        "error": "executor failed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    repair_agent = _RepairingHarnessRepairAgent()
    verifier = HarnessChangeVerifier(repair_agent=repair_agent)

    result = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )
    fix = asyncio.run(
        verifier.repair(
            verification_result=result,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "fix_result.json"),
            model_config_ref="unused-by-fake",
            stage_retry_limit=1,
        )
    )

    assert result.role_results["explainer"].repairable is False
    assert repair_agent.repair_calls == []
    assert fix.status == "failed"
    assert "action_result:action_001" in fix.metadata["remaining_failed_checks"]["explainer"]


def test_member_verifier_mixed_static_and_action_failure_is_not_repairable(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    integration = worktrees_dir / "explainer" / "integration"
    integration.mkdir(parents=True)
    (integration / "harness.yaml").write_text("role: [\n", encoding="utf-8")
    plan = _single_action_plan(two_role_harness_dir)
    (run_dir / "execution_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "action_id": "action_001",
                        "role": "explainer",
                        "status": "failed",
                        "merge_status": "",
                        "error": "executor failed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    repair_agent = _YamlRepairingHarnessRepairAgent()
    verifier = HarnessChangeVerifier(repair_agent=repair_agent)

    result = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )

    assert result.role_results["explainer"].repairable is False
    assert repair_agent.repair_calls == []
    assert any(check.name.startswith("yaml_parse:explainer/") for check in result.role_results["explainer"].checks)
    assert any(check.name == "action_result:action_001" for check in result.role_results["explainer"].checks)


def test_member_verifier_repair_loop_preserves_role_isolation(
    tmp_path: Path,
    two_role_harness_dir: Path,
) -> None:
    run_dir = tmp_path / "member_optimization_001"
    worktrees_dir = run_dir / "worktrees"
    explainer = worktrees_dir / "explainer" / "integration"
    diagnostician = worktrees_dir / "diagnostician" / "integration"
    explainer.mkdir(parents=True)
    diagnostician.mkdir(parents=True)
    (explainer / "harness.yaml").write_text("role: [\n", encoding="utf-8")
    (diagnostician / "harness.yaml").write_text("role: [\n", encoding="utf-8")
    plan = MemberOptimizationPlan(
        plan_id="plan_multi",
        targets=[
            MemberOptimizationTarget(role="explainer", harness_ref_path=str(two_role_harness_dir / "explainer")),
            MemberOptimizationTarget(
                role="diagnostician",
                harness_ref_path=str(two_role_harness_dir / "diagnostician"),
            ),
        ],
        actions=[],
        action_waves=[],
    )

    class _OneRoleRepairAgent(_YamlRepairingHarnessRepairAgent):
        async def repair_role(self, role_integration_worktree, role, failed_checks):  # type: ignore[no-untyped-def]
            if role == "explainer":
                return await super().repair_role(role_integration_worktree, role, failed_checks)
            return await _RepairingHarnessRepairAgent.repair_role(
                self,
                role_integration_worktree,
                role,
                failed_checks,
            )

    repair_agent = _OneRoleRepairAgent()
    verifier = HarnessChangeVerifier(repair_agent=repair_agent)

    initial = asyncio.run(
        verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
    )
    fix = asyncio.run(
        verifier.repair(
            verification_result=initial,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "fix_result.json"),
            model_config_ref="unused-by-fake",
            stage_retry_limit=1,
        )
    )

    assert len(repair_agent.repair_calls) == 2
    assert fix.final_verification_status == "failed"
    assert "diagnostician" in fix.metadata["remaining_failed_checks"]
    assert any(item.role == "explainer" and item.status == "succeeded" for item in fix.repairs)
    assert any(item.role == "diagnostician" and item.status == "failed" for item in fix.repairs)


def test_member_action_planner_user_message_includes_skill_search_candidate_query() -> None:
    planner_agent = MemberActionPlannerAgent(model_config_ref="dummy")
    targets = [
        MemberOptimizationTarget(
            role="script_writer",
            member_name="script_writer",
            harness_ref_path="current_harnesses/script_writer",
            attributed_issue_ids=["issue_code_review_skill"],
            confidence=0.9,
            reason="role owns the skill gap",
            mechanism_types=["skill"],
        )
    ]
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_code_review_skill",
                role="script_writer",
                harness_ref_path="current_harnesses/script_writer",
                confidence=0.9,
                rationale="role lacks a reusable review skill",
                evidence=[
                    {
                        "summary": "script_writer lacks an external code review skill",
                        "recommendation": "Use skill/search with candidate_query exactly 'code review'.",
                        "candidate_query": "code review",
                    }
                ],
                trace_refs=[],
            )
        ],
        unassigned_issues=[],
        metadata={},
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "script_writer": [
                RoleMechanismAttribution(
                    issue_id="issue_code_review_skill",
                    role="script_writer",
                    mechanism_type="skill",
                    failure_signature="skill_code_failure",
                    confidence=0.95,
                    rationale="missing skill",
                    evidence=[{"summary": "missing code review skill"}],
                    evidence_refs=[],
                )
            ]
        },
        metadata={},
    )

    message = planner_agent._build_user_message(
        targets,
        role_report,
        mechanism_report,
        [
            ActionDefinition(
                name="skill_search",
                group="skill",
                operation="search",
                function="search_skill",
                purpose="Search for an existing reusable skill",
            )
        ],
    )

    assert "Allowed action_group values" in message
    assert "skill/search: Search for an existing reusable skill" in message
    assert "candidate_query=code review" in message
    assert "candidate_query exactly 'code review'" in message


def test_member_action_planner_disables_search_in_single_action_mode() -> None:
    class LocalPatchPlannerAgent:
        seen_definitions: list[ActionDefinition] = []

        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            self.seen_definitions = kwargs["action_definitions"]
            return {
                "plan_id": "plan_local_skill",
                "actions": [
                    {
                        "action_id": "act_local_skill",
                        "role": "game-developer",
                        "action_group": "skill",
                        "operation": "add",
                        "action_type": "skill_add",
                        "target_path": "skills/deterministic_dom_identity/SKILL.md",
                        "description": "Add a bounded local skill from current evidence.",
                        "rationale": "The failure mechanism is already concrete.",
                        "depends_on": [],
                        "declared_write_paths": [
                            "skills/deterministic_dom_identity/SKILL.md",
                            "skills/skills.yaml",
                        ],
                    }
                ],
                "action_waves": [["act_local_skill"]],
            }

    target = MemberOptimizationTarget(
        role="game-developer",
        harness_ref_path="game-developer",
        attributed_issue_ids=["issue_dom_identity"],
        confidence=0.9,
        mechanism_types=["skill"],
        optimization_surfaces=["skill"],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_dom_identity",
                role="game-developer",
                harness_ref_path="game-developer",
                confidence=0.9,
            )
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "game-developer": [
                RoleMechanismAttribution(
                    issue_id="issue_dom_identity",
                    role="game-developer",
                    mechanism_type="skill",
                    failure_signature="missing_dom_identity_contract",
                    confidence=0.9,
                    optimization_surface="skill",
                )
            ]
        }
    )
    planner_agent = LocalPatchPlannerAgent()

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=planner_agent).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[
                ActionDefinition(
                    name="skill_search",
                    group="skill",
                    operation="search",
                    function="search_skill",
                    purpose="Search externally",
                ),
                ActionDefinition(
                    name="skill_add",
                    group="skill",
                    operation="add",
                    function="add_skill",
                    purpose="Create a local skill",
                ),
            ],
            model_config_ref="unused",
            allowed_action_groups=["skill"],
            max_actions_per_plan=1,
        )
    )

    assert [definition.operation for definition in planner_agent.seen_definitions] == ["add"]
    assert plan.actions[0].operation == "add"
    assert plan.metadata["skill_search_disabled_for_single_action"] is True


def test_member_action_planner_reports_the_latest_action_budget_error() -> None:
    class RetryingPlannerAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return {
                    "plan_id": "merged_plan",
                    "actions": [
                        {
                            "action_id": "action_1",
                            "role": "solver",
                            "action_group": "skill",
                            "operation": "add",
                            "action_type": "skill_add",
                            "target_path": "skills/first/SKILL.md",
                            "declared_write_paths": [
                                "skills/first/SKILL.md",
                                "skills/skills.yaml",
                            ],
                            "description": "Fix both diagnosed issues.",
                            "attributed_issue_ids": ["issue_001"],
                            "depends_on": [],
                        }
                    ],
                    "action_waves": [["action_1"]],
                }
            return {
                "plan_id": "two_action_plan",
                "actions": [
                    {
                        "action_id": "action_1",
                        "role": "solver",
                        "action_group": "skill",
                        "operation": "add",
                        "action_type": "skill_add",
                        "target_path": "skills/first/SKILL.md",
                        "declared_write_paths": [
                            "skills/first/SKILL.md",
                            "skills/skills.yaml",
                        ],
                        "description": "Fix the first diagnosed issue.",
                        "attributed_issue_ids": ["issue_001"],
                        "depends_on": [],
                    },
                    {
                        "action_id": "action_2",
                        "role": "solver",
                        "action_group": "skill",
                        "operation": "add",
                        "action_type": "skill_add",
                        "target_path": "skills/second/SKILL.md",
                        "declared_write_paths": [
                            "skills/second/SKILL.md",
                            "skills/skills.yaml",
                        ],
                        "description": "Fix the second diagnosed issue.",
                        "attributed_issue_ids": ["issue_002"],
                        "depends_on": [],
                    },
                ],
                "action_waves": [["action_1"], ["action_2"]],
            }

    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["issue_001", "issue_002"],
        confidence=1.0,
        mechanism_types=["skill"],
        optimization_surfaces=["skill"],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_001",
                role="solver",
                harness_ref_path="solver",
                confidence=1.0,
            ),
            RoleIssueAttribution(
                issue_id="issue_002",
                role="solver",
                harness_ref_path="solver",
                confidence=1.0,
            ),
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="issue_001",
                    role="solver",
                    mechanism_type="skill",
                    failure_signature="first_issue",
                    confidence=1.0,
                    optimization_surface="skill",
                ),
                RoleMechanismAttribution(
                    issue_id="issue_002",
                    role="solver",
                    mechanism_type="skill",
                    failure_signature="second_issue",
                    confidence=1.0,
                    optimization_surface="skill",
                ),
            ],
        }
    )

    with pytest.raises(
        RuntimeError,
        match="restricted optimization mode allows at most 1 action",
    ):
        asyncio.run(
            MemberActionPlanner(
                planner_agent=RetryingPlannerAgent(),
            ).plan(
                targets=[target],
                role_attribution_report=role_report,
                mechanism_attribution_report=mechanism_report,
                action_definitions=[
                    ActionDefinition(
                        name="skill_add",
                        group="skill",
                        operation="add",
                        function="add_skill",
                        purpose="Create a local skill",
                    )
                ],
                model_config_ref="unused",
                allowed_action_groups=["skill"],
                max_actions_per_plan=1,
            )
        )


@pytest.mark.parametrize(
    "failure_class",
    ["late_skill_activation", "execution_convergence_failure"],
)
def test_planner_turns_unapplied_skill_into_execution_checkpoint(
    failure_class: str,
) -> None:
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _adapt_recovery_surface_from_history,
    )

    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["issue_owner"],
        optimization_surfaces=["skill"],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_owner",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"case_id": "marshmallow__marshmallow-1359"}],
            )
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="issue_owner",
                    role="solver",
                    mechanism_type="workflow",
                    failure_signature="root_owner_not_applied",
                    confidence=0.9,
                    optimization_surface="skill",
                    rationale="The owner chain was understood but no edit was produced.",
                )
            ]
        }
    )

    targets, mechanisms, adaptations = _adapt_recovery_surface_from_history(
        targets=[target],
        role_report=role_report,
        mechanism_report=mechanism_report,
        rejected_capabilities=[
            {
                "role": "solver",
                "action_group": "skill",
                "runtime_name": "owner_chain",
                "target_case_ids": ["marshmallow__marshmallow-1359"],
                "failure_class": failure_class,
            }
        ],
    )

    assert targets[0].optimization_surfaces == ["control"]
    assert mechanisms.role_mechanisms["solver"][0].optimization_surface == "control"
    assert adaptations[0]["failure_class"] == failure_class


def test_planner_keeps_skill_surface_after_semantic_replay_failure() -> None:
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _adapt_recovery_surface_from_history,
    )

    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["issue_iterator"],
        optimization_surfaces=["skill"],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_iterator",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"case_id": "pydicom__pydicom-1139"}],
            )
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="issue_iterator",
                    role="solver",
                    mechanism_type="skill",
                    failure_signature="stateful_next_contract_missing",
                    confidence=0.9,
                    optimization_surface="skill",
                )
            ]
        }
    )

    targets, mechanisms, adaptations = _adapt_recovery_surface_from_history(
        targets=[target],
        role_report=role_report,
        mechanism_report=mechanism_report,
        rejected_capabilities=[
            {
                "role": "solver",
                "action_group": "skill",
                "target_case_ids": ["pydicom__pydicom-1139"],
                "failure_class": "semantic_non_reproduction",
            }
        ],
    )

    assert targets[0].optimization_surfaces == ["skill"]
    assert mechanisms.role_mechanisms["solver"][0].optimization_surface == "skill"
    assert adaptations == []


def test_member_action_planner_recovers_when_model_reintroduces_disabled_search() -> None:
    """A new contract error after a size retry must still get a correction attempt."""

    class RetryingPlannerAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.validation_errors: list[list[str] | None] = []

        async def create_plan(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            self.validation_errors.append(kwargs.get("validation_errors"))
            search = {
                "action_id": "act_skill_search",
                "role": "game-developer",
                "action_group": "skill",
                "operation": "search",
                "action_type": "skill_search",
                "target_path": "skills/",
                "candidate_query": "runtime verification",
                "depends_on": [],
                "declared_write_paths": ["skills/", "skills/skills.yaml"],
            }
            add = {
                "action_id": "act_skill_add",
                "role": "game-developer",
                "action_group": "skill",
                "operation": "add",
                "action_type": "skill_add",
                "target_path": "skills/runtime_verification/SKILL.md",
                "candidate_query": "",
                "depends_on": [],
                "declared_write_paths": [
                    "skills/runtime_verification/SKILL.md",
                    "skills/skills.yaml",
                ],
            }
            if self.calls == 1:
                actions = [search, add]
            elif self.calls == 2:
                actions = [search]
            else:
                actions = [add]
            return {
                "plan_id": "plan_retry_contract",
                "actions": actions,
                "action_waves": [[action["action_id"] for action in actions]],
            }

    target = MemberOptimizationTarget(
        role="game-developer",
        harness_ref_path="game-developer",
        attributed_issue_ids=["issue_runtime"],
        confidence=0.9,
        mechanism_types=["skill"],
        optimization_surfaces=["skill"],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_runtime",
                role="game-developer",
                harness_ref_path="game-developer",
                confidence=0.9,
            )
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "game-developer": [
                RoleMechanismAttribution(
                    issue_id="issue_runtime",
                    role="game-developer",
                    mechanism_type="skill",
                    failure_signature="runtime_reference_error",
                    confidence=0.9,
                    optimization_surface="skill",
                )
            ]
        }
    )
    planner_agent = RetryingPlannerAgent()

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=planner_agent).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[
                ActionDefinition(
                    name="skill_search",
                    group="skill",
                    operation="search",
                    function="search_skill",
                    purpose="Search externally",
                ),
                ActionDefinition(
                    name="skill_add",
                    group="skill",
                    operation="add",
                    function="add_skill",
                    purpose="Create a local skill",
                ),
            ],
            model_config_ref="unused",
            allowed_action_groups=["skill"],
            max_actions_per_plan=1,
        )
    )

    assert planner_agent.calls == 3
    assert planner_agent.validation_errors[1] == ["restricted optimization mode allows at most 1 action(s)"]
    assert any(
        "not present in the run-specific action contract" in error for error in planner_agent.validation_errors[2] or []
    )
    assert [action.operation for action in plan.actions] == ["add"]


def test_member_action_contract_marks_unoffered_search_disabled() -> None:
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _build_action_contract_text,
    )

    contract = _build_action_contract_text(
        [
            ActionDefinition(
                name="skill_add",
                group="skill",
                operation="add",
                function="add_skill",
                purpose="Create a local skill",
            )
        ]
    )

    assert "Run-Specific Strict Allowlist" in contract
    assert "skill/search is disabled for this run" in contract


def test_member_action_planner_rejects_skill_add_success_dependency_on_skill_search() -> None:
    """skill/add may follow failed search only with explicit dependency_failed semantics."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _validate_plan,
    )

    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="harnesses/solver",
        attributed_issue_ids=["issue_001"],
        confidence=0.8,
        optimization_surfaces=["skill"],
    )
    plan_data = {
        "actions": [
            {
                "action_id": "act_skill_search",
                "role": "solver",
                "action_group": "skill",
                "operation": "search",
                "action_type": "skill_search",
                "target_path": "skills/",
                "candidate_query": "preserve analyze fix",
                "declared_write_paths": ["skills", "skills/skills.yaml"],
            },
            {
                "action_id": "act_skill_add",
                "role": "solver",
                "action_group": "skill",
                "operation": "add",
                "action_type": "skill_creation",
                "target_path": "skills/preserve_analyze_fix/SKILL.md",
                "depends_on": ["act_skill_search"],
                "declared_write_paths": [
                    "skills/preserve_analyze_fix/SKILL.md",
                    "skills/skills.yaml",
                ],
            },
        ],
        "action_waves": [["act_skill_search"], ["act_skill_add"]],
    }

    errors = _validate_plan(plan_data, {"solver"}, [target])

    assert any("skill/add fallback after skill/search" in error for error in errors)

    plan_data["actions"][1]["run_if"] = "dependency_failed"
    assert _validate_plan(plan_data, {"solver"}, [target]) == []


def test_member_action_planner_rejects_action_removed_from_run_contract() -> None:
    """A model cannot reintroduce skill/search after one-action filtering removes it."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _validate_plan,
    )

    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="harnesses/solver",
        attributed_issue_ids=["issue_001"],
        confidence=0.8,
        optimization_surfaces=["skill"],
    )
    plan_data = {
        "actions": [
            {
                "action_id": "act_skill_search",
                "role": "solver",
                "action_group": "skill",
                "operation": "search",
                "action_type": "capability_acquisition",
                "target_path": "skills/",
                "candidate_query": "runtime verification",
                "declared_write_paths": ["skills", "skills/skills.yaml"],
            }
        ],
        "action_waves": [["act_skill_search"]],
    }
    offered = [
        ActionDefinition(
            name="skill_add",
            group="skill",
            operation="add",
            function="add_skill",
            purpose="Create a bounded local skill",
        )
    ]

    errors = _validate_plan(plan_data, {"solver"}, [target], offered)

    assert any("not present in the run-specific action contract" in error for error in errors)


def test_member_action_planner_rejects_action_when_run_contract_is_empty() -> None:
    """An explicitly empty run contract must fail closed for model actions."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _validate_plan,
    )

    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="harnesses/solver",
        attributed_issue_ids=["issue_001"],
        confidence=0.8,
        optimization_surfaces=["skill"],
    )
    plan_data = {
        "actions": [
            {
                "action_id": "act_skill_search",
                "role": "solver",
                "action_group": "skill",
                "operation": "search",
                "action_type": "capability_acquisition",
                "target_path": "skills/",
                "candidate_query": "runtime verification",
                "declared_write_paths": ["skills", "skills/skills.yaml"],
            }
        ],
        "action_waves": [["act_skill_search"]],
    }

    errors = _validate_plan(plan_data, {"solver"}, [target], [])

    assert any("not present in the run-specific action contract" in error for error in errors)


def test_member_action_planner_rejects_empty_plan_for_actionable_surface() -> None:
    """Selected roles with a supported diagnosed surface need executable actions."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _validate_plan,
    )

    target = MemberOptimizationTarget(
        role="game-engineer",
        harness_ref_path="harnesses/game-engineer",
        attributed_issue_ids=["issue_tool_gap"],
        confidence=0.82,
        optimization_surfaces=["tool"],
    )
    plan_data = {
        "plan_id": "plan_empty",
        "targets": [{"role": "game-engineer"}],
        "actions": [],
        "action_waves": [],
    }

    errors = _validate_plan(
        plan_data,
        {"game-engineer"},
        [target],
        [
            ActionDefinition(
                name="tool_add",
                group="tool",
                operation="add",
                function="add_tool",
                purpose="Add a package-local deterministic tool",
            )
        ],
    )

    assert any("target game-engineer has actionable optimization_surfaces" in error for error in errors)


def test_member_action_planner_defers_tool_gap_instead_of_recasting_as_skill() -> None:
    """An unavailable Action lever must not be disguised as Instruction."""

    class RestrictedSkillPlannerAgent:
        async def create_plan(self, **kwargs):
            raise AssertionError("planner must not run for an unsupported lever")

    target = MemberOptimizationTarget(
        role="game-coder",
        member_name="game-coder",
        harness_ref_path="harnesses/game-coder",
        attributed_issue_ids=["issue_undefined_call"],
        confidence=0.92,
        mechanism_types=["tool"],
        optimization_surfaces=["tool"],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_undefined_call",
                role="game-coder",
                harness_ref_path="harnesses/game-coder",
                confidence=0.92,
            )
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "game-coder": [
                RoleMechanismAttribution(
                    issue_id="issue_undefined_call",
                    role="game-coder",
                    mechanism_type="tool",
                    failure_signature="undefined_function_reference",
                    confidence=0.88,
                    optimization_surface="tool",
                    rationale="A deterministic pre-delivery check is missing.",
                )
            ]
        }
    )
    planner_agent = RestrictedSkillPlannerAgent()

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=planner_agent).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[
                ActionDefinition(
                    name="skill_add",
                    group="skill",
                    operation="add",
                    function="add_skill",
                    purpose="Create a bounded local skill",
                )
            ],
            model_config_ref="unused",
            allowed_action_groups=["prompt", "skill"],
            max_actions_per_plan=1,
        )
    )

    assert plan.actions == []
    assert plan.targets == []
    request = plan.metadata["capability_requests"][0]
    assert request["required_surfaces"] == ["tool"]
    assert request["status"] == "unsupported_capability_request"
    assert "cross_lever_compensation_is_forbidden" in request["reason"]


def test_member_action_planner_hard_limits_prompt_surface() -> None:
    class RestrictedPromptPlannerAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.seen_targets = []

        async def create_plan(self, **kwargs):
            self.calls += 1
            self.seen_targets = kwargs["targets"]
            if self.calls == 1:
                target_path = "identity.md"
                declared_paths = ["identity.md"]
                constraints = {}
            else:
                target_path = "prompt_sections/files/recovery_contract.md"
                declared_paths = [
                    target_path,
                    "prompt_sections/sections.yaml",
                ]
                constraints = {
                    "section_name": "recovery_contract",
                    "priority": 50,
                }
            return {
                "plan_id": "member_plan_prompt_surface",
                "targets": [],
                "actions": [
                    {
                        "action_id": "act_prompt",
                        "role": "game-coder",
                        "member_name": "game-coder",
                        "action_group": "prompt",
                        "operation": "modify",
                        "action_type": "prompt_modify",
                        "target_path": target_path,
                        "description": "Encode the recovery contract.",
                        "rationale": "Prevent repeated missing evidence.",
                        "depends_on": [],
                        "declared_write_paths": declared_paths,
                        "constraints": constraints,
                    }
                ],
                "action_waves": [["act_prompt"]],
            }

    target = MemberOptimizationTarget(
        role="game-coder",
        member_name="game-coder",
        harness_ref_path="harnesses/game-coder",
        attributed_issue_ids=["issue_recovery"],
        confidence=0.9,
        mechanism_types=["prompt"],
        optimization_surfaces=["identity"],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_recovery",
                role="game-coder",
                harness_ref_path="harnesses/game-coder",
                confidence=0.9,
            )
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "game-coder": [
                RoleMechanismAttribution(
                    issue_id="issue_recovery",
                    role="game-coder",
                    mechanism_type="prompt",
                    failure_signature="missing_recovery_contract",
                    confidence=0.9,
                    optimization_surface="identity",
                )
            ]
        }
    )
    planner_agent = RestrictedPromptPlannerAgent()

    plan = asyncio.run(
        MemberActionPlanner(planner_agent=planner_agent).plan(
            targets=[target],
            role_attribution_report=role_report,
            mechanism_attribution_report=mechanism_report,
            action_definitions=[
                ActionDefinition(
                    name="prompt_modify",
                    group="prompt",
                    operation="modify",
                    function="modify_prompt",
                    purpose="Modify a prompt surface",
                )
            ],
            model_config_ref="unused",
            allowed_action_groups=["prompt", "tool"],
            allowed_prompt_surfaces=["prompt_section"],
            max_actions_per_plan=1,
        )
    )

    assert planner_agent.calls == 2
    assert planner_agent.seen_targets[0].optimization_surfaces == ["prompt_section"]
    assert plan.actions[0].target_path.startswith("prompt_sections/files/")
    assert plan.metadata["allowed_prompt_surfaces"] == ["prompt_section"]


def test_role_attribution_matches_stable_member_alias() -> None:
    """Analyzer display names should resolve through persisted member identity aliases."""
    from openjiuwen.rsi.member_optimizer.role_attributor import (
        _match_target_member,
    )

    issue = TeamIssue(
        issue_id="issue_frontend",
        category="member_harness",
        severity="medium",
        summary="frontend interaction bug",
        optimization_target="member_harness",
        target_members=["frontend-developer"],
    )
    candidate = MemberRoleCandidate(
        role="frontend-engineer",
        member_name="frontend-engineer",
        harness_ref_path="frontend-harness",
        metadata={
            "member_id": "frontend-engineer",
            "aliases": ["frontend-developer", "frontend-engineer"],
        },
    )

    matched, status = _match_target_member(issue, [candidate])

    assert matched == candidate
    assert status == "target_members_exact_match"


def test_member_action_planner_rejects_duplicate_add_target(tmp_path: Path) -> None:
    """An existing Prompt/Skill/Tool must be modified rather than added again."""
    from openjiuwen.rsi.member_optimizer.action_planner import (
        _validate_plan,
    )

    harness = tmp_path / "frontend"
    existing = harness / "prompt_sections" / "files" / "deliverable_checklist.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("# Existing checklist\n", encoding="utf-8")
    target = MemberOptimizationTarget(
        role="frontend-developer",
        harness_ref_path=str(harness),
        attributed_issue_ids=["issue_001"],
        confidence=0.9,
        optimization_surfaces=["prompt_section"],
    )
    plan_data = {
        "actions": [
            {
                "action_id": "duplicate_add",
                "role": "frontend-developer",
                "action_group": "prompt",
                "operation": "add",
                "action_type": "prompt_section_add",
                "target_path": "prompt_sections/files/deliverable_checklist.md",
                "declared_write_paths": [
                    "prompt_sections/files/deliverable_checklist.md",
                    "prompt_sections/sections.yaml",
                ],
            }
        ],
        "action_waves": [["duplicate_add"]],
    }

    errors = _validate_plan(plan_data, {"frontend-developer"}, [target])

    assert any("target_path already exists" in error for error in errors)


def test_skill_acquisition_parses_skill_hub_list_response() -> None:
    result = SkillAcquisitionResult(status="failed", query="code review")
    acquisition = SkillAcquisition(
        runner=_failing_skill_search_runner,
        skill_hub_url="https://skill-hub.example/search",
        skill_hub_fetcher=lambda query, url, token: [
            {
                "ref": "demo/skills@skills/code-review",
                "install_count": "2.5K",
                "security_rating": "safe",
                "source_url": "https://github.com/demo/skills/tree/main/skills/code-review",
            }
        ],
    )

    candidates = acquisition.search("code review", result)

    assert [candidate.ref for candidate in candidates] == ["demo/skills@skills/code-review"]
    candidate = candidates[0]
    assert candidate.backend == "skill_hub"
    assert candidate.install_count == 2500
    assert candidate.security_rating == "Safe"
    assert candidate.source_url.endswith("/skills/code-review")
    assert {item["ref"] for item in result.rejections} == {"npx", "skillnet"}


def test_skill_acquisition_parses_skill_hub_results_response() -> None:
    acquisition = SkillAcquisition(
        runner=_failing_skill_search_runner,
        skill_hub_url="https://skill-hub.example/search",
        skill_hub_fetcher=lambda query, url, token: {
            "results": [
                {
                    "ref": "demo/skills@skills/review-helper",
                    "installs": 1800,
                    "security": "low",
                    "url": "https://github.com/demo/skills",
                }
            ]
        },
    )

    candidates = acquisition.search("review helper")

    assert len(candidates) == 1
    assert candidates[0].ref == "demo/skills@skills/review-helper"
    assert candidates[0].install_count == 1800
    assert candidates[0].security_rating == "Low"
    assert candidates[0].source_url == "https://github.com/demo/skills"


def test_skill_acquisition_skips_skill_hub_when_url_absent() -> None:
    def unexpected_fetcher(query, url, token):  # type: ignore[no-untyped-def]
        raise AssertionError("Skill Hub should be disabled when URL is absent")

    result = SkillAcquisitionResult(status="failed", query="code review")
    acquisition = SkillAcquisition(
        runner=_failing_skill_search_runner,
        skill_hub_url="",
        skill_hub_fetcher=unexpected_fetcher,
    )

    candidates = acquisition.search("code review", result)

    assert candidates == []
    assert {item["ref"] for item in result.rejections} == {"npx", "skillnet"}


def test_skill_command_timeout_terminates_descendant_holding_output_pipe() -> None:
    """A child inheriting stdout must not keep the timed-out runner blocked."""
    parent_code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        _run_command(
            [sys.executable, "-c", parent_code],
            None,
            timeout=0.2,
        )

    assert time.monotonic() - started < 5


def test_skill_acquisition_records_skill_hub_errors_without_aborting() -> None:
    def fake_runner(command, cwd):  # type: ignore[no-untyped-def]
        if _is_skill_find_command(command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="demo/skills@skills/npx-review\ninstalls: 2K\nsecurity: Safe\n",
                stderr="",
            )
        if command[:2] == ["skillnet", "search"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
        raise AssertionError(f"unexpected command: {command}")

    result = SkillAcquisitionResult(status="failed", query="code review")
    acquisition = SkillAcquisition(
        runner=fake_runner,
        skill_hub_url="https://skill-hub.example/search",
        skill_hub_fetcher=lambda query, url, token: (_ for _ in ()).throw(ValueError("bad json")),
    )

    candidates = acquisition.search("code review", result)

    assert [candidate.ref for candidate in candidates] == ["demo/skills@skills/npx-review"]
    assert any(item["ref"] == "skill_hub" and "bad json" in item["reason"] for item in result.rejections)


def test_skill_acquisition_falls_back_to_skill_hub_when_command_sources_fail(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    def fake_runner(command, cwd):  # type: ignore[no-untyped-def]
        if _is_skill_find_command(command):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="npx failed")
        if command[:2] == ["skillnet", "search"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="skillnet failed")
        if _is_git_clone_command(command):
            checkout = Path(command[-1])
            skill_dir = checkout / "skills" / "hub-review"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\ndescription: hub review skill\n---\n\n# Hub Review\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if _is_git_ls_tree_command(command):
            return subprocess.CompletedProcess(command, 0, stdout="skills/hub-review/SKILL.md\n", stderr="")
        if _is_git_sparse_command(command):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    acquisition = SkillAcquisition(
        runner=fake_runner,
        skill_hub_url="https://skill-hub.example/search",
        skill_hub_fetcher=lambda query, url, token: {
            "results": [
                {
                    "ref": "demo/skills@skills/hub-review",
                    "install_count": 5000,
                    "security_rating": "Safe",
                }
            ]
        },
    )

    result = acquisition.acquire(action_worktree=worktree, query="code review")

    assert result.status == "succeeded"
    assert result.selected_ref == "demo/skills@skills/hub-review"
    assert result.safety_scan["status"] == "passed"
    assert (worktree / "skills" / "hub-review" / "SKILL.md").is_file()
    assert yaml.safe_load((worktree / "skills" / "skills.yaml").read_text(encoding="utf-8")) == {"skills": ["skills"]}


def test_skill_acquisition_resolves_real_skill_dir_from_sparse_repo_tree(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    sparse_paths: list[str] = []

    def fake_runner(command, cwd):  # type: ignore[no-untyped-def]
        if _is_skill_find_command(command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="demo/skills@feature-dev-loop\ninstalls: 2K\nsecurity: Safe\n",
                stderr="",
            )
        if command[:2] == ["skillnet", "search"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing")
        if _is_git_clone_command(command):
            Path(command[-1]).mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if _is_git_ls_tree_command(command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\n".join(
                    [
                        "skills/feature-dev-loop/SKILL.md",
                        "skills/other-review/SKILL.md",
                    ]
                ),
                stderr="",
            )
        if _is_git_sparse_command(command):
            sparse_path = command[-1]
            sparse_paths.append(sparse_path)
            skill_dir = _git_checkout_dir(command) / sparse_path
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\ndescription: feature dev loop\n---\n\n# Feature Dev Loop\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    result = SkillAcquisition(runner=fake_runner, skill_hub_url="").acquire(
        action_worktree=worktree,
        query="code review",
    )

    assert result.status == "succeeded"
    assert result.selected_ref == "demo/skills@feature-dev-loop"
    assert sparse_paths == ["skills/feature-dev-loop"]
    assert (worktree / "skills" / "feature-dev-loop" / "SKILL.md").is_file()


def test_skill_safety_scan_rejects_dangerous_script(tmp_path: Path) -> None:
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: bad\n---\n", encoding="utf-8")
    (skill_dir / "script.py").write_text(
        "import subprocess\nsubprocess.run(['echo', 'bad'])\n",
        encoding="utf-8",
    )

    scan = scan_skill_directory(skill_dir)

    assert scan["status"] == "failed"
    assert any("dangerous import" in error for error in scan["errors"])


def _is_skill_find_command(command) -> bool:  # type: ignore[no-untyped-def]
    executable = Path(str(command[0])).name.lower() if command else ""
    return executable in {"npx", "npx.cmd"} and command[1:3] == ["skills", "find"]


def _is_git_clone_command(command) -> bool:  # type: ignore[no-untyped-def]
    return command and command[0] == "git" and "clone" in command


def _is_git_sparse_command(command) -> bool:  # type: ignore[no-untyped-def]
    return command and command[0] == "git" and "sparse-checkout" in command


def _is_git_ls_tree_command(command) -> bool:  # type: ignore[no-untyped-def]
    return command and command[0] == "git" and "ls-tree" in command


def _git_checkout_dir(command) -> Path:  # type: ignore[no-untyped-def]
    return Path(command[command.index("-C") + 1])


def _failing_skill_search_runner(command, cwd):  # type: ignore[no-untyped-def]
    if _is_skill_find_command(command):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="npx failed")
    if command[:2] == ["skillnet", "search"]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="skillnet failed")
    raise AssertionError(f"unexpected command: {command}")
