# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Member action planning -DeepAgent-powered generation with outer-layer validation.

Per feat_009 rule.md Section 3.6 and design.md Section 4.8.
The planner's DeepAgent is the first phase that can output executable actions.
Outer layer validates the plan before returning MemberOptimizationPlan.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.member_optimizer.action_groups import (
    action_policy_prompt,
    build_action_waves,
    build_action_waves_from_data,
    build_waves_from_deps_only,
    filter_action_definitions,
    sanitize_allowed_tools,
    validate_action_policy,
)
from openjiuwen.rsi.member_optimizer.agents.factory import (
    create_action_planning_agent,
)
from openjiuwen.rsi.member_optimizer.agents.output import (
    invoke_member_optimizer_agent_structured,
    parse_yaml_or_json_object_response,
)
from openjiuwen.rsi.member_optimizer.agents.rails import (
    HarnessStructureRail,
)
from openjiuwen.rsi.member_optimizer.lever import (
    build_action_lever_decision,
)
from openjiuwen.rsi.member_optimizer.schema import (
    ActionDefinition,
    MechanismAttributionReport,
    MemberOptimizationAction,
    MemberOptimizationPlan,
    MemberOptimizationTarget,
    RoleAttributionReport,
    RoleMechanismAttribution,
)

_ROLE_IDENTITY_SCOPES = frozenset({"role_identity", "duty_boundary"})
_SOUL_SCOPES = frozenset({"durable_operating_principle"})
_PROMPT_CORE_PATHS = frozenset({"identity.md", "soul.md"})
_RUN_IF_VALUES = frozenset({"dependency_succeeded", "dependency_failed", "always"})
_UNSUPPORTED_OPTIMIZATION_SURFACES: frozenset[str] = frozenset()
_PLAN_VALIDATION_ATTEMPTS = 3
_MAX_ACTIONS_PER_ISSUE = 3
_MIN_NEW_SKILL_SUPPORT_CASES = 2


def _validate_action(
    action: dict[str, Any],
    selected_roles: set[str],
) -> list[str]:
    """Validate one action dict. Returns list of error strings."""
    check = validate_action_policy(action, selected_roles)
    errors = list(check.errors)
    run_if = str(action.get("run_if", "dependency_succeeded") or "dependency_succeeded")
    if run_if not in _RUN_IF_VALUES:
        errors.append(f"run_if must be one of {sorted(_RUN_IF_VALUES)}, got {run_if!r}")
    return errors


def _validate_plan(
    plan_data: dict[str, Any],
    selected_roles: set[str],
    targets: list[MemberOptimizationTarget] | None = None,
    action_definitions: list[ActionDefinition] | None = None,
) -> list[str]:
    """Validate a parsed plan dict. Returns list of error strings."""
    errors: list[str] = []
    targets_by_role = {target.role: target for target in targets or []}

    action_ids_seen: set[str] = set()
    all_action_ids: set[str] = set()
    actions_by_role: dict[str, list[dict[str, Any]]] = {}

    for action in plan_data.get("actions", []):
        action_id = action.get("action_id", "")
        all_action_ids.add(action_id)
        actions_by_role.setdefault(str(action.get("role", "") or ""), []).append(action)

        errs = _validate_action(action, selected_roles)
        errors.extend(f"action {action_id}: {e}" for e in errs)
        surface_errors = _validate_prompt_surface_selection(action, targets_by_role)
        errors.extend(f"action {action_id}: {e}" for e in surface_errors)
        alignment_errors = _validate_optimization_surface_alignment(action, targets_by_role)
        errors.extend(f"action {action_id}: {e}" for e in alignment_errors)
        skill_qualification_errors = _validate_new_skill_qualification(action, targets_by_role)
        errors.extend(f"action {action_id}: {e}" for e in skill_qualification_errors)
        attribution_errors = _validate_action_issue_attribution(action, targets_by_role)
        errors.extend(f"action {action_id}: {e}" for e in attribution_errors)
        existing_surface_errors = _validate_existing_surface_operation(action, targets_by_role)
        errors.extend(f"action {action_id}: {e}" for e in existing_surface_errors)
        definition_errors = _validate_offered_action_definition(
            action,
            action_definitions,
        )
        errors.extend(f"action {action_id}: {e}" for e in definition_errors)

        if action_id in action_ids_seen:
            errors.append(f"duplicate action_id: {action_id}")
        action_ids_seen.add(action_id)

    errors.extend(_validate_skill_search_add_fallback(plan_data))
    errors.extend(_validate_action_bundle_cohesion(plan_data))
    errors.extend(
        _validate_actionable_target_coverage(
            targets or [],
            actions_by_role,
            action_definitions or [],
        )
    )

    for wave_idx, wave in enumerate(plan_data.get("action_waves", [])):
        for action_id in wave:
            if action_id not in all_action_ids:
                errors.append(f"wave[{wave_idx}] references unknown action_id: {action_id}")

    action_ids_from_waves: set[str] = set()
    for wave in plan_data.get("action_waves", []):
        action_ids_from_waves.update(wave)
    if action_ids_from_waves != all_action_ids:
        missing = all_action_ids - action_ids_from_waves
        extra = action_ids_from_waves - all_action_ids
        if missing:
            errors.append(f"actions missing from waves: {missing}")
        if extra:
            errors.append(f"waves reference unknown action_ids: {extra}")

    return errors


def _validate_action_bundle_cohesion(plan_data: dict[str, Any]) -> list[str]:
    """Require related multi-action repairs to stay inside one issue bundle."""
    actions = [action for action in plan_data.get("actions", []) if isinstance(action, dict)]
    actions_by_id = {
        str(action.get("action_id", "") or ""): action for action in actions if str(action.get("action_id", "") or "")
    }
    bundle_by_action_id: dict[str, tuple[str, str]] = {}
    actions_by_bundle: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for action in actions:
        issue_ids = [str(issue_id) for issue_id in action.get("attributed_issue_ids", []) if str(issue_id)]
        action_id = str(action.get("action_id", "") or "")
        role = str(action.get("role", "") or "")
        if action_id and role and len(issue_ids) == 1:
            bundle = (role, issue_ids[0])
            bundle_by_action_id[action_id] = bundle
            actions_by_bundle.setdefault(bundle, []).append(action)

    errors: list[str] = []
    for bundle, bundle_actions in sorted(actions_by_bundle.items()):
        if len(bundle_actions) > _MAX_ACTIONS_PER_ISSUE:
            errors.append(
                f"role {bundle[0]} issue {bundle[1]} has {len(bundle_actions)} actions; "
                f"at most {_MAX_ACTIONS_PER_ISSUE} associated actions are allowed"
            )

    for action in actions:
        action_id = str(action.get("action_id", "") or "")
        action_bundle = bundle_by_action_id.get(action_id)
        for dependency_id in action.get("depends_on", []) or []:
            dependency_id = str(dependency_id)
            dependency = actions_by_id.get(dependency_id)
            if dependency is None:
                errors.append(f"action {action_id} depends on unknown action_id: {dependency_id}")
                continue
            dependency_bundle = bundle_by_action_id.get(dependency_id)
            if action_bundle is not None and dependency_bundle != action_bundle:
                errors.append(
                    f"action {action_id} depends on unrelated action {dependency_id}; "
                    "dependencies must stay within the same role and attributed issue"
                )

    for bundle, bundle_actions in sorted(actions_by_bundle.items()):
        if len(bundle_actions) <= 1:
            continue
        action_ids = {str(action.get("action_id", "") or "") for action in bundle_actions}
        adjacency = {action_id: set() for action_id in action_ids}
        for action in bundle_actions:
            action_id = str(action.get("action_id", "") or "")
            for dependency_id in action.get("depends_on", []) or []:
                dependency_id = str(dependency_id)
                if dependency_id not in action_ids:
                    continue
                adjacency[action_id].add(dependency_id)
                adjacency[dependency_id].add(action_id)
        visited: set[str] = set()
        pending = [next(iter(action_ids))]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency[current] - visited)
        if visited != action_ids:
            errors.append(
                f"role {bundle[0]} issue {bundle[1]} actions are not one connected "
                "dependency bundle; split unrelated changes into separate candidates"
            )
    return errors


def _validate_action_issue_attribution(
    action: dict[str, Any],
    targets_by_role: dict[str, MemberOptimizationTarget],
) -> list[str]:
    """Bind each action to only the diagnosed issues it is intended to fix."""
    role = str(action.get("role", "") or "")
    target = targets_by_role.get(role)
    if target is None:
        return []

    target_issue_ids = {str(issue_id) for issue_id in target.attributed_issue_ids if str(issue_id)}
    raw_issue_ids = action.get("attributed_issue_ids")
    if raw_issue_ids is None and len(target_issue_ids) == 1:
        action["attributed_issue_ids"] = sorted(target_issue_ids)
        raw_issue_ids = action["attributed_issue_ids"]
    if not isinstance(raw_issue_ids, list):
        return ["attributed_issue_ids must be a list"]

    action_issue_ids = {str(issue_id) for issue_id in raw_issue_ids if str(issue_id)}
    if target_issue_ids and not action_issue_ids:
        return ["attributed_issue_ids must identify the diagnosed issue(s) this action fixes"]
    unknown_issue_ids = action_issue_ids - target_issue_ids
    if unknown_issue_ids:
        return [f"attributed_issue_ids contains issues not assigned to the action role: {sorted(unknown_issue_ids)}"]
    if len(action_issue_ids) > 1:
        return [
            "each optimization action must attribute exactly one diagnosed issue; "
            "separate causal mechanisms require separate actions"
        ]
    action["attributed_issue_ids"] = sorted(action_issue_ids)
    return _validate_action_issue_text_scope(action, action_issue_ids)


