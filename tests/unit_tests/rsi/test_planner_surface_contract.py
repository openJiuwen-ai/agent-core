# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exercise diagnosed surfaces through planning, not just surface helpers."""

import asyncio
from copy import deepcopy

import pytest

from openjiuwen.rsi.harness_rsi.member_optimizer.action_planner import MemberActionPlanner
from openjiuwen.rsi.harness_rsi.member_optimizer.member_selector import MemberSelector
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
    ActionDefinition,
    MechanismAttributionReport,
    MemberOptimizationTarget,
    RoleAttributionReport,
    RoleIssueAttribution,
    RoleMechanismAttribution,
)


class RecordingPlanner:
    def __init__(self, group, *, empty=False):
        self.group = group
        self.empty = empty
        self.calls = []

    async def create_plan(self, *, optimization_hypotheses=None, **kwargs):
        self.calls.append(deepcopy({**kwargs, "optimization_hypotheses": optimization_hypotheses}))
        if self.empty:
            return {"actions": [], "action_waves": []}
        path = {
            "skill": "skills/boundary_check/SKILL.md",
            "prompt": "prompt_sections/files/boundary_check.md",
            "rail": "rails/boundary_check.py",
        }[self.group]
        manifests = {
            "skill": "skills/skills.yaml",
            "prompt": "prompt_sections/sections.yaml",
            "rail": "rails/rails.yaml",
        }
        return {
            "actions": [
                {
                    "action_id": "a1",
                    "role": "solver",
                    "action_group": self.group,
                    "operation": "add",
                    "action_type": "boundary_check",
                    "target_path": path,
                    "declared_write_paths": [path, manifests[self.group]],
                    "attributed_issue_ids": ["issue_1"],
                    "description": "Verify the boundary required by the task contract.",
                    "depends_on": [],
                    "constraints": {
                        "rail": {"class_name": "BoundaryCheckRail"},
                        "prompt": {"section_name": "boundary_check"},
                        "skill": {},
                    }[self.group],
                }
            ],
            "action_waves": [["a1"]],
        }


def plan_inputs(surface, phase="pre_submission"):
    lever = "control" if surface == "rail" else "instruction"
    target = MemberOptimizationTarget(
        role="solver",
        harness_ref_path="solver",
        attributed_issue_ids=["issue_1"],
        optimization_surfaces=[surface],
    )
    role_report = RoleAttributionReport(
        assigned_role_issues=[
            RoleIssueAttribution(
                issue_id="issue_1",
                role="solver",
                harness_ref_path="solver",
                confidence=0.9,
                evidence=[{"case_id": "training-1", "summary": "Boundary check missing."}],
            )
        ]
    )
    mechanism_report = MechanismAttributionReport(
        role_mechanisms={
            "solver": [
                RoleMechanismAttribution(
                    issue_id="issue_1",
                    role="solver",
                    mechanism_type=lever,
                    failure_signature="missing_boundary_check",
                    confidence=0.9,
                    optimization_surface=surface,
                ),
            ]
        }
    )
    return {
        "targets": [target],
        "role_attribution_report": role_report,
        "mechanism_attribution_report": mechanism_report,
        "action_definitions": [
            ActionDefinition(
                name=f"{group}_add",
                group=group,
                operation="add",
                function=f"add_{group}",
                purpose="Add the diagnosed capability.",
            )
            for group in ("prompt", "skill", "rail")
        ],
        "model_config_ref": "unused",
        "allowed_action_groups": ["prompt", "skill", "tool", "rail"],
        "allowed_prompt_surfaces": ["prompt_section"],
        "max_actions_per_plan": 1,
        "optimization_hypotheses": [
            {
                "hypothesis_id": "h1",
                "source_issue_id": "issue_1",
                "content_sha256": "original",
                "required_behavior": "Check the boundary before handing off the result.",
                "decision_contract": {"activation_phase": phase},
                "lever_policy": {"recommended_lever": lever},
            }
        ],
    }


@pytest.mark.parametrize("phase", ["task_start", "during_investigation", "post_diagnosis", "pre_submission"])
@pytest.mark.parametrize("surface,group", [("skill", "skill"), ("prompt_section", "prompt")])
def test_activation_timing_does_not_rewrite_diagnosed_surface(phase, surface, group):
    agent = RecordingPlanner(group)
    inputs = plan_inputs(surface, phase)
    original = deepcopy(inputs)

    result = asyncio.run(MemberActionPlanner(agent).plan(**inputs))

    assert len(agent.calls) == 1
    assert result.targets[0].optimization_surfaces == [surface]
    assert result.actions[0].action_group == group
    assert result.actions[0].constraints["lever_decision"]["lever_matches_diagnosis"] is True
    contract = result.actions[0].constraints["optimization_contracts"][0]
    assert contract["decision_contract"]["activation_phase"] == phase
    assert inputs == original


@pytest.mark.parametrize(
    "failure_class",
    [
        "late_skill_activation",
        "natural_skill_activation_failure",
        "execution_convergence_failure",
        "semantic_non_reproduction",
    ],
)
def test_history_is_passed_as_feedback_without_overwriting_current_diagnosis(failure_class):
    agent = RecordingPlanner("skill")
    inputs = plan_inputs("skill", "task_start")
    history = [
        {
            "role": "solver",
            "action_group": "skill",
            "target_case_ids": ["training-1"],
            "failure_class": failure_class,
        }
    ]

    result = asyncio.run(MemberActionPlanner(agent).plan(**inputs, rejected_capabilities=history))

    assert result.actions[0].action_group == "skill"
    assert agent.calls[0]["rejected_capabilities"] == history
    assert result.targets[0].optimization_surfaces == ["skill"]


def test_explicit_rail_diagnosis_is_retained_and_executable():
    agent = RecordingPlanner("rail")
    result = asyncio.run(MemberActionPlanner(agent).plan(**plan_inputs("rail")))

    assert result.targets[0].optimization_surfaces == ["rail"]
    assert result.actions[0].action_group == "rail"
    assert result.actions[0].constraints["lever_decision"]["selected_surface"] == "rail"
    assert result.actions[0].constraints["lever_decision"]["lever_matches_diagnosis"] is True


def test_empty_plan_cannot_silently_drop_supported_rail():
    agent = RecordingPlanner("rail", empty=True)
    with pytest.raises(RuntimeError, match="no executable action"):
        asyncio.run(MemberActionPlanner(agent).plan(**plan_inputs("rail")))


def test_unavailable_rail_is_deferred_not_recast_as_instruction():
    agent = RecordingPlanner("skill")
    inputs = plan_inputs("rail")
    inputs["allowed_action_groups"] = ["prompt", "skill"]

    result = asyncio.run(MemberActionPlanner(agent).plan(**inputs))

    assert agent.calls == []
    assert result.actions == []
    assert result.metadata["capability_requests"][0]["required_surfaces"] == ["rail"]


def test_rail_diagnosis_rejects_wrong_surface_even_when_prompt_is_allowed():
    agent = RecordingPlanner("prompt")
    with pytest.raises(RuntimeError, match="does not match diagnosed optimization_surface"):
        asyncio.run(MemberActionPlanner(agent).plan(**plan_inputs("rail")))


def test_allowed_group_without_executable_definition_is_deferred():
    agent = RecordingPlanner("rail")
    inputs = plan_inputs("rail")
    inputs["action_definitions"] = [d for d in inputs["action_definitions"] if d.group != "rail"]

    result = asyncio.run(MemberActionPlanner(agent).plan(**inputs))

    assert agent.calls == []
    assert result.actions == []
    assert result.metadata["capability_requests"][0]["required_surfaces"] == ["rail"]


@pytest.mark.parametrize("rail_allowed", [True, False])
def test_selector_preserves_rail_for_planner_availability_decision(rail_allowed):
    inputs = plan_inputs("rail")
    selection = MemberSelector().select(
        inputs["role_attribution_report"],
        inputs["mechanism_attribution_report"],
    )
    inputs["targets"] = selection.targets
    assert inputs["targets"][0].optimization_surfaces == ["rail"]
    if not rail_allowed:
        inputs["allowed_action_groups"] = ["prompt", "skill"]
    agent = RecordingPlanner("rail")

    result = asyncio.run(MemberActionPlanner(agent).plan(**inputs))

    if rail_allowed:
        assert result.actions[0].action_group == "rail"
    else:
        assert agent.calls == []
        assert result.metadata["capability_requests"][0]["required_surfaces"] == ["rail"]