def _validate_new_skill_qualification(
    action: dict[str, Any],
    targets_by_role: dict[str, MemberOptimizationTarget],
) -> list[str]:
    """Prevent one observed subtask from being materialized as a new Skill."""
    if str(action.get("action_group", "")).strip() != "skill":
        return []
    target = targets_by_role.get(str(action.get("role", "") or ""))
    if target is None:
        return []
    qualification = target.metadata.get("new_skill_qualification", {})
    if not isinstance(qualification, dict):
        return []
    if qualification.get("status") != "insufficient_cross_case_support":
        return []
    return [
        "a Skill change requires the same reusable mechanism in at least "
        f"{qualification.get('required_support_case_count', _MIN_NEW_SKILL_SUPPORT_CASES)} "
        "distinct cases; use the declared prompt_section fallback for this "
        "single-case observation"
    ]


def _validate_action_issue_text_scope(
    action: dict[str, Any],
    action_issue_ids: set[str],
) -> list[str]:
    """Reject prose that silently merges diagnoses outside the declared issue."""
    scoped_fields: dict[str, Any] = {}
    for key in (
        "description",
        "rationale",
        "expected_effect",
        "risk_notes",
        "constraints",
    ):
        scoped_fields[key] = action.get(key)
    text = yaml.safe_dump(scoped_fields, allow_unicode=True).lower()
    merged_claims = (
        "both attributed issues",
        "both diagnosed issues",
        "both issues",
        "all attributed issues",
        "all diagnosed issues",
        "multiple diagnosed issues",
        "two diagnosed issues",
    )
    if any(claim in text for claim in merged_claims):
        return [
            "action prose merges multiple diagnosed issues despite declaring exactly one; "
            "keep every action field within attributed_issue_ids"
        ]
    referenced_issue_ids = set(re.findall(r"\bissue_[a-z0-9_-]+\b", text))
    out_of_scope = referenced_issue_ids - action_issue_ids
    if out_of_scope:
        return [f"action prose references issues outside attributed_issue_ids: {sorted(out_of_scope)}"]
    return []


def _validate_offered_action_definition(
    action: dict[str, Any],
    action_definitions: list[ActionDefinition] | None,
) -> list[str]:
    """Reject model actions removed from the run-specific action contract."""
    if action_definitions is None:
        return []
    offered = {
        (str(definition.group or "").strip(), str(definition.operation or "").strip())
        for definition in filter_action_definitions(action_definitions)
    }
    selected = (
        str(action.get("action_group", "") or "").strip(),
        str(action.get("operation", "") or "").strip(),
    )
    if selected in offered:
        return []
    return [f"action is not present in the run-specific action contract: {selected[0]}/{selected[1]}"]


def _validate_existing_surface_operation(
    action: dict[str, Any],
    targets_by_role: dict[str, MemberOptimizationTarget],
) -> list[str]:
    """Reject duplicate add actions when the target surface already exists."""
    if str(action.get("operation", "")).strip().lower() != "add":
        return []
    role = str(action.get("role", "") or "")
    target = targets_by_role.get(role)
    target_path = str(action.get("target_path", "") or "").strip()
    if target is None or not target_path:
        return []
    harness_root = Path(target.harness_ref_path).expanduser()
    candidate = harness_root / target_path
    if candidate.exists():
        return [
            f"target_path already exists: {target_path}; use operation=modify instead of adding a duplicate capability"
        ]
    return []


def _validate_and_repair_plan_data(
    *,
    plan_data: dict[str, Any],
    selected_roles: set[str],
    actionable_targets: list[MemberOptimizationTarget],
    action_definitions: list[ActionDefinition],
) -> list[str]:
    """Validate a plan and repair only mechanical action wave errors."""
    errors = _validate_plan(
        plan_data,
        selected_roles,
        actionable_targets,
        action_definitions,
    )
    if not errors:
        return []

    waves = build_action_waves_from_data(plan_data.get("actions", []))
    plan_data["action_waves"] = waves
    re_errors = _validate_plan(
        plan_data,
        selected_roles,
        actionable_targets,
        action_definitions,
    )
    if not re_errors:
        return []

    waves = build_waves_from_deps_only(plan_data.get("actions", []))
    plan_data["action_waves"] = waves
    return _validate_plan(
        plan_data,
        selected_roles,
        actionable_targets,
        action_definitions,
    )


def _validate_actionable_target_coverage(
    targets: list[MemberOptimizationTarget],
    actions_by_role: dict[str, list[dict[str, Any]]],
    action_definitions: list[ActionDefinition],
) -> list[str]:
    """Ensure supported diagnosed surfaces are not silently dropped."""
    supported_surfaces = _supported_optimization_surfaces(action_definitions)
    if not supported_surfaces:
        return []

    errors: list[str] = []
    for target in targets:
        target_surfaces: set[str] = set()
        for raw_surface in target.optimization_surfaces:
            surface = _normalize_optimization_surface(raw_surface)
            if surface and surface not in _UNSUPPORTED_OPTIMIZATION_SURFACES and surface in supported_surfaces:
                target_surfaces.add(surface)
        if target_surfaces and not actions_by_role.get(target.role):
            errors.append(
                f"target {target.role} has actionable optimization_surfaces "
                f"{sorted(target_surfaces)} but no executable action"
            )
    return errors


def _supported_optimization_surfaces(
    action_definitions: list[ActionDefinition],
) -> set[str]:
    surfaces: set[str] = set()
    for definition in filter_action_definitions(action_definitions):
        group = str(definition.group or "").strip()
        if group == "prompt":
            surfaces.update({"prompt", "prompt_section", "identity", "soul"})
        elif group in {"skill", "tool", "rail"}:
            surfaces.add(group)
    return surfaces


def _normalize_optimization_surface(surface: Any) -> str:
    value = str(surface or "").strip()
    if value in {"prompt_file", "prompt_section_file"}:
        return "prompt_section"
    return value


def _validate_skill_search_add_fallback(plan_data: dict[str, Any]) -> list[str]:
    """Require explicit failure semantics for local add after skill search."""
    actions = {
        str(action.get("action_id", "") or ""): action
        for action in plan_data.get("actions", [])
        if isinstance(action, dict)
    }
    search_action_ids = {
        action_id
        for action_id, action in actions.items()
        if str(action.get("action_group", "") or "") == "skill" and str(action.get("operation", "") or "") == "search"
    }
    errors: list[str] = []
    for action_id, action in actions.items():
        if str(action.get("action_group", "") or "") != "skill" or str(action.get("operation", "") or "") != "add":
            continue
        depends_on = {str(dependency_id) for dependency_id in action.get("depends_on", [])}
        if not (depends_on & search_action_ids):
            continue
        run_if = str(action.get("run_if", "dependency_succeeded") or "dependency_succeeded")
        if run_if != "dependency_failed":
            errors.append(
                f"action {action_id}: skill/add fallback after skill/search must set run_if=dependency_failed"
            )
    return errors


def _normalize_required_declared_paths(plan_data: dict[str, Any]) -> None:
    """Complete mechanical manifest declarations implied by selected surfaces."""
    for action in plan_data.get("actions", []):
        if not isinstance(action, dict):
            continue
        target_path = _normalize_plan_path(str(action.get("target_path", "") or ""))
        action_group = str(action.get("action_group", "") or "")
        operation = str(action.get("operation", "") or "")
        declared_paths = action.get("declared_write_paths", [])
        if not isinstance(declared_paths, list):
            declared_paths = [declared_paths] if declared_paths else []

        normalized: list[str] = []
        seen: set[str] = set()
        for path in declared_paths:
            rel = _normalize_plan_path(str(path))
            if rel and rel not in seen:
                normalized.append(rel)
                seen.add(rel)

        required = _required_declared_paths_for_action(
            action_group=action_group,
            operation=operation,
            target_path=target_path,
        )
        for rel in required:
            if rel and rel not in seen:
                normalized.append(rel)
                seen.add(rel)
        action["declared_write_paths"] = normalized


def _required_declared_paths_for_action(
    *,
    action_group: str,
    operation: str,
    target_path: str,
) -> list[str]:
    if action_group == "prompt" and target_path.startswith("prompt_sections/files/"):
        return [target_path, "prompt_sections/sections.yaml"]
    if action_group == "skill" and target_path.startswith("skills/"):
        return [target_path, "skills/skills.yaml"]
    if action_group == "skill" and operation == "search":
        return ["skills", "skills/skills.yaml"]
    if action_group == "tool" and target_path.startswith("tools/") and target_path != "tools/tools.yaml":
        return [target_path, "tools/tools.yaml"]
    if action_group == "rail" and target_path.startswith("rails/") and target_path != "rails/rails.yaml":
        return [target_path, "rails/rails.yaml"]
    return []


def _validate_prompt_surface_selection(
    action: dict[str, Any],
    targets_by_role: dict[str, MemberOptimizationTarget],
) -> list[str]:
    """Enforce the prompt surface contract for core prompt files."""
    if action.get("action_group") != "prompt":
        return []
    target_path = _normalize_plan_path(str(action.get("target_path", "")))
    if target_path not in _PROMPT_CORE_PATHS:
        return []

    constraints = action.get("constraints")
    if not isinstance(constraints, dict):
        constraints = {}
    surface_scope = str(constraints.get("surface_scope", "") or "").strip()

    if target_path == "identity.md":
        if surface_scope not in _ROLE_IDENTITY_SCOPES:
            return [
                (
                    "identity.md can only be used for role identity or "
                    "duty-boundary changes; set constraints.surface_scope to "
                    "`role_identity` or `duty_boundary`, otherwise use a "
                    "prompt section"
                )
            ]
        return []

    if target_path == "soul.md":
        if surface_scope not in _SOUL_SCOPES:
            return [
                (
                    "soul.md can only be used for durable operating principles; "
                    "set constraints.surface_scope to "
                    "`durable_operating_principle`, otherwise use a prompt section"
                )
            ]
        role = str(action.get("role", "") or "")
        target = targets_by_role.get(role)
        mechanism_types = set(target.mechanism_types if target else [])
        if mechanism_types & {"workflow", "instruction"}:
            rationale = " ".join(
                str(action.get(field, "") or "") for field in ("description", "rationale", "expected_effect")
            ).lower()
            procedural_terms = (
                "checklist",
                "procedure",
                "step",
                "specific",
                "git",
                "merge",
                "rebase",
                "verification",
                "verify",
                "recover",
            )
            if any(term in rationale for term in procedural_terms):
                return [
                    (
                        "specific workflows, checklists, verification procedures, "
                        "and task recovery routines must use "
                        "prompt_sections/files/*.md plus "
                        "prompt_sections/sections.yaml"
                    )
                ]
        return []

    return []


def _validate_optimization_surface_alignment(
    action: dict[str, Any],
    targets_by_role: dict[str, MemberOptimizationTarget],
) -> list[str]:
    """Ensure the planned action lands on the diagnosed optimization surface."""
    role = str(action.get("role", "") or "")
    target = targets_by_role.get(role)
    if target is None or not target.optimization_surfaces:
        return []

    allowed_surfaces: set[str] = set()
    for raw_surface in target.optimization_surfaces:
        surface = str(raw_surface).strip()
        if surface and surface not in _UNSUPPORTED_OPTIMIZATION_SURFACES:
            allowed_surfaces.add(surface)
    if not allowed_surfaces:
        return []

    actual_surface = _action_optimization_surface(action)
    if actual_surface in allowed_surfaces:
        return []

    return [
        (
            "action surface does not match diagnosed optimization_surface: "
            f"actual={actual_surface or 'unknown'}, "
            f"expected_one_of={sorted(allowed_surfaces)}"
        )
    ]


def _action_optimization_surface(action: dict[str, Any]) -> str:
    action_group = str(action.get("action_group", "") or "")
    target_path = _normalize_plan_path(str(action.get("target_path", "") or ""))
    if action_group in {"skill", "tool", "rail"}:
        return action_group
    if action_group != "prompt":
        return ""
    if target_path == "identity.md":
        return "identity"
    if target_path == "soul.md":
        return "soul"
    if target_path.startswith("prompt_sections/files/"):
        return "prompt_section"
    return "prompt_section"


def _validate_allowed_prompt_surfaces(
    plan_data: dict[str, Any],
    allowed_prompt_surfaces: set[str],
) -> list[str]:
    if not allowed_prompt_surfaces:
        return []
    errors: list[str] = []
    for action in plan_data.get("actions", []):
        if not isinstance(action, dict) or action.get("action_group") != "prompt":
            continue
        actual_surface = _action_optimization_surface(action)
        if actual_surface not in allowed_prompt_surfaces:
            errors.append(
                "restricted prompt optimization allows only "
                f"{sorted(allowed_prompt_surfaces)}, got {actual_surface or 'unknown'} "
                f"for action {action.get('action_id', '')}"
            )
    return errors


def _normalize_plan_path(path: str) -> str:
    return Path(path.replace("\\", "/")).as_posix().strip("/")


class MemberActionPlannerAgent:
    """DeepAgent-based planner that generates MemberOptimizationPlan drafts."""

    def __init__(
        self,
        model_config_ref: str,
        stage_retry_limit: int = 2,
        workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> None:
        self._model_config_ref = model_config_ref
        self._retry_limit = stage_retry_limit
        self._workspace = Path(workspace).expanduser().resolve() if workspace else None
        self._agent_skills_dirs = list(agent_skills_dirs or [])
        self._harness_structure_rail = HarnessStructureRail()

    async def create_plan(  # pylint: disable=huawei-too-many-arguments
        self,
        targets: list[MemberOptimizationTarget],
        role_attribution_report: RoleAttributionReport,
        mechanism_attribution_report: MechanismAttributionReport,
        action_definitions: list[ActionDefinition],
        harness_summaries: dict[str, str] | None = None,
        validation_errors: list[str] | None = None,
        rejected_capabilities: list[dict[str, Any]] | None = None,
        optimization_hypotheses: list[dict[str, Any]] | None = None,
        optimization_experience: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a plan draft via DeepAgent.

        Returns the raw plan dict (not yet validated).
        Raises RuntimeError if all retries fail.
        """
        harness_summaries = harness_summaries or self._build_harness_summaries(targets)
        self._harness_structure_rail.update_harness_summaries(harness_summaries)
        agent = create_action_planning_agent(
            model_config_ref=self._model_config_ref,
            workspace=self._workspace or ".",
            action_definitions=action_definitions,
            agent_skills_dirs=self._agent_skills_dirs,
            extra_rails=[self._harness_structure_rail],
        )

        user_message = self._build_user_message(
            targets,
            role_attribution_report,
            mechanism_attribution_report,
            action_definitions,
            validation_errors=validation_errors,
            rejected_capabilities=rejected_capabilities,
            optimization_hypotheses=optimization_hypotheses,
            optimization_experience=optimization_experience,
        )

        return await invoke_member_optimizer_agent_structured(
            agent=agent,
            agent_name="MemberActionPlannerAgent",
            user_message=user_message,
            session_id=_planner_session_id(optimization_experience),
            retry_limit=self._retry_limit,
            parse_response=parse_yaml_or_json_object_response,
            build_retry_message=self._build_retry_message,
        )

    @staticmethod
    def _build_retry_message(original: Any, error: Any) -> str:
        return (
            "Return ONLY one valid JSON or YAML mapping.\n"
            "Do not include reasoning or markdown outside the final object.\n\n"
            f"Original task:\n{original}\n\n"
            f"Last error:\n{error}\n\n"
            "If the last error is only schema or formatting, keep the same "
            "chosen surface and fix only the invalid fields. If you are "
            "uncertain which harness surface should change, return an empty "
            "plan with no actions instead of guessing an existing prompt file. "
            "If the last error says a target has actionable optimization_surfaces "
            "but no executable action, keep the diagnosed surface and add one "
            "matching action for that target."
        )

    @staticmethod
    def _build_harness_summaries(
        targets: list[MemberOptimizationTarget],
    ) -> dict[str, str]:
        summaries: dict[str, str] = {}
        for target in targets:
            try:
                harness_path = Path(target.harness_ref_path).expanduser().resolve()
                summary_parts: list[str] = []
                if harness_path.is_dir():
                    harness_yaml = harness_path / "harness.yaml"
                    identity_md = harness_path / "identity.md"
                    soul_md = harness_path / "soul.md"
                    if harness_yaml.is_file():
                        summary_parts.append("files: harness.yaml")
                    if identity_md.is_file():
                        summary_parts.append("files: identity.md")
                    if soul_md.is_file():
                        summary_parts.append("files: soul.md")
                    for folder_name in ("prompt_sections", "skills", "tools", "rails"):
                        folder = harness_path / folder_name
                        if folder.exists():
                            summary_parts.append(f"surface: {folder_name}/")
                    if harness_yaml.is_file():
                        try:
                            data = yaml.safe_load(harness_yaml.read_text(encoding="utf-8")) or {}
                            tools = data.get("tools") or []
                            skills = data.get("skills") or []
                            prompt_sections = data.get("prompt_sections") or []
                            rails = data.get("rails") or []
                            summary_parts.append(
                                "config: "
                                f"tools={len(tools)}, skills={len(skills)}, "
                                f"prompt_sections={len(prompt_sections)}, "
                                f"rails={len(rails)}"
                            )
                        except Exception:
                            summary_parts.append("config: unreadable")
                summaries[target.role] = "; ".join(summary_parts) if summary_parts else "no structure summary available"
            except Exception:
                summaries[target.role] = "no structure summary available"
        return summaries

    @staticmethod
    def _build_user_message(  # pylint: disable=huawei-too-many-arguments
        targets: list[MemberOptimizationTarget],
        role_attribution_report: RoleAttributionReport,
        mechanism_attribution_report: MechanismAttributionReport,
        action_definitions: list[ActionDefinition] | None = None,
        validation_errors: list[str] | None = None,
        rejected_capabilities: list[dict[str, Any]] | None = None,
        optimization_hypotheses: list[dict[str, Any]] | None = None,
        optimization_experience: dict[str, Any] | None = None,
    ) -> str:
        targets_text = "\n".join(
            f"  - role: {t.role}, member: {t.member_name}, "
            f"harness_ref: {t.harness_ref_path}, "
            f"issue_ids: {t.attributed_issue_ids}, "
            f"confidence: {t.confidence:.2f}, "
            f"mechanism_types: {t.mechanism_types}, "
            f"optimization_surfaces: {t.optimization_surfaces}, "
            f"reason: {t.reason}"
            for t in targets
        )

        selected_roles = {target.role for target in targets}
        role_issues_text = ""
        for issue in role_attribution_report.assigned_role_issues:
            if issue.role not in selected_roles:
                continue
            role_issues_text += (
                f"\n  Role {issue.role}:"
                f"\n    issue={issue.issue_id}, confidence={issue.confidence:.2f}, "
                f"rationale={issue.rationale}"
                f"\n    evidence={_summarize_evidence_items(issue.evidence) or 'none'}"
                f"\n    trace_refs={_summarize_evidence_items(issue.trace_refs) or 'none'}"
            )

        role_mechanisms_text = ""
        for role, mechanisms in mechanism_attribution_report.role_mechanisms.items():
            if role not in selected_roles:
                continue
            role_mechanisms_text += f"\n  Role {role}:"
            for m in mechanisms:
                role_mechanisms_text += (
                    f"\n    issue={m.issue_id}, "
                    f"mechanism={m.mechanism_type}, "
                    f"signature={m.failure_signature}, "
                    f"optimization_surface={m.optimization_surface}, "
                    f"confidence={m.confidence:.2f}, "
                    f"rationale={m.rationale}"
                    f"\n    evidence={_summarize_evidence_items(m.evidence) or 'none'}"
                    f"\n    evidence_refs={_summarize_evidence_items(m.evidence_refs) or 'none'}"
                )

        action_contract = _build_action_contract_text(action_definitions or [])
        validation_feedback = ""
        if validation_errors:
            validation_lines = "\n".join(f"- {error}" for error in validation_errors)
            validation_feedback = f"""
## Previous Plan Validation Errors

The previous plan was rejected by deterministic validation:
{validation_lines}

Generate a corrected plan for the same selected roles and diagnoses. Preserve
the diagnosed optimization surfaces. For surface mismatch errors, choose the
action_group and target_path that match the diagnosed optimization_surface.
"""
        rejected_feedback = ""
        if rejected_capabilities:
            rejected_lines = "\n".join(
                f"- role={item.get('role', '')}, group={item.get('action_group', '')}, "
                f"operation={item.get('operation', '')}, runtime_name={item.get('runtime_name', '')}, "
                f"expected_effect={item.get('expected_effect', '')}, "
                f"rejection_reason={item.get('rejection_reason', '')}, "
                f"failure_class={item.get('failure_class', '')}, "
                f"target_case_ids={item.get('target_case_ids', [])}, "
                f"verifier_deltas_by_case={item.get('verifier_deltas_by_case', {})}, "
                f"candidate_failure_diagnoses={item.get('candidate_failure_diagnoses', {})}, "
                f"target_confirmation={item.get('target_confirmation', {})}, "
                f"epoch_checkpoint_outcome={item.get('epoch_checkpoint_outcome', {})}"
                for item in rejected_capabilities[-8:]
                if isinstance(item, dict)
            )
            rejected_feedback = f"""
## Rejected Capability History

{rejected_lines}

Do not add the same capability under a different file name. If the prior
capability was not invoked, repair activation/discoverability through a modify
action; do not create another tool/add candidate with equivalent intent.
If the prior candidate worked once but failed confirmation or epoch replay
because no edit was produced, do not write a synonymous Skill. Use the adapted
prompt_section surface to create a bounded decision-to-edit checkpoint. If an
edit was produced but the official semantic observable still failed, keep the
evidence-backed surface and repair the causal discriminator instead.
"""

        hypothesis_contract = ""
        if optimization_hypotheses:
            compact_hypotheses = [
                {
                    "hypothesis_id": item.get("hypothesis_id", ""),
                    "content_sha256": item.get("content_sha256", ""),
                    "source_issue_id": item.get("source_issue_id", ""),
                    "target_case_ids": item.get("target_case_ids", []),
                    "summary": (item.get("authoritative_observations", {}) or {}).get("summary", ""),
                    "required_behavior": item.get("required_behavior", ""),
                    "forbidden_behavior": item.get("forbidden_behavior", []),
                    "public_trigger": item.get("public_trigger", []),
                    "decisive_probe": item.get("decisive_probe", {}),
                    "decision_contract": item.get("decision_contract", {}),
                    "supported_causal_hypothesis_ids": item.get("supported_causal_hypothesis_ids", []),
                    "falsified_causal_hypothesis_ids": item.get("falsified_causal_hypothesis_ids", []),
                }
                for item in optimization_hypotheses
                if isinstance(item, dict)
            ]
            hypothesis_contract = f"""
## Immutable Optimization Hypotheses

{json.dumps(compact_hypotheses, ensure_ascii=False, indent=2)}

These analyzer-authored contracts are semantic source of truth. Select exactly
one allowed runtime surface, but do not weaken, reverse, or paraphrase away its
required_behavior or decision_contract. Attribute each action only to the
source_issue_id it fixes. The action must teach the selected required_action;
do not turn it back into a menu containing the recorded wrong_decision.
Every action is controller-bound to supported_causal_hypothesis_ids. Never use,
rename, or combine a hypothesis listed in falsified_causal_hypothesis_ids.
"""
        experience_context = ""
        if optimization_experience:
            scoreboard = optimization_experience.get("lever_scoreboard", {})
            journal = optimization_experience.get("journal", [])
            recent = (
                [_compact_experiment_for_planner(item) for item in journal[-8:] if isinstance(item, dict)]
                if isinstance(journal, list)
                else []
            )
            if scoreboard or recent:
                experience_context = f"""
## Optimization Experience

Lever scoreboard:
{json.dumps(scoreboard, ensure_ascii=False, indent=2)}

Recent experiments:
{json.dumps(recent, ensure_ascii=False, indent=2)}

Use outcomes only to choose among surfaces inside the diagnosed lever. Do not
rename and retry an equivalent rejected capability. A different lever requires
a new analyzer diagnosis; never compensate for an unavailable Control or
Configuration lever with a Skill or Prompt.
When verifier_deltas_by_case shows newly passing FAIL_TO_PASS operations, keep
that behavior and narrow the next action to remaining_failed_fail_to_pass.
Do not describe a binary 0-to-0 case score as "no effect" when the official
per-test delta records partial contract progress.
"""
        sibling_generation_context = _build_sibling_generation_context(
            optimization_experience,
        )
        improver_policy_context = _build_improver_policy_context(
            optimization_experience,
        )

        return f"""## Current Action Contract

{action_contract}

## Selected Optimization Targets

{targets_text}

## Attributed Role Issues

{role_issues_text}

## Mechanism Attribution Summary

{role_mechanisms_text}
{hypothesis_contract}
{experience_context}
{improver_policy_context}
{sibling_generation_context}
{validation_feedback}
{rejected_feedback}

## Output

Return ONLY a JSON plan object as specified in the system prompt.
"""


def _planner_session_id(optimization_experience: dict[str, Any] | None) -> str:
    sibling_generation = _sibling_generation(optimization_experience)
    candidate_id = str(sibling_generation.get("candidate_id", "") or "").strip()
    if not candidate_id:
        return "member_action_planner"
    safe_candidate_id = re.sub(r"[^A-Za-z0-9_-]+", "_", candidate_id).strip("_")
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:10]
    return f"member_action_planner_{safe_candidate_id[:32] or 'candidate'}_{digest}"


def _build_improver_policy_context(
    optimization_experience: dict[str, Any] | None,
) -> str:
    if not isinstance(optimization_experience, dict):
        return ""
    raw_policy = optimization_experience.get("improver_policy")
    if not isinstance(raw_policy, dict) or not str(raw_policy.get("version_id", "") or ""):
        return ""
    policy = {
        "version_id": str(raw_policy.get("version_id", "") or ""),
        "policy_digest": str(raw_policy.get("policy_digest", "") or ""),
        "generation_directives": (
            dict(raw_policy.get("generation_directives", {}))
            if isinstance(raw_policy.get("generation_directives"), dict)
            else {}
        ),
        "budget_policy": (
            dict(raw_policy.get("budget_policy", {})) if isinstance(raw_policy.get("budget_policy"), dict) else {}
        ),
    }
    return f"""
## Frozen Improver Policy

{json.dumps(policy, ensure_ascii=False, indent=2)}

This is the versioned pre-execution policy for the current Improver. It is not
sibling execution feedback and contains no result for the current cohort.
Apply its generation directives only within the immutable hypothesis, diagnosed
lever, run-specific action contract, and available Harness surfaces. A diversity
directive never permits an unsupported cross-lever change. An activation-evidence
directive requires concrete current execution or Harness-path evidence before
choosing that surface. Do not invent evidence merely to satisfy the policy.
"""


def _build_sibling_generation_context(
    optimization_experience: dict[str, Any] | None,
) -> str:
    sibling_generation = _sibling_generation(optimization_experience)
    candidate_id = str(sibling_generation.get("candidate_id", "") or "").strip()
    if not candidate_id:
        return ""
    prior_proposals = sibling_generation.get("prior_proposals", [])
    if not isinstance(prior_proposals, list):
        prior_proposals = []
    compact_proposals = [_compact_sibling_proposal(item) for item in prior_proposals if isinstance(item, dict)]
    compact_proposals = [item for item in compact_proposals if item]
    generation_position = {
        "candidate_id": candidate_id,
        "generation_index": sibling_generation.get(
            "generation_index",
            sibling_generation.get("candidate_index", ""),
        ),
        "candidate_count": sibling_generation.get("candidate_count", ""),
    }
    return f"""
## Sibling Candidate Generation (Pre-Execution Plans Only)

Current candidate position:
{json.dumps(generation_position, ensure_ascii=False, indent=2)}

Prior sibling proposal summaries:
{json.dumps(compact_proposals, ensure_ascii=False, indent=2)}

This section contains static plans produced before candidate execution. It is
not execution feedback, rejection history, verifier evidence, a score outcome,
or evidence that any sibling works. `generation_index` is only a generation
slot; it is not a quality prediction. Predicted ranks are computed and frozen
only after every sibling proposal exists.

Generate the proposal assigned to the current generation slot. Make it
materially different from the prior proposal summaries in its intervention,
not merely in action IDs, wording, or file names. Stay inside the immutable
hypothesis and its diagnosed lever. Never cross levers just to manufacture
diversity. If no evidence-backed materially distinct proposal exists, keep the
evidence contract even if the resulting proposal is a duplicate; the cohort
ranker will identify that duplicate without pretending it is a new strategy.
"""


def _sibling_generation(
    optimization_experience: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(optimization_experience, dict):
        return {}
    sibling_generation = optimization_experience.get("sibling_generation")
    return dict(sibling_generation) if isinstance(sibling_generation, dict) else {}


def _compact_sibling_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    static_keys = (
        "candidate_id",
        "index",
        "candidate_index",
        "generation_order",
        "plan_id",
        "summary",
        "proposal_summary",
        "intervention_summary",
        "intervention_strategy",
        "diversity_signature",
        "candidate_fingerprint",
        "role",
        "action_group",
        "operation",
        "action_type",
        "target_path",
        "runtime_name",
        "description",
        "rationale",
        "expected_effect",
        "selected_lever",
        "selected_surface",
        "action_groups",
        "operations",
        "target_paths",
        "hypothesis_ids",
        "source_issue_ids",
        "target_case_ids",
        "action_count",
    )
    compact = {key: proposal[key] for key in static_keys if key in proposal and proposal[key] not in (None, "", [], {})}
    raw_actions = proposal.get("actions", [])
    if isinstance(raw_actions, list):
        actions = [_compact_sibling_action(action) for action in raw_actions if isinstance(action, dict)]
        actions = [action for action in actions if action]
        if actions:
            compact["actions"] = actions
    raw_capabilities = proposal.get("capabilities", [])
    if isinstance(raw_capabilities, list):
        capabilities = [
            _compact_sibling_action(capability) for capability in raw_capabilities if isinstance(capability, dict)
        ]
        capabilities = [capability for capability in capabilities if capability]
        if capabilities:
            compact["capabilities"] = capabilities
    return compact


def _compact_sibling_action(action: dict[str, Any]) -> dict[str, Any]:
    static_keys = (
        "role",
        "action_group",
        "operation",
        "action_type",
        "target_path",
        "runtime_name",
        "description",
        "rationale",
        "expected_effect",
        "attributed_issue_ids",
        "declared_write_paths",
        "optimization_hypothesis_ids",
        "target_case_ids",
    )
    compact = {key: action[key] for key in static_keys if key in action and action[key] not in (None, "", [], {})}
    lever_decision = action.get("lever_decision")
    if not isinstance(lever_decision, dict):
        constraints = action.get("constraints")
        if isinstance(constraints, dict):
            lever_decision = constraints.get("lever_decision")
    if isinstance(lever_decision, dict):
        compact_lever: dict[str, Any] = {}
        for key in (
            "selected_lever",
            "selected_surface",
            "recommended_levers",
            "predicted_affected_case_ids",
        ):
            if key in lever_decision and lever_decision[key] not in (None, "", [], {}):
                compact_lever[key] = lever_decision[key]
        if compact_lever:
            compact["lever_decision"] = compact_lever
    return compact


def _summarize_evidence_items(items: list[dict[str, Any]], *, limit: int = 4) -> str:
    parts: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        fields: list[str] = []
        for key in ("summary", "recommendation", "candidate_query", "failure_mode"):
            value = item.get(key)
            if value not in (None, ""):
                fields.append(f"{key}={value}")
        if not fields:
            fields.append(", ".join(f"{key}={value}" for key, value in item.items() if key and value not in (None, "")))
        summary = "; ".join(field for field in fields if field)
        if summary:
            parts.append(summary)
    return " | ".join(parts)


def _bind_immutable_hypotheses(
    plan_data: dict[str, Any],
    hypotheses: list[dict[str, Any]],
) -> None:
    """Attach compact source contracts after plan validation without LLM rewriting."""
    if not hypotheses:
        return
    by_issue = {
        str(item.get("source_issue_id", "")): item
        for item in hypotheses
        if isinstance(item, dict) and str(item.get("source_issue_id", ""))
    }
    selected_hypothesis_ids: list[str] = []
    for action in plan_data.get("actions", []):
        if not isinstance(action, dict):
            continue
        selected = [
            by_issue[str(issue_id)] for issue_id in action.get("attributed_issue_ids", []) if str(issue_id) in by_issue
        ]
        if not selected:
            continue
        contracts = []
        for item in selected:
            contract = {
                "hypothesis_id": item.get("hypothesis_id", ""),
                "content_sha256": item.get("content_sha256", ""),
                "source_issue_id": item.get("source_issue_id", ""),
                "required_behavior": item.get("required_behavior", ""),
                "forbidden_behavior": item.get("forbidden_behavior", []),
                "public_trigger": item.get("public_trigger", []),
                "decisive_probe": item.get("decisive_probe", {}),
            }
            if item.get("supported_causal_hypothesis_ids"):
                contract["supported_causal_hypothesis_ids"] = item["supported_causal_hypothesis_ids"]
            if item.get("supported_causal_hypothesis_semantic_ids"):
                contract["supported_causal_hypothesis_semantic_ids"] = item["supported_causal_hypothesis_semantic_ids"]
            if item.get("falsified_causal_hypothesis_ids"):
                contract["falsified_causal_hypothesis_ids"] = item["falsified_causal_hypothesis_ids"]
            if item.get("falsified_causal_hypothesis_semantic_ids"):
                contract["falsified_causal_hypothesis_semantic_ids"] = item["falsified_causal_hypothesis_semantic_ids"]
            if isinstance(item.get("decision_contract"), dict) and item.get("decision_contract"):
                contract["decision_contract"] = item["decision_contract"]
            if isinstance(item.get("lever_policy"), dict) and item.get("lever_policy"):
                contract.update(
                    {
                        "diagnostic_lens": item.get("diagnostic_lens", "failure"),
                        "intent": item.get("intent", "corrective"),
                        "lever_policy": item["lever_policy"],
                    }
                )
            contracts.append(contract)
        constraints = dict(action.get("constraints") or {})
        constraints["optimization_contracts"] = contracts
        supported_causal_ids: list[str] = []
        falsified_causal_ids: set[str] = set()
        for item in selected:
            for hypothesis_id in item.get("supported_causal_hypothesis_ids", []):
                normalized = str(hypothesis_id)
                if normalized and normalized not in supported_causal_ids:
                    supported_causal_ids.append(normalized)
            for hypothesis_id in item.get("falsified_causal_hypothesis_ids", []):
                normalized = str(hypothesis_id)
                if normalized:
                    falsified_causal_ids.add(normalized)
        if set(supported_causal_ids) & falsified_causal_ids:
            raise RuntimeError("optimization hypothesis marks one causal hypothesis both supported and falsified")
        if any(item.get("hypothesis_assessment") for item in selected) and not supported_causal_ids:
            raise RuntimeError("optimization hypothesis has no supported causal hypothesis")
        if supported_causal_ids:
            constraints["source_causal_hypothesis_ids"] = supported_causal_ids
        supported_causal_semantic_ids: list[str] = []
        for item in selected:
            for semantic_id in item.get("supported_causal_hypothesis_semantic_ids", []):
                normalized = str(semantic_id)
                if normalized and normalized not in supported_causal_semantic_ids:
                    supported_causal_semantic_ids.append(normalized)
        if supported_causal_semantic_ids:
            constraints["source_causal_hypothesis_semantic_ids"] = supported_causal_semantic_ids
        policies = [
            dict(item.get("lever_policy", {}))
            for item in selected
            if isinstance(item.get("lever_policy"), dict) and item.get("lever_policy")
        ]
        lever_decision = build_action_lever_decision(
            action_group=str(action.get("action_group", "") or ""),
            selected_surface=_action_optimization_surface(action),
            policies=policies,
        )
        if lever_decision["recommended_levers"] and not lever_decision["lever_matches_diagnosis"]:
            raise RuntimeError(
                "planned action crosses the diagnosed optimization lever: "
                f"action={action.get('action_id', '')}, "
                f"selected={lever_decision['selected_lever']}, "
                f"recommended={lever_decision['recommended_levers']}"
            )
        if policies:
            constraints["lever_decision"] = lever_decision
        action["constraints"] = constraints
        action["expected_effect"] = "\n".join(
            str(item.get("required_behavior", "") or "")
            for item in selected
            if str(item.get("required_behavior", "") or "")
        )
        selected_hypothesis_ids.extend(
            str(item.get("hypothesis_id", "") or "") for item in selected if str(item.get("hypothesis_id", "") or "")
        )
    metadata = plan_data.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata["optimization_hypothesis_ids"] = list(dict.fromkeys(selected_hypothesis_ids))
        metadata["semantic_authority"] = "immutable_optimization_hypotheses"


def _annotate_action_bundles(
    plan_data: dict[str, Any],
    hypotheses: list[dict[str, Any]],
) -> None:
    """Persist the issue and required behavior shared by each atomic bundle."""
    required_behavior_by_issue = {
        str(item.get("source_issue_id", "")): str(item.get("required_behavior", "") or "")
        for item in hypotheses
        if isinstance(item, dict) and str(item.get("source_issue_id", ""))
    }
    grouped: dict[tuple[str, str], list[str]] = {}
    for action in plan_data.get("actions", []):
        if not isinstance(action, dict):
            continue
        issue_ids = [str(issue_id) for issue_id in action.get("attributed_issue_ids", []) if str(issue_id)]
        if len(issue_ids) != 1:
            continue
        key = (str(action.get("role", "") or ""), issue_ids[0])
        grouped.setdefault(key, []).append(str(action.get("action_id", "") or ""))

    metadata = plan_data.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        return
    metadata["action_bundles"] = [
        {
            "bundle_id": f"{role}:{issue_id}",
            "role": role,
            "issue_id": issue_id,
            "required_behavior": required_behavior_by_issue.get(issue_id, ""),
            "action_ids": action_ids,
            "atomic": True,
        }
        for (role, issue_id), action_ids in sorted(grouped.items())
    ]


def _build_action_contract_text(action_definitions: list[ActionDefinition]) -> str:
    """Render request-specific action policy outside the system prompt."""
    parts = [action_policy_prompt().strip()]
    filtered_definitions = filter_action_definitions(action_definitions)
    strict_lines = [
        "## Run-Specific Strict Allowlist",
        "Only the exact action_group/operation pairs listed under Action Definitions are allowed.",
        "Globally documented actions that are absent from Action Definitions are disabled for this run.",
    ]
    parts.append("\n".join(strict_lines))
    if filtered_definitions:
        lines = ["## Action Definitions"]
        for definition in filtered_definitions:
            lines.append(
                f"- {definition.group}/{definition.operation}: {definition.purpose} (function={definition.function})"
            )
        parts.append("\n".join(lines))
    else:
        parts.append("## Action Definitions\n(none)")
    return "\n\n".join(part for part in parts if part)


def _attach_improvement_briefs(
    *,
    plan_data: dict[str, Any],
    targets: list[MemberOptimizationTarget],
    role_attribution_report: RoleAttributionReport,
) -> None:
    """Attach analyzer improvement briefs to planned prompt actions."""
    brief_by_issue: dict[str, dict[str, Any]] = {}
    for issue in role_attribution_report.assigned_role_issues:
        for evidence in issue.evidence:
            if not isinstance(evidence, dict):
                continue
            brief = evidence.get("improvement_brief")
            if isinstance(brief, dict) and brief:
                brief_by_issue[issue.issue_id] = dict(brief)
                break

    if not brief_by_issue:
        return

    issue_ids_by_role: dict[str, set[str]] = {target.role: set(target.attributed_issue_ids) for target in targets}

    for action in plan_data.get("actions", []):
        if not isinstance(action, dict):
            continue
        if action.get("action_group") != "prompt":
            continue
        constraints = action.get("constraints")
        if isinstance(constraints, dict) and constraints:
            continue

        role = str(action.get("role", ""))
        action_issue_ids = {str(issue_id) for issue_id in action.get("attributed_issue_ids", []) if str(issue_id)}
        candidate_issue_ids = action_issue_ids or issue_ids_by_role.get(role, set())
        for issue_id in candidate_issue_ids:
            brief = brief_by_issue.get(issue_id)
            if brief:
                action["constraints"] = dict(brief)
                break


_INACTIONABLE_MECHANISM = "insufficient_role_evidence"


def _filter_actionable_planning_inputs(
    *,
    targets: list[MemberOptimizationTarget],
    role_attribution_report: RoleAttributionReport,
    mechanism_attribution_report: MechanismAttributionReport,
) -> tuple[
    list[MemberOptimizationTarget],
    RoleAttributionReport,
    MechanismAttributionReport,
    list[str],
]:
    """Remove issues whose mechanism attribution explicitly says evidence is insufficient."""
    targets = [_without_unsupported_surfaces(target) for target in targets]
    inactionable_issue_ids: set[str] = set()
    for mechanisms in mechanism_attribution_report.role_mechanisms.values():
        by_issue: dict[str, list[RoleMechanismAttribution]] = {}
        for mechanism in mechanisms:
            by_issue.setdefault(mechanism.issue_id, []).append(mechanism)
        for issue_id, issue_mechanisms in by_issue.items():
            if issue_mechanisms and all(_is_inactionable_mechanism(m) for m in issue_mechanisms):
                inactionable_issue_ids.add(issue_id)

    if not inactionable_issue_ids:
        return targets, role_attribution_report, mechanism_attribution_report, []

    actionable_targets: list[MemberOptimizationTarget] = []
    actionable_roles: set[str] = set()
    actionable_surfaces_by_role: dict[str, list[str]] = {}
    for role, mechanisms in mechanism_attribution_report.role_mechanisms.items():
        surfaces: list[str] = []
        seen: set[str] = set()
        for mechanism in mechanisms:
            if mechanism.issue_id in inactionable_issue_ids:
                continue
            surface = str(getattr(mechanism, "optimization_surface", "") or "").strip()
            if surface and surface not in _UNSUPPORTED_OPTIMIZATION_SURFACES and surface not in seen:
                surfaces.append(surface)
                seen.add(surface)
        if surfaces:
            actionable_surfaces_by_role[role] = surfaces

    for target in targets:
        issue_ids = [issue_id for issue_id in target.attributed_issue_ids if issue_id not in inactionable_issue_ids]
        if target.attributed_issue_ids and not issue_ids:
            continue
        mechanism_types = [
            mechanism_type for mechanism_type in target.mechanism_types if mechanism_type != _INACTIONABLE_MECHANISM
        ]
        actionable_targets.append(
            replace(
                target,
                attributed_issue_ids=issue_ids,
                mechanism_types=mechanism_types,
                optimization_surfaces=actionable_surfaces_by_role.get(
                    target.role,
                    target.optimization_surfaces,
                ),
            )
        )
        actionable_roles.add(target.role)

    actionable_issues = []
    for issue in role_attribution_report.assigned_role_issues:
        if issue.role in actionable_roles and issue.issue_id not in inactionable_issue_ids:
            actionable_issues.append(issue)
    actionable_role_report = replace(
        role_attribution_report,
        assigned_role_issues=actionable_issues,
    )

    actionable_mechanisms: dict[str, list[RoleMechanismAttribution]] = {}
    for role, mechanisms in mechanism_attribution_report.role_mechanisms.items():
        if role not in actionable_roles:
            continue
        kept = []
        for mechanism in mechanisms:
            if mechanism.issue_id not in inactionable_issue_ids and not _is_inactionable_mechanism(mechanism):
                kept.append(mechanism)
        if kept:
            actionable_mechanisms[role] = kept

    actionable_mechanism_report = replace(
        mechanism_attribution_report,
        role_mechanisms=actionable_mechanisms,
    )
    return (
        actionable_targets,
        actionable_role_report,
        actionable_mechanism_report,
        sorted(inactionable_issue_ids),
    )


def _is_inactionable_mechanism(mechanism: RoleMechanismAttribution) -> bool:
    return mechanism.mechanism_type == _INACTIONABLE_MECHANISM or mechanism.failure_signature == _INACTIONABLE_MECHANISM


def _without_unsupported_surfaces(
    target: MemberOptimizationTarget,
) -> MemberOptimizationTarget:
    surfaces = [
        surface
        for surface in target.optimization_surfaces
        if str(surface).strip() not in _UNSUPPORTED_OPTIMIZATION_SURFACES
    ]
    if surfaces == target.optimization_surfaces:
        return target
    return replace(target, optimization_surfaces=surfaces)


def _case_ids_from_value(value: Any) -> set[str]:
    """Extract explicit case identifiers without interpreting free-form prose."""
    case_ids: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"case_id", "source_case_id"} and str(nested).strip():
                case_ids.add(str(nested).strip())
            else:
                case_ids.update(_case_ids_from_value(nested))
    elif isinstance(value, list):
        for nested in value:
            case_ids.update(_case_ids_from_value(nested))
    return case_ids


def _compact_experiment_for_planner(record: dict[str, Any]) -> dict[str, Any]:
    """Keep causal candidate feedback while excluding bulky artifact payloads."""
    compact: dict[str, Any] = {}
    for key in (
        "experiment_id",
        "surface",
        "lever",
        "target_case_ids",
        "status",
        "reason",
        "failure_class",
        "outcome",
        "verifier_deltas_by_case",
        "candidate_failure_diagnoses",
        "epoch_checkpoint",
    ):
        if key in record:
            compact[key] = record.get(key)
    return compact


def _adapt_surface_for_activation_phase(
    *,
    targets: list[MemberOptimizationTarget],
    mechanism_report: MechanismAttributionReport,
    optimization_hypotheses: list[dict[str, Any]],
) -> tuple[
    list[MemberOptimizationTarget],
    MechanismAttributionReport,
    list[dict[str, Any]],
]:
    """Move post-diagnosis Instructions to Control without inferring reuse.

    Activation phase answers *when* behavior is needed. It does not establish
    that a method is reusable enough to become a Skill. Investigation-time
    Prompt guidance therefore remains a Prompt; Skill qualification is handled
    independently from cross-case evidence.
    """
    instruction_surfaces = {
        "identity",
        "soul",
        "prompt",
        "prompt_section",
        "skill",
    }
    control_phases = {"post_diagnosis", "pre_submission"}
    phase_by_issue: dict[str, str] = {}
    for item in optimization_hypotheses:
        if not isinstance(item, dict):
            continue
        decision_contract = item.get("decision_contract", {})
        if not isinstance(decision_contract, dict):
            continue
        phase_by_issue[str(item.get("source_issue_id", ""))] = str(
            decision_contract.get("activation_phase", "task_start")
        )
    adapted_surface_by_issue: dict[str, str] = {}
    adaptations: list[dict[str, Any]] = []
    adapted_targets: list[MemberOptimizationTarget] = []
    for target in targets:
        issue_ids = set(target.attributed_issue_ids)
        phases = {phase_by_issue.get(issue_id, "task_start") for issue_id in issue_ids}
        source_surfaces = set(target.optimization_surfaces)
        adapted_surface = ""
        reason = ""
        if phases and phases <= control_phases and source_surfaces & instruction_surfaces:
            adapted_surface = "control"
            reason = "required_action_is_not_knowable_at_task_start_and_must_not_be_recast_as_static_instruction"
        if not adapted_surface:
            adapted_targets.append(target)
            continue
        metadata = dict(target.metadata)
        adaptation = {
            "source_surfaces": sorted(source_surfaces & instruction_surfaces),
            "adapted_surface": adapted_surface,
            "activation_phases": sorted(phases),
            "reason": reason,
        }
        metadata["activation_phase_surface_adaptation"] = adaptation
        adapted_surfaces = [surface for surface in target.optimization_surfaces if surface not in instruction_surfaces]
        if adapted_surface not in adapted_surfaces:
            adapted_surfaces.append(adapted_surface)
        adapted_targets.append(
            replace(
                target,
                optimization_surfaces=adapted_surfaces,
                metadata=metadata,
            )
        )
        adapted_surface_by_issue.update({issue_id: adapted_surface for issue_id in issue_ids})
        adaptations.append(
            {
                "role": target.role,
                "issue_ids": sorted(issue_ids),
                **adaptation,
            }
        )

    if not adaptations:
        return targets, mechanism_report, []
    role_mechanisms = {
        role: [
            replace(
                mechanism,
                optimization_surface=adapted_surface_by_issue[mechanism.issue_id],
                rationale=(
                    f"{mechanism.rationale} The required action becomes knowable "
                    "at the activation phase recorded by the decision contract; "
                    "place it on the routed runtime surface for that phase rather "
                    "than a global static Prompt."
                ).strip(),
            )
            if mechanism.issue_id in adapted_surface_by_issue
            else mechanism
            for mechanism in mechanisms
        ]
        for role, mechanisms in mechanism_report.role_mechanisms.items()
    }
    metadata = dict(mechanism_report.metadata)
    metadata["activation_phase_surface_adaptations"] = adaptations
    return (
        adapted_targets,
        replace(
            mechanism_report,
            role_mechanisms=role_mechanisms,
            metadata=metadata,
        ),
        adaptations,
    )


def _adapt_surface_for_new_skill_qualification(
    *,
    targets: list[MemberOptimizationTarget],
    mechanism_report: MechanismAttributionReport,
    optimization_hypotheses: list[dict[str, Any]],
) -> tuple[
    list[MemberOptimizationTarget],
    MechanismAttributionReport,
    list[dict[str, Any]],
]:
    """Expose a Prompt fallback when a proposed new Skill has one-case support.

    The mechanism presented to the planner defaults to a prompt section and
    every Skill action is rejected for that target. Thus one observed verifier
    subitem cannot create or contaminate a benchmark-specific runtime
    capability.
    """
    support_by_issue: dict[str, set[str]] = {}
    for hypothesis in optimization_hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        issue_id = str(hypothesis.get("source_issue_id", "") or "").strip()
        if not issue_id:
            continue
        raw_case_ids = hypothesis.get("target_case_ids", [])
        if not isinstance(raw_case_ids, list):
            continue
        support_by_issue.setdefault(issue_id, set()).update(
            str(case_id).strip() for case_id in raw_case_ids if str(case_id).strip()
        )

    adaptations: list[dict[str, Any]] = []
    adapted_targets: list[MemberOptimizationTarget] = []
    for target in targets:
        surfaces = [str(surface).strip() for surface in target.optimization_surfaces if str(surface).strip()]
        if "skill" not in surfaces:
            adapted_targets.append(target)
            continue
        issue_ids = {str(issue_id).strip() for issue_id in target.attributed_issue_ids if str(issue_id).strip()}
        support_case_id_set: set[str] = set()
        for issue_id in issue_ids:
            support_case_id_set.update(support_by_issue.get(issue_id, set()))
        support_case_ids = sorted(support_case_id_set)
        if len(support_case_ids) != 1:
            adapted_targets.append(target)
            continue

        qualification = {
            "status": "insufficient_cross_case_support",
            "support_case_ids": support_case_ids,
            "support_case_count": len(support_case_ids),
            "required_support_case_count": _MIN_NEW_SKILL_SUPPORT_CASES,
            "fallback_surface": "prompt_section",
            "reason": "one_observed_subtask_does_not_establish_a_reusable_skill",
        }
        metadata = dict(target.metadata)
        metadata["new_skill_qualification"] = qualification
        adapted_surfaces = [surface for surface in surfaces if surface != "skill"]
        if "prompt_section" not in adapted_surfaces:
            adapted_surfaces.append("prompt_section")
        adapted_targets.append(
            replace(
                target,
                optimization_surfaces=adapted_surfaces,
                metadata=metadata,
            )
        )
        adaptations.append(
            {
                "role": target.role,
                "issue_ids": sorted(issue_ids),
                **qualification,
            }
        )
    if not adaptations:
        return targets, mechanism_report, []
    adapted_issue_ids: set[str] = set()
    for adaptation in adaptations:
        adapted_issue_ids.update(adaptation["issue_ids"])
    role_mechanisms = {
        role: [
            replace(
                mechanism,
                optimization_surface="prompt_section",
                rationale=(
                    f"{mechanism.rationale} A single observed case does not establish "
                    "cross-case Skill reuse; test the instruction as a bounded prompt "
                    "section until another independent case supports the mechanism."
                ).strip(),
            )
            if mechanism.issue_id in adapted_issue_ids and mechanism.optimization_surface == "skill"
            else mechanism
            for mechanism in mechanisms
        ]
        for role, mechanisms in mechanism_report.role_mechanisms.items()
    }
    metadata = dict(mechanism_report.metadata)
    metadata["new_skill_qualification_adaptations"] = adaptations
    return (
        adapted_targets,
        replace(
            mechanism_report,
            role_mechanisms=role_mechanisms,
            metadata=metadata,
        ),
        adaptations,
    )


def _adapt_recovery_surface_from_history(
    *,
    targets: list[MemberOptimizationTarget],
    role_report: RoleAttributionReport,
    mechanism_report: MechanismAttributionReport,
    rejected_capabilities: list[dict[str, Any]],
) -> tuple[
    list[MemberOptimizationTarget],
    MechanismAttributionReport,
    list[dict[str, Any]],
]:
    """Move a delivered-but-unapplied Skill to runtime transition control."""
    if not rejected_capabilities:
        return targets, mechanism_report, []
    issue_case_ids: dict[str, set[str]] = {}
    for issue in role_report.assigned_role_issues:
        issue_case_ids[issue.issue_id] = _case_ids_from_value(
            [issue.evidence, issue.trace_refs, issue.role_output_refs]
        )
    adaptations: list[dict[str, Any]] = []
    adapted_issue_ids: set[str] = set()
    adapted_targets: list[MemberOptimizationTarget] = []
    for target in targets:
        current_case_ids: set[str] = set()
        for issue_id in target.attributed_issue_ids:
            current_case_ids.update(issue_case_ids.get(issue_id, set()))
        matching = None
        for item in reversed(rejected_capabilities):
            if not isinstance(item, dict):
                continue
            target_case_ids = {str(case_id) for case_id in item.get("target_case_ids", []) if str(case_id)}
            same_role = str(item.get("role", "")) == target.role
            is_skill = str(item.get("action_group", "")) == "skill"
            recoverable_failure = str(item.get("failure_class", "")) in {
                "late_skill_activation",
                "execution_convergence_failure",
            }
            matches_failed_skill = same_role and is_skill and recoverable_failure
            targets_overlap = bool(current_case_ids & target_case_ids)
            if matches_failed_skill and targets_overlap:
                matching = item
                break
        if matching is None:
            adapted_targets.append(target)
            continue
        metadata = dict(target.metadata)
        metadata["recovery_surface_adaptation"] = {
            "source_surface": "skill",
            "adapted_surface": "control",
            "failure_class": str(matching.get("failure_class", "")),
            "target_case_ids": sorted(current_case_ids),
            "reason": (
                "skill_knowledge_was_available_but_not_applied_in_time; "
                "replace_method_repetition_with_a_bounded_execution_checkpoint"
            ),
        }
        adapted_targets.append(
            replace(
                target,
                optimization_surfaces=["control"],
                metadata=metadata,
            )
        )
        adapted_issue_ids.update(target.attributed_issue_ids)
        adaptations.append(
            {
                "role": target.role,
                "issue_ids": list(target.attributed_issue_ids),
                **metadata["recovery_surface_adaptation"],
            }
        )

    if not adaptations:
        return targets, mechanism_report, []
    role_mechanisms: dict[str, list[RoleMechanismAttribution]] = {}
    for role, mechanisms in mechanism_report.role_mechanisms.items():
        role_mechanisms[role] = [
            replace(
                mechanism,
                optimization_surface="control",
                rationale=(
                    f"{mechanism.rationale} A prior Skill candidate reached the "
                    "target but failed through late activation or failure to converge "
                    "on an edit; use runtime transition control that resumes the "
                    "established action, rather than adding another static instruction."
                ).strip(),
            )
            if mechanism.issue_id in adapted_issue_ids
            else mechanism
            for mechanism in mechanisms
        ]
    metadata = dict(mechanism_report.metadata)
    metadata["recovery_surface_adaptations"] = adaptations
    return (
        adapted_targets,
        replace(
            mechanism_report,
            role_mechanisms=role_mechanisms,
            metadata=metadata,
        ),
        adaptations,
    )


def _adapt_prompt_surface_within_instruction_lever(
    *,
    targets: list[MemberOptimizationTarget],
    mechanism_report: MechanismAttributionReport,
    allowed_prompt_surfaces: set[str],
) -> tuple[list[MemberOptimizationTarget], MechanismAttributionReport]:
    """Narrow a prompt target without changing its diagnosed lever.

    Identity, soul, and prompt sections are delivery surfaces for the same
    Instruction lever. A restricted run may safely choose among them, unlike
    recasting a Control, Configuration, or Action defect as instructions.
    """
    if not allowed_prompt_surfaces:
        return targets, mechanism_report
    prompt_surfaces = {"identity", "soul", "prompt_section"}
    preferred_surface = next(
        (surface for surface in ("prompt_section", "identity", "soul") if surface in allowed_prompt_surfaces),
        "",
    )
    if not preferred_surface:
        return targets, mechanism_report

    adapted_issue_ids: set[str] = set()
    adapted_targets: list[MemberOptimizationTarget] = []
    for target in targets:
        normalized = {
            _normalize_optimization_surface(surface) for surface in target.optimization_surfaces if str(surface).strip()
        }
        if normalized and normalized <= prompt_surfaces and not normalized & allowed_prompt_surfaces:
            metadata = dict(target.metadata)
            metadata["within_lever_surface_adaptation"] = {
                "lever": "instruction",
                "source_surfaces": sorted(normalized),
                "adapted_surface": preferred_surface,
                "reason": "restricted_prompt_delivery_surface",
            }
            adapted_targets.append(
                replace(
                    target,
                    optimization_surfaces=[preferred_surface],
                    metadata=metadata,
                )
            )
            adapted_issue_ids.update(target.attributed_issue_ids)
        else:
            adapted_targets.append(target)

    if not adapted_issue_ids:
        return targets, mechanism_report
    role_mechanisms = {
        role: [
            replace(mechanism, optimization_surface=preferred_surface)
            if mechanism.issue_id in adapted_issue_ids
            else mechanism
            for mechanism in mechanisms
        ]
        for role, mechanisms in mechanism_report.role_mechanisms.items()
    }
    return adapted_targets, replace(
        mechanism_report,
        role_mechanisms=role_mechanisms,
    )


class MemberActionPlanner:
    """Build dependency-aware optimization plans using DeepAgent + validation."""

    def __init__(
        self,
        planner_agent: MemberActionPlannerAgent | None = None,
    ) -> None:
        self._agent = planner_agent

    def _get_agent(
        self,
        model_config_ref: str,
        stage_retry_limit: int,
        agent_workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> MemberActionPlannerAgent:
        if self._agent is not None:
            return self._agent
        return MemberActionPlannerAgent(
            model_config_ref=model_config_ref,
            stage_retry_limit=stage_retry_limit,
            workspace=agent_workspace,
            agent_skills_dirs=agent_skills_dirs,
        )

    async def plan(  # pylint: disable=huawei-too-many-arguments
        self,
        targets: list[MemberOptimizationTarget],
        role_attribution_report: RoleAttributionReport,
        mechanism_attribution_report: MechanismAttributionReport,
        action_definitions: list[ActionDefinition],
        model_config_ref: str,
        stage_retry_limit: int = 2,
        harness_summaries: dict[str, str] | None = None,
        agent_workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
        rejected_capabilities: list[dict[str, Any]] | None = None,
        allowed_action_groups: list[str] | None = None,
        allowed_prompt_surfaces: list[str] | None = None,
        max_actions_per_plan: int = 0,
        optimization_hypotheses: list[dict[str, Any]] | None = None,
        optimization_experience: dict[str, Any] | None = None,
    ) -> MemberOptimizationPlan:
        """Generate and validate a member optimization plan.

        Calls MemberActionPlannerAgent to get a draft plan, then validates
        and rebuilds action waves via topological sort if needed.
        """
        import uuid

        (
            actionable_targets,
            actionable_role_report,
            actionable_mechanism_report,
            filtered_issue_ids,
        ) = _filter_actionable_planning_inputs(
            targets=targets,
            role_attribution_report=role_attribution_report,
            mechanism_attribution_report=mechanism_attribution_report,
        )
        (
            actionable_targets,
            actionable_mechanism_report,
            activation_phase_surface_adaptations,
        ) = _adapt_surface_for_activation_phase(
            targets=actionable_targets,
            mechanism_report=actionable_mechanism_report,
            optimization_hypotheses=list(optimization_hypotheses or []),
        )
        (
            actionable_targets,
            actionable_mechanism_report,
            new_skill_qualification_adaptations,
        ) = _adapt_surface_for_new_skill_qualification(
            targets=actionable_targets,
            mechanism_report=actionable_mechanism_report,
            optimization_hypotheses=list(optimization_hypotheses or []),
        )
        # A delivered-but-unapplied Skill already supplied the method. Adapt the
        # next attempt to a bounded execution checkpoint instead of another Skill.
        (
            actionable_targets,
            actionable_mechanism_report,
            recovery_surface_adaptations,
        ) = _adapt_recovery_surface_from_history(
            targets=actionable_targets,
            role_report=actionable_role_report,
            mechanism_report=actionable_mechanism_report,
            rejected_capabilities=list(rejected_capabilities or []),
        )
        restricted_groups = {str(group).strip() for group in (allowed_action_groups or []) if str(group).strip()}
        restricted_prompt_surfaces = {
            _normalize_optimization_surface(surface)
            for surface in (allowed_prompt_surfaces or [])
            if str(surface).strip()
        }
        unsupported_prompt_surfaces = restricted_prompt_surfaces - {
            "prompt_section",
            "identity",
            "soul",
        }
        if unsupported_prompt_surfaces:
            raise ValueError(f"unsupported allowed_prompt_surfaces: {sorted(unsupported_prompt_surfaces)}")
        (
            actionable_targets,
            actionable_mechanism_report,
        ) = _adapt_prompt_surface_within_instruction_lever(
            targets=actionable_targets,
            mechanism_report=actionable_mechanism_report,
            allowed_prompt_surfaces=restricted_prompt_surfaces,
        )
        deferred_capability_requests: list[dict[str, Any]] = []
        if restricted_groups:
            allowed_surfaces = set(restricted_groups)
            if "prompt" in allowed_surfaces:
                allowed_surfaces.update(restricted_prompt_surfaces or {"prompt_section", "identity", "soul"})
            retained_targets: list[MemberOptimizationTarget] = []
            for target in actionable_targets:
                target_surfaces = {str(surface).strip() for surface in target.optimization_surfaces}
                if not target_surfaces or target_surfaces & allowed_surfaces:
                    retained_targets.append(target)
                    continue
                deferred_capability_requests.append(
                    {
                        "role": target.role,
                        "issue_ids": list(target.attributed_issue_ids),
                        "required_surfaces": sorted(target_surfaces),
                        "status": "unsupported_capability_request",
                        "reason": (
                            "surface_not_allowed_in_restricted_optimization_mode; cross_lever_compensation_is_forbidden"
                        ),
                    }
                )
            actionable_targets = retained_targets
        if not actionable_targets:
            return MemberOptimizationPlan(
                plan_id=f"member_plan_{uuid.uuid4().hex[:8]}",
                targets=[],
                actions=[],
                action_waves=[],
                metadata={
                    "planner": "member_action_planner_agent",
                    "validated": True,
                    "action_count": 0,
                    "wave_count": 0,
                    "filtered_inactionable_issue_ids": filtered_issue_ids,
                    "activation_phase_surface_adaptations": (activation_phase_surface_adaptations),
                    "new_skill_qualification_adaptations": (new_skill_qualification_adaptations),
                    "allowed_action_groups": sorted(restricted_groups),
                    "allowed_prompt_surfaces": sorted(restricted_prompt_surfaces),
                    "capability_requests": deferred_capability_requests,
                },
            )

        agent = self._get_agent(
            model_config_ref,
            stage_retry_limit,
            agent_workspace,
            agent_skills_dirs,
        )
        selected_roles = {t.role for t in actionable_targets}
        action_definitions = filter_action_definitions(action_definitions)
        if restricted_groups:
            action_definitions = [
                definition for definition in action_definitions if definition.group in restricted_groups
            ]
        plan_data: dict[str, Any] = {}
        validation_errors: list[str] | None = None
        final_errors: list[str] = []
        for _attempt in range(_PLAN_VALIDATION_ATTEMPTS):
            create_plan_kwargs: dict[str, Any] = {
                "targets": actionable_targets,
                "role_attribution_report": actionable_role_report,
                "mechanism_attribution_report": actionable_mechanism_report,
                "action_definitions": action_definitions,
                "harness_summaries": harness_summaries,
                "validation_errors": validation_errors,
                "rejected_capabilities": rejected_capabilities,
            }
            if "optimization_hypotheses" in inspect.signature(agent.create_plan).parameters:
                create_plan_kwargs["optimization_hypotheses"] = list(optimization_hypotheses or [])
            if "optimization_experience" in inspect.signature(agent.create_plan).parameters:
                create_plan_kwargs["optimization_experience"] = dict(optimization_experience or {})
            plan_data = await agent.create_plan(
                **create_plan_kwargs,
            )
            _attach_improvement_briefs(
                plan_data=plan_data,
                targets=actionable_targets,
                role_attribution_report=actionable_role_report,
            )
            _normalize_required_declared_paths(plan_data)

            prompt_surface_errors = _validate_allowed_prompt_surfaces(
                plan_data,
                restricted_prompt_surfaces,
            )
            if prompt_surface_errors:
                validation_errors = prompt_surface_errors
                continue

            if max_actions_per_plan > 0 and len(plan_data.get("actions", [])) > max_actions_per_plan:
                validation_errors = [f"restricted optimization mode allows at most {max_actions_per_plan} action(s)"]
                continue

            final_errors = _validate_and_repair_plan_data(
                plan_data=plan_data,
                selected_roles=selected_roles,
                actionable_targets=actionable_targets,
                action_definitions=action_definitions,
            )
            if not final_errors:
                break
            validation_errors = final_errors
        else:
            reported_errors = validation_errors or final_errors
            raise RuntimeError("Member action plan validation failed: " + "; ".join(reported_errors))

        _bind_immutable_hypotheses(
            plan_data,
            list(optimization_hypotheses or []),
        )
        _annotate_action_bundles(
            plan_data,
            list(optimization_hypotheses or []),
        )
        plan_id = plan_data.get("plan_id", f"member_plan_{uuid.uuid4().hex[:8]}")

        actions = []
        for action_data in plan_data.get("actions", []):
            actions.append(
                MemberOptimizationAction(
                    action_id=action_data.get("action_id", ""),
                    role=action_data.get("role", ""),
                    member_name=action_data.get("member_name", ""),
                    action_group=action_data.get("action_group", ""),
                    operation=action_data.get("operation", ""),
                    action_type=action_data.get("action_type", ""),
                    target_path=action_data.get("target_path", ""),
                    description=action_data.get("description", ""),
                    rationale=action_data.get("rationale", ""),
                    attributed_issue_ids=action_data.get("attributed_issue_ids", []),
                    depends_on=action_data.get("depends_on", []),
                    run_if=action_data.get("run_if", "dependency_succeeded"),
                    allowed_skills=action_data.get("allowed_skills", []),
                    allowed_tools=sanitize_allowed_tools(action_data.get("allowed_tools", [])),
                    candidate_query=action_data.get("candidate_query", ""),
                    install_ref=action_data.get("install_ref", ""),
                    expected_effect=action_data.get("expected_effect", ""),
                    risk_notes=action_data.get("risk_notes", []),
                    declared_write_paths=action_data.get("declared_write_paths", []),
                    constraints=action_data.get("constraints", {}),
                )
            )

        # The model-provided wave list is descriptive only. Dependencies are the
        # source of truth and must determine the persisted execution order.
        action_waves = build_action_waves(actions)

        return MemberOptimizationPlan(
            plan_id=plan_id,
            targets=actionable_targets,
            actions=actions,
            action_waves=action_waves,
            metadata={
                "planner": "member_action_planner_agent",
                "validated": True,
                "action_count": len(actions),
                "wave_count": len(action_waves),
                "filtered_inactionable_issue_ids": filtered_issue_ids,
                "allowed_action_groups": sorted(restricted_groups),
                "allowed_prompt_surfaces": sorted(restricted_prompt_surfaces),
                "max_actions_per_plan": max_actions_per_plan,
                "capability_requests": deferred_capability_requests,
                "activation_phase_surface_adaptations": (activation_phase_surface_adaptations),
                "new_skill_qualification_adaptations": (new_skill_qualification_adaptations),
                "recovery_surface_adaptations": recovery_surface_adaptations,
                "optimization_hypothesis_ids": list(
                    (plan_data.get("metadata") or {}).get(
                        "optimization_hypothesis_ids",
                        [],
                    )
                    if isinstance(plan_data.get("metadata"), dict)
                    else []
                ),
                "action_bundles": list(
                    (plan_data.get("metadata") or {}).get("action_bundles", [])
                    if isinstance(plan_data.get("metadata"), dict)
                    else []
                ),
                "semantic_authority": (
                    "immutable_optimization_hypotheses" if optimization_hypotheses else "attribution_pipeline"
                ),
            },
        )

    @staticmethod
    def write_plan(plan: MemberOptimizationPlan, output_dir: Path) -> Path:
        """Write plan to plan.yaml."""
        path = output_dir / "plan.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        from dataclasses import asdict as _asdict

        payload = {
            "plan_id": plan.plan_id,
            "targets": [_asdict(t) for t in plan.targets],
            "actions": [_asdict(a) for a in plan.actions],
            "action_waves": plan.action_waves,
            "metadata": plan.metadata,
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

        return path


__all__ = [
    "MemberActionPlanner",
    "MemberActionPlannerAgent",
]
