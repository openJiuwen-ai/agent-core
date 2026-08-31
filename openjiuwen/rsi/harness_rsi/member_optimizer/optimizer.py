# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MemberOptimizer facade and PublishCoordinator.

Per feat_009 rule.md and design.md v1.10.
Orchestrates the two-step attribution-first pipeline:
  1. RoleAttributor       -> role_attribution.yaml
  2. MechanismAttributor -> mechanism_attribution.yaml
  3. MemberSelector       -> member_selection.yaml
  4. MemberActionPlanner -> plan.yaml
  5. MemberActionExecutor -> execution_results.json
  6. HarnessChangeVerifier -> verification.json + fix_result.json
  7. PublishCoordinator   -> current_harnesses/{role} + current_harness_refs.yaml
                             + member_optimization_ref.yaml
"""

from __future__ import annotations

import inspect
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.harness_rsi.config import MemberOptimizerConfig
from openjiuwen.rsi.harness_rsi.member_optimizer.action_executor import MemberActionExecutor
from openjiuwen.rsi.harness_rsi.member_optimizer.action_groups import (
    load_action_definitions,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.action_planner import MemberActionPlanner
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.factory import (
    load_member_optimizer_model,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.hypothesis import (
    load_optimization_hypotheses,
    write_candidate_manifest,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.execution_contract import role_execution_errors
from openjiuwen.rsi.harness_rsi.member_optimizer.lever import (
    LEVER_ACTION,
    LEVER_CONFIGURATION,
    LEVER_CONTROL,
    LEVER_INSTRUCTION,
    available_surfaces_for_lever,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.loader import (
    load_analysis_ref,
    load_eval_ref,
    resolve_candidate_roles,
    resolve_team_issues,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.mechanism_attributor import MechanismAttributor
from openjiuwen.rsi.harness_rsi.member_optimizer.member_selector import MemberSelector
from openjiuwen.rsi.harness_rsi.member_optimizer.path_layout import (
    MemberOptimizerPathLayout,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.role_attributor import RoleAttributor
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
    MechanismAttributionReport,
    MemberOptimizationArtifact,
    MemberOptimizationPlan,
    MemberOptimizationTarget,
    MemberSelectionReport,
    RoleAttributionReport,
    RoleIssueAttribution,
    RoleMechanismAttribution,
    RoleResult,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.verification import HarnessChangeVerifier
from openjiuwen.rsi.harness_rsi.member_optimizer.worktree_coordinator import (
    resolve_integration_worktree_path,
)


@dataclass(frozen=True)
class _PublishRequest:
    run_dir: Path
    path_layout: MemberOptimizerPathLayout
    output_root: Path
    selection_report: Any
    plan: MemberOptimizationPlan
    execution_results: Any
    verification_result: Any
    eval_ref_path: str
    analysis_result_path: str
    harness_refs_path: str
    role_attr_path: str
    mechanism_attr_path: str
    selection_path: str
    plan_path: str
    execution_result_path: str
    verification_path: str
    fix_result_path: str
    defer_publish: bool = False
    candidate_manifest_path: str = ""
    optimization_hypotheses_path: str = ""


@dataclass(frozen=True)
class _HarnessRefsWriteRequest:
    output_root: Path
    harness_refs_path: str
    published_roles: list[str]
    failed_roles: list[str]
    skipped_roles: list[str]
    role_results: dict[str, RoleResult]
    path_layout: MemberOptimizerPathLayout | None = None
    refs_path: Path | None = None
    published_ref_paths: dict[str, str] | None = None
    cumulative_publish: bool = False


class MemberOptimizer:
    """Orchestrate the member harness optimization pipeline.

    Coordinates two-step attribution, selection, planning, execution,
    verification, repair, and publication of Expert Harness changes.
    """

    def __init__(
        self,
        config: MemberOptimizerConfig,
        *,
        role_attributor: RoleAttributor | None = None,
        mechanism_attributor: MechanismAttributor | None = None,
        member_selector: MemberSelector | None = None,
        action_planner: MemberActionPlanner | None = None,
        action_executor: MemberActionExecutor | None = None,
        verifier: HarnessChangeVerifier | None = None,
    ) -> None:
        self.config = config
        self._action_group_config_paths: list[str] = list(config.action_group_configs)

        self._role_attributor = role_attributor or RoleAttributor()
        self._mechanism_attributor = mechanism_attributor or MechanismAttributor()
        self._member_selector = member_selector or MemberSelector()
        self._action_planner = action_planner or MemberActionPlanner()
        self._action_executor = action_executor or MemberActionExecutor(
            execution_concurrency=config.execution_concurrency,
            role_execution_concurrency=config.role_execution_concurrency,
            action_execution_concurrency_per_role=config.action_execution_concurrency_per_role,
            agent_skills_dirs=config.agent_skills_dirs,
        )
        self._verifier = verifier or HarnessChangeVerifier(
            agent_skills_dirs=config.agent_skills_dirs,
        )

    def register_action_group(self, action_group_config_path: str) -> None:
        """Register an action group configuration path."""
        if action_group_config_path:
            self._action_group_config_paths.append(action_group_config_path)

    async def optimize(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        harness_refs_path: str,
        output_dir: str,
        *,
        defer_publish: bool = False,
        rejected_capabilities: list[dict[str, Any]] | None = None,
        single_harness: bool = False,
        optimization_hypotheses_path: str = "",
        optimization_issue_ids: list[str] | None = None,
        optimization_experience: dict[str, Any] | None = None,
    ) -> str:
        """Execute the full member optimization pipeline.

        Args:
            eval_ref_path: Path to eval_ref.yaml from TeamEvaluator.
            analysis_result_path: Path to analysis_ref.yaml from EvaluationResultAnalyzer.
            harness_refs_path: Path to harness refs (file or directory).
            output_dir: Root directory for optimization output artifacts.

        Returns:
            Path to the written member_optimization_ref.yaml.

        Raises:
            RuntimeError: if model_config_ref is missing or invalid,
                         or if attribution/retry exhaustion occurs.
        """
        if self.config.freeze:
            return await self._write_noop_artifact(
                eval_ref_path,
                analysis_result_path,
                harness_refs_path,
                output_dir,
                status="skipped",
                reason="optimizer is frozen",
                defer_publish=defer_publish,
            )

        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        if defer_publish:
            reusable_ref = _find_reusable_pending_optimization(
                output_root=output_root,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                harness_refs_path=harness_refs_path,
            )
            if reusable_ref:
                return reusable_ref

        load_member_optimizer_model(self.config.model_config_ref)

        path_layout = MemberOptimizerPathLayout.from_output_root(output_root)
        run_dir = _allocate_optimization_dir(output_root)

        eval_ref = load_eval_ref(eval_ref_path)
        analysis_ref = load_analysis_ref(analysis_result_path)
        team_issues = resolve_team_issues(analysis_ref)

        candidate_roles = resolve_candidate_roles(
            harness_refs_path=harness_refs_path,
            eval_ref=eval_ref,
            team_skill_ref_path=eval_ref.team_skill_ref_path,
        )

        if not candidate_roles and not self.config.allow_empty_harness_refs_noop:
            raise RuntimeError(
                f"No candidate roles found and allow_empty_harness_refs_noop=False. "
                f"harness_refs_path={harness_refs_path}, eval_ref.harness_refs_path={eval_ref.harness_refs_path}"
            )
        _validate_candidate_harness_refs(candidate_roles)

        hypotheses = (
            load_optimization_hypotheses(optimization_hypotheses_path)
            if single_harness and optimization_hypotheses_path
            else []
        )
        if single_harness:
            hypotheses_by_issue = {
                str(item.get("source_issue_id", "")): item
                for item in hypotheses
                if str(item.get("source_issue_id", ""))
            }
            missing_hypotheses = sorted(
                issue.issue_id for issue in team_issues if issue.issue_id not in hypotheses_by_issue
            )
            if missing_hypotheses:
                raise RuntimeError(
                    "single-harness analyzer issues are missing immutable hypotheses: " + ", ".join(missing_hypotheses)
                )
            if optimization_issue_ids is not None:
                issue_scope = {str(issue_id) for issue_id in optimization_issue_ids if str(issue_id)}
                unknown_issue_ids = sorted(issue_scope - set(hypotheses_by_issue))
                if unknown_issue_ids:
                    raise RuntimeError(
                        "single-harness optimization issue scope is unknown: " + ", ".join(unknown_issue_ids)
                    )
                team_issues = [issue for issue in team_issues if issue.issue_id in issue_scope]
                hypotheses = [item for item in hypotheses if str(item.get("source_issue_id", "")) in issue_scope]
        if single_harness:
            (
                role_attr_report,
                mechanism_attr_report,
                selection_report,
            ) = _compile_single_harness_planning_inputs(
                team_issues=team_issues,
                candidate_roles=candidate_roles,
                hypotheses=hypotheses,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                harness_refs_path=harness_refs_path,
                allowed_action_groups=self.config.allowed_action_groups,
            )
            role_attr_path = _write_dataclass_yaml(
                run_dir / "role_attribution.yaml",
                role_attr_report,
            )
            mechanism_attr_path = _write_dataclass_yaml(
                run_dir / "mechanism_attribution.yaml",
                mechanism_attr_report,
            )
            selection_path = _write_dataclass_yaml(
                run_dir / "member_selection.yaml",
                selection_report,
            )
        else:
            # Team mode keeps role/mechanism attribution because ownership is ambiguous.
            role_attr_report = await self._role_attributor.attribute(
                eval_ref=eval_ref,
                team_issues=team_issues,
                candidate_roles=candidate_roles,
                model_config_ref=self.config.model_config_ref,
                attribution_retry_limit=self.config.attribution_retry_limit,
                max_cases_per_issue=self.config.max_cases_per_issue,
                max_trace_chars=self.config.max_trace_excerpt_chars,
                max_result_chars=self.config.max_result_excerpt_chars,
                agent_workspace=run_dir / "stage_workspaces" / "role_attribution",
                agent_skills_dirs=self.config.agent_skills_dirs,
            )
            role_attr_report = RoleAttributionReport(
                attribution_id=role_attr_report.attribution_id,
                source_eval_ref_path=eval_ref_path,
                source_analysis_result_path=analysis_result_path,
                harness_refs_path=harness_refs_path,
                candidate_roles=role_attr_report.candidate_roles,
                assigned_role_issues=role_attr_report.assigned_role_issues,
                unassigned_issues=role_attr_report.unassigned_issues,
                warnings=role_attr_report.warnings,
                metadata=role_attr_report.metadata,
            )
            role_attr_path = self._role_attributor.write_report(role_attr_report, run_dir)

            mechanism_attr_report = await self._mechanism_attributor.attribute(
                role_attribution_report=role_attr_report,
                eval_ref=eval_ref,
                candidate_roles=candidate_roles,
                model_config_ref=self.config.model_config_ref,
                attribution_retry_limit=self.config.attribution_retry_limit,
                max_cases_per_issue=self.config.max_cases_per_issue,
                max_trace_chars=self.config.max_trace_excerpt_chars,
                max_result_chars=self.config.max_result_excerpt_chars,
                agent_workspace=run_dir / "stage_workspaces" / "mechanism_attribution",
                agent_skills_dirs=self.config.agent_skills_dirs,
            )
            mechanism_attr_path = self._mechanism_attributor.write_report(
                mechanism_attr_report,
                run_dir,
            )

        # No-op: no issues
        if not team_issues:
            return await self._write_noop_artifact(
                eval_ref_path,
                analysis_result_path,
                harness_refs_path,
                output_dir,
                status="no_issues",
                run_dir=run_dir,
                role_attr_path=str(role_attr_path),
                mechanism_attr_path=str(mechanism_attr_path),
                defer_publish=defer_publish,
            )

        # Phase 3: Selection. Single-harness ownership was resolved above.
        if not single_harness:
            selection_report = self._member_selector.select(
                role_attribution_report=role_attr_report,
                mechanism_attribution_report=mechanism_attr_report,
                min_attribution_confidence=self.config.min_attribution_confidence,
                max_roles_per_run=self.config.max_roles_per_run,
            )
            selection_report = replace(
                selection_report,
                source_role_attribution_path=str(role_attr_path),
                source_mechanism_attribution_path=str(mechanism_attr_path),
            )
            selection_path = self._member_selector.write_report(selection_report, run_dir)

        # No-op: no targets
        if not selection_report.targets:
            return await self._write_noop_artifact(
                eval_ref_path,
                analysis_result_path,
                harness_refs_path,
                output_dir,
                status="no_targets",
                run_dir=run_dir,
                role_attr_path=str(role_attr_path),
                mechanism_attr_path=str(mechanism_attr_path),
                selection_path=str(selection_path),
                defer_publish=defer_publish,
            )

        # Phase 4: Planning
        action_definitions = load_action_definitions(self._action_group_config_paths)
        planning_workspace = run_dir / "stage_workspaces" / "action_planning"
        try:
            plan_kwargs: dict[str, Any] = {
                "targets": selection_report.targets,
                "role_attribution_report": role_attr_report,
                "mechanism_attribution_report": mechanism_attr_report,
                "action_definitions": action_definitions,
                "model_config_ref": self.config.model_config_ref,
                "stage_retry_limit": self.config.stage_retry_limit,
                "agent_workspace": planning_workspace,
                "agent_skills_dirs": self.config.agent_skills_dirs,
            }
            planner_parameters = inspect.signature(self._action_planner.plan).parameters
            if "rejected_capabilities" in planner_parameters:
                plan_kwargs["rejected_capabilities"] = list(rejected_capabilities or [])
            if "allowed_action_groups" in planner_parameters:
                plan_kwargs["allowed_action_groups"] = self.config.allowed_action_groups
            if "allowed_prompt_surfaces" in planner_parameters:
                plan_kwargs["allowed_prompt_surfaces"] = self.config.allowed_prompt_surfaces
            if "max_actions_per_plan" in planner_parameters:
                plan_kwargs["max_actions_per_plan"] = self.config.max_actions_per_plan
            if "optimization_hypotheses" in planner_parameters:
                plan_kwargs["optimization_hypotheses"] = hypotheses
            if "optimization_experience" in planner_parameters:
                plan_kwargs["optimization_experience"] = dict(optimization_experience or {})
            plan = await self._action_planner.plan(
                **plan_kwargs,
            )
        except Exception as exc:
            _write_stage_error(
                run_dir / "action_planning_error.json",
                stage="action_planning",
                error=exc,
                details={
                    "selection_path": str(selection_path),
                    "role_attribution_path": str(role_attr_path),
                    "mechanism_attribution_path": str(mechanism_attr_path),
                    "planning_workspace": str(planning_workspace),
                    "selected_roles": [target.role for target in selection_report.targets],
                    "selected_issue_ids": sorted(
                        {issue_id for target in selection_report.targets for issue_id in target.attributed_issue_ids}
                    ),
                },
            )
            if _is_action_plan_rejection(exc):
                skipped_roles = [target.role for target in selection_report.targets]
                role_results = {
                    target.role: RoleResult(
                        status="skipped",
                        before_harness_ref_path=target.harness_ref_path,
                        after_harness_ref_path=target.harness_ref_path,
                        verification_status="not_run",
                        error=str(exc),
                    )
                    for target in selection_report.targets
                }
                return await self._write_noop_artifact(
                    eval_ref_path,
                    analysis_result_path,
                    harness_refs_path,
                    output_dir,
                    status="planning_rejected",
                    reason=str(exc),
                    run_dir=run_dir,
                    role_attr_path=str(role_attr_path),
                    mechanism_attr_path=str(mechanism_attr_path),
                    selection_path=str(selection_path),
                    roles=skipped_roles,
                    skipped_roles=skipped_roles,
                    role_results=role_results,
                    defer_publish=defer_publish,
                )
            raise
        plan_path = self._action_planner.write_plan(plan, run_dir)
        candidate_manifest_path = ""
        if hypotheses and optimization_hypotheses_path:
            candidate_manifest_path = write_candidate_manifest(
                output_path=run_dir / "candidate_manifest.yaml",
                source_harness_refs_path=harness_refs_path,
                hypotheses_path=optimization_hypotheses_path,
                plan=asdict(plan),
            )
        if not plan.actions:
            skipped_roles = [target.role for target in selection_report.targets]
            role_results = {
                target.role: RoleResult(
                    status="skipped",
                    before_harness_ref_path=target.harness_ref_path,
                    after_harness_ref_path=target.harness_ref_path,
                    verification_status="not_run",
                    error="planner returned no executable actions",
                )
                for target in selection_report.targets
            }
            return await self._write_noop_artifact(
                eval_ref_path,
                analysis_result_path,
                harness_refs_path,
                output_dir,
                status="no_actions",
                reason="planner returned no executable actions",
                run_dir=run_dir,
                role_attr_path=str(role_attr_path),
                mechanism_attr_path=str(mechanism_attr_path),
                selection_path=str(selection_path),
                plan_path=str(plan_path),
                roles=skipped_roles,
                skipped_roles=skipped_roles,
                role_results=role_results,
                defer_publish=defer_publish,
            )

        # Phase 5: Execution
        worktrees_dir = path_layout.worktrees_dir(run_dir.name)
        execution_results = await self._action_executor.execute(
            plan=plan,
            output_dir=str(run_dir),
            worktrees_dir=str(worktrees_dir),
            model_config_ref=self.config.model_config_ref,
        )
        execution_result_path = run_dir / "execution_results.json"
        _write_execution_results(execution_result_path, execution_results)
        runtime_run_dir = path_layout.run_runtime_dir(run_dir.name)
        runtime_run_dir.mkdir(parents=True, exist_ok=True)
        _write_execution_results(runtime_run_dir / "execution_results.json", execution_results)
        if plan_path.is_file():
            shutil.copy2(plan_path, runtime_run_dir / "plan.yaml")

        # Phase 6: Verification
        verification_result = await self._verifier.verify(
            plan=plan,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "verification.json"),
        )
        verification_path = run_dir / "verification.json"

        # Phase 7: Repair
        await self._verifier.repair(
            verification_result=verification_result,
            worktrees_dir=worktrees_dir,
            output_path=str(run_dir / "fix_result.json"),
            model_config_ref=self.config.model_config_ref,
            stage_retry_limit=self.config.stage_retry_limit,
        )
        fix_result_path = run_dir / "fix_result.json"
        if verification_result.status != "passed":
            verification_result = await self._verifier.verify(
                plan=plan,
                worktrees_dir=worktrees_dir,
                output_path=str(verification_path),
            )

        # Phase 8: Publish
        self._publish(
            _PublishRequest(
                run_dir=run_dir,
                path_layout=path_layout,
                output_root=output_root,
                selection_report=selection_report,
                plan=plan,
                execution_results=execution_results,
                verification_result=verification_result,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                harness_refs_path=harness_refs_path,
                role_attr_path=str(role_attr_path),
                mechanism_attr_path=str(mechanism_attr_path),
                selection_path=str(selection_path),
                plan_path=str(plan_path),
                execution_result_path=str(execution_result_path),
                verification_path=str(verification_path),
                fix_result_path=str(fix_result_path),
                defer_publish=defer_publish,
                candidate_manifest_path=candidate_manifest_path,
                optimization_hypotheses_path=optimization_hypotheses_path,
            )
        )

        return str(run_dir / "member_optimization_ref.yaml")

    async def _write_noop_artifact(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        harness_refs_path: str,
        output_dir: str,
        *,
        status: str,
        reason: str = "",
        run_dir: Path | None = None,
        role_attr_path: str = "",
        mechanism_attr_path: str = "",
        selection_path: str = "",
        plan_path: str = "",
        execution_result_path: str = "",
        verification_path: str = "",
        fix_result_path: str = "",
        roles: list[str] | None = None,
        skipped_roles: list[str] | None = None,
        role_results: dict[str, RoleResult] | None = None,
        defer_publish: bool = False,
    ) -> str:
        """Write a no-op artifact when no optimization can be performed."""
        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        path_layout = MemberOptimizerPathLayout.from_output_root(output_root)
        if run_dir is None:
            run_dir = _allocate_optimization_dir(output_root)

        if not role_attr_path:
            empty_role_report = RoleAttributionReport(
                attribution_id=f"role_attribution_{_short_id()}",
                source_eval_ref_path=eval_ref_path,
                source_analysis_result_path=analysis_result_path,
                harness_refs_path=harness_refs_path,
                candidate_roles=[],
                assigned_role_issues=[],
                unassigned_issues=[],
                warnings=[reason] if reason else [],
                metadata={"status": status},
            )
            role_attr_path = str(self._role_attributor.write_report(empty_role_report, run_dir))

        current_refs_path = Path(harness_refs_path).expanduser()
        if not defer_publish:
            current_refs_path = _write_current_harness_refs(
                _HarnessRefsWriteRequest(
                    output_root=output_root,
                    harness_refs_path=harness_refs_path,
                    published_roles=[],
                    failed_roles=[],
                    skipped_roles=skipped_roles or [],
                    role_results=role_results or {},
                    path_layout=path_layout,
                )
            )

        artifact = MemberOptimizationArtifact(
            optimization_id=run_dir.name,
            output_dir=str(run_dir),
            status=status,
            optimized_harness_refs_path=str(current_refs_path),
            roles=roles or [],
            published_roles=[],
            staged_roles=roles or [],
            verified_roles=[],
            promoted_roles=[],
            failed_roles=[],
            skipped_roles=skipped_roles or [],
            role_attribution_path=role_attr_path,
            mechanism_attribution_path=mechanism_attr_path,
            selection_path=selection_path,
            plan_path=plan_path,
            execution_result_path=execution_result_path,
            verification_path=verification_path,
            fix_result_path=fix_result_path,
            role_results=role_results or {},
            metadata={
                "eval_ref_path": eval_ref_path,
                "analysis_result_path": analysis_result_path,
                "source_harness_refs_path": harness_refs_path,
                "runtime_root": str(path_layout.runtime_root),
                "reason": reason,
            },
        )

        ref_path = run_dir / "member_optimization_ref.yaml"
        with open(ref_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(artifact), f, allow_unicode=True, sort_keys=False)

        return str(ref_path)

    @staticmethod
    def _publish(
        request: _PublishRequest,
    ) -> MemberOptimizationArtifact:
        """Publish verified roles and write the final artifact."""
        run_dir = request.run_dir
        path_layout = request.path_layout
        output_root = request.output_root
        selection_report = request.selection_report
        plan = request.plan
        execution_results = request.execution_results
        verification_result = request.verification_result
        eval_ref_path = request.eval_ref_path
        analysis_result_path = request.analysis_result_path
        harness_refs_path = request.harness_refs_path
        role_attr_path = request.role_attr_path
        mechanism_attr_path = request.mechanism_attr_path
        selection_path = request.selection_path
        plan_path = request.plan_path
        execution_result_path = request.execution_result_path
        verification_path = request.verification_path
        fix_result_path = request.fix_result_path
        defer_publish = request.defer_publish
        candidate_manifest_path = request.candidate_manifest_path
        optimization_hypotheses_path = request.optimization_hypotheses_path
        selected_roles = [t.role for t in selection_report.targets]

        harness_refs = {r.role: r.harness_ref_path for r in selection_report.targets}
        harness_refs.update(_read_input_harness_refs(harness_refs_path))

        published_roles: list[str] = []
        failed_roles: list[str] = []
        skipped_roles: list[str] = []
        role_results: dict[str, RoleResult] = {}

        execution_errors_by_role = {
            role: role_execution_errors(plan, execution_results, role) for role in selected_roles
        }
        verified_roles = {
            role: vr.status == "passed" and not execution_errors_by_role.get(role)
            for role, vr in verification_result.role_results.items()
        }

        for role in sorted(selected_roles):
            before_ref = harness_refs.get(role, "")
            integration_dir = resolve_integration_worktree_path(
                path_layout.worktrees_dir(run_dir.name),
                role,
            )
            role_verified = verified_roles.get(role, False)

            if role_verified:
                try:
                    path_layout.write_role_mapping(role)
                    publish_dir = (
                        path_layout.candidate_harness_dir(run_dir.name, role)
                        if defer_publish
                        else path_layout.current_harness_dir(role)
                    )
                    tmp_dir = path_layout.publish_tmp_dir(run_dir.name, role)
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    if integration_dir.exists():
                        shutil.copytree(integration_dir, tmp_dir, dirs_exist_ok=True)
                    if publish_dir.exists():
                        shutil.rmtree(publish_dir)
                    shutil.copytree(tmp_dir, publish_dir, dirs_exist_ok=True)
                    shutil.rmtree(tmp_dir)
                    after_ref = str(publish_dir)
                    published_roles.append(role)
                    role_results[role] = RoleResult(
                        status="candidate_ready" if defer_publish else "published",
                        before_harness_ref_path=before_ref,
                        after_harness_ref_path=after_ref,
                        action_ids=[r.action_id for r in execution_results if r.role == role],
                        verification_status="passed",
                    )
                except Exception as e:
                    failed_roles.append(role)
                    role_results[role] = RoleResult(
                        status="failed",
                        before_harness_ref_path=before_ref,
                        after_harness_ref_path=before_ref,
                        verification_status="failed",
                        error=str(e),
                    )
            else:
                failed_roles.append(role)
                role_results[role] = RoleResult(
                    status="failed",
                    before_harness_ref_path=before_ref,
                    after_harness_ref_path=before_ref,
                    action_ids=[r.action_id for r in execution_results if r.role == role],
                    verification_status="failed",
                    error="; ".join(execution_errors_by_role.get(role, [])) or "Verification failed",
                )

        published_ref_paths = None
        if defer_publish:
            published_ref_paths = {role: role_results[role].after_harness_ref_path for role in published_roles}
        current_refs_path = _write_current_harness_refs(
            _HarnessRefsWriteRequest(
                output_root=output_root,
                harness_refs_path=harness_refs_path,
                published_roles=published_roles,
                failed_roles=failed_roles,
                skipped_roles=skipped_roles,
                role_results=role_results,
                path_layout=path_layout,
                refs_path=(run_dir / "candidate_harness_refs.yaml") if defer_publish else None,
                published_ref_paths=published_ref_paths,
                cumulative_publish=defer_publish,
            )
        )
        if candidate_manifest_path:
            _update_candidate_manifest(
                candidate_manifest_path,
                status=("candidate_ready" if published_roles else "verification_failed"),
                candidate_harness_refs_path=str(current_refs_path),
                verified_roles=published_roles,
                failed_roles=failed_roles,
            )

        if published_roles and (failed_roles or skipped_roles):
            status = "partial_success"
        elif published_roles:
            status = "success"
        else:
            status = "failed"

        artifact = MemberOptimizationArtifact(
            optimization_id=run_dir.name,
            output_dir=str(run_dir),
            status=status,
            optimized_harness_refs_path=str(current_refs_path),
            roles=selected_roles,
            candidate_ready_roles=published_roles if defer_publish else [],
            published_roles=published_roles,
            staged_roles=selected_roles,
            verified_roles=published_roles,
            promoted_roles=[],
            promotion_status="pending_gate" if defer_publish and published_roles else "not_evaluated",
            failed_roles=failed_roles,
            skipped_roles=skipped_roles,
            role_attribution_path=role_attr_path,
            mechanism_attribution_path=mechanism_attr_path,
            selection_path=selection_path,
            plan_path=plan_path,
            execution_result_path=execution_result_path,
            verification_path=verification_path,
            fix_result_path=fix_result_path,
            role_results=role_results,
            metadata={
                "eval_ref_path": eval_ref_path,
                "analysis_result_path": analysis_result_path,
                "source_harness_refs_path": harness_refs_path,
                "runtime_root": str(path_layout.runtime_root),
                "runtime_run_dir": str(path_layout.run_runtime_dir(run_dir.name)),
                "runtime_worktrees_dir": str(path_layout.worktrees_dir(run_dir.name)),
                "candidate_manifest_path": candidate_manifest_path,
                "optimization_hypotheses_path": optimization_hypotheses_path,
                "execution_errors_by_role": execution_errors_by_role,
            },
        )

        ref_path = run_dir / "member_optimization_ref.yaml"
        with open(ref_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(artifact), f, allow_unicode=True, sort_keys=False)

        return artifact


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile_single_harness_planning_inputs(
    *,
    team_issues: list[Any],
    candidate_roles: list[Any],
    hypotheses: list[dict[str, Any]],
    eval_ref_path: str,
    analysis_result_path: str,
    harness_refs_path: str,
    allowed_action_groups: list[str],
) -> tuple[
    RoleAttributionReport,
    MechanismAttributionReport,
    MemberSelectionReport,
]:
    """Bind analyzer issues directly to the sole role without LLM reinterpretation."""
    if len(candidate_roles) != 1:
        raise RuntimeError(
            f"single-harness optimization requires exactly one candidate role; found {len(candidate_roles)}"
        )
    role = candidate_roles[0]
    hypotheses_by_issue = {
        str(item.get("source_issue_id", "")): item for item in hypotheses if str(item.get("source_issue_id", ""))
    }
    missing_hypotheses = sorted(issue.issue_id for issue in team_issues if issue.issue_id not in hypotheses_by_issue)
    if missing_hypotheses:
        raise RuntimeError(
            "single-harness analyzer issues are missing immutable hypotheses: " + ", ".join(missing_hypotheses)
        )
    assigned: list[RoleIssueAttribution] = []
    mechanisms: list[RoleMechanismAttribution] = []
    issue_ids: list[str] = []
    executable_issue_ids: list[str] = []
    surfaces_by_issue: dict[str, list[str]] = {}
    deferred_levers: list[dict[str, Any]] = []
    for issue in team_issues:
        hypothesis = hypotheses_by_issue.get(issue.issue_id, {})
        lever_policy = hypothesis.get("lever_policy", {}) if isinstance(hypothesis.get("lever_policy"), dict) else {}
        recommended_lever = str(lever_policy.get("recommended_lever", "unresolved") or "unresolved")
        issue_surfaces = available_surfaces_for_lever(
            recommended_lever,
            allowed_action_groups,
        )
        surfaces_by_issue[issue.issue_id] = issue_surfaces
        if issue_surfaces:
            executable_issue_ids.append(issue.issue_id)
        else:
            deferred_levers.append(
                {
                    "issue_id": issue.issue_id,
                    "recommended_lever": recommended_lever,
                    "target_ref": lever_policy.get("target_ref", ""),
                    "status": "deferred",
                    "reason": "recommended_lever_has_no_executable_surface",
                }
            )
        evidence = [dict(item) for item in issue.evidence if isinstance(item, dict)]
        evidence.append(
            {
                "summary": issue.summary,
                "recommendation": issue.recommendation,
                "optimization_hypothesis_id": hypothesis.get("hypothesis_id", ""),
                "required_behavior": hypothesis.get(
                    "required_behavior",
                    issue.recommendation,
                ),
            }
        )
        assigned.append(
            RoleIssueAttribution(
                issue_id=issue.issue_id,
                role=role.role,
                member_name=role.member_name,
                harness_ref_path=role.harness_ref_path,
                confidence=1.0,
                evidence=evidence,
                trace_refs=list(hypothesis.get("evidence_refs", [])),
                rationale=(
                    f"Analyzer-authored immutable hypothesis "
                    f"{hypothesis.get('hypothesis_id', '<missing>')}; downstream stages "
                    "must preserve required_behavior verbatim."
                ),
            )
        )
        mechanisms.append(
            RoleMechanismAttribution(
                issue_id=issue.issue_id,
                role=role.role,
                mechanism_type={
                    LEVER_INSTRUCTION: "instruction",
                    LEVER_ACTION: "tool",
                    LEVER_CONTROL: "workflow",
                    LEVER_CONFIGURATION: "config",
                }.get(recommended_lever, "insufficient_role_evidence"),
                failure_signature=(
                    "prompt_instruction_mismatch"
                    if recommended_lever == LEVER_INSTRUCTION
                    else "missed_exploration_or_capability"
                    if recommended_lever == LEVER_ACTION
                    else "config_or_rail_mismatch"
                    if recommended_lever in {LEVER_CONTROL, LEVER_CONFIGURATION}
                    else "insufficient_role_evidence"
                ),
                confidence=1.0,
                optimization_surface=issue_surfaces[0] if len(issue_surfaces) == 1 else "",
                evidence=evidence,
                evidence_refs=list(hypothesis.get("evidence_refs", [])),
                rationale=(
                    f"Analyzer-selected lever={recommended_lever}; choose only an "
                    "executable surface within that lever without changing the immutable "
                    "hypothesis."
                ),
            )
        )
        issue_ids.append(issue.issue_id)

    role_report = RoleAttributionReport(
        attribution_id="single_harness_direct_attribution",
        source_eval_ref_path=eval_ref_path,
        source_analysis_result_path=analysis_result_path,
        harness_refs_path=harness_refs_path,
        candidate_roles=list(candidate_roles),
        assigned_role_issues=assigned,
        metadata={
            "mode": "single_harness_direct",
            "semantic_rewrite_stages_bypassed": [
                "role_attributor",
                "mechanism_attributor",
            ],
        },
    )
    mechanism_report = MechanismAttributionReport(
        mechanism_attribution_id="single_harness_surface_unresolved",
        role_mechanisms={role.role: mechanisms} if mechanisms else {},
        metadata={
            "mode": "single_harness_direct",
            "semantic_authority": "optimization_hypotheses",
        },
    )
    surfaces = [surface for issue_id in executable_issue_ids for surface in surfaces_by_issue.get(issue_id, [])]
    targets = []
    if executable_issue_ids:
        targets.append(
            MemberOptimizationTarget(
                role=role.role,
                member_name=role.member_name,
                harness_ref_path=role.harness_ref_path,
                attributed_issue_ids=executable_issue_ids,
                confidence=1.0,
                reason="Directly bound to analyzer-authored immutable hypotheses.",
                evidence_refs=[{"issue_id": issue_id} for issue_id in issue_ids],
                mechanism_types=list(
                    dict.fromkeys(
                        mechanism.mechanism_type
                        for mechanism in mechanisms
                        if mechanism.issue_id in executable_issue_ids
                    )
                ),
                optimization_surfaces=list(dict.fromkeys(surfaces)),
                metadata={
                    "optimization_hypothesis_ids": [
                        hypotheses_by_issue[issue_id]["hypothesis_id"]
                        for issue_id in executable_issue_ids
                        if issue_id in hypotheses_by_issue
                    ],
                },
            )
        )
    selection_report = MemberSelectionReport(
        selection_id="single_harness_direct_selection",
        targets=targets,
        metadata={
            "mode": "single_harness_direct",
            "selector_bypassed": True,
            "deferred_levers": deferred_levers,
        },
    )
    return role_report, mechanism_report, selection_report


def _write_dataclass_yaml(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(asdict(value), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _update_candidate_manifest(
    path: str,
    *,
    status: str,
    candidate_harness_refs_path: str,
    verified_roles: list[str],
    failed_roles: list[str],
) -> None:
    target = Path(path).expanduser().resolve()
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"candidate manifest must be a mapping: {target}")
    payload.update(
        {
            "status": status,
            "candidate_harness_refs_path": candidate_harness_refs_path,
            "verified_roles": list(verified_roles),
            "failed_roles": list(failed_roles),
        }
    )
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _find_reusable_pending_optimization(
    *,
    output_root: Path,
    eval_ref_path: str,
    analysis_result_path: str,
    harness_refs_path: str,
) -> str:
    """Return the newest verified candidate awaiting a gate for identical inputs."""
    expected = {
        "eval_ref_path": str(Path(eval_ref_path).expanduser().resolve()),
        "analysis_result_path": str(Path(analysis_result_path).expanduser().resolve()),
        "source_harness_refs_path": str(Path(harness_refs_path).expanduser().resolve()),
    }
    candidates = sorted(
        output_root.glob("member_optimization_*/member_optimization_ref.yaml"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for ref_path in candidates:
        try:
            payload = yaml.safe_load(ref_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if payload.get("status") not in {"success", "partial_success"}:
            continue
        if payload.get("promotion_status") != "pending_gate":
            continue
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            continue
        actual = {key: str(Path(str(metadata.get(key, ""))).expanduser().resolve()) for key in expected}
        if actual != expected:
            continue
        candidate_refs = Path(str(payload.get("optimized_harness_refs_path", ""))).expanduser()
        if candidate_refs.is_file():
            return str(ref_path.resolve())
    return ""


def _allocate_optimization_dir(output_root: Path) -> Path:
    """Allocate the next member_optimization_XXX directory."""
    index = 1
    while True:
        candidate = output_root / f"member_optimization_{index:03d}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
        index += 1


def _short_id() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def _write_execution_results(path: Path, results: Any) -> None:
    """Write execution results to JSON."""
    import json
    from dataclasses import asdict as _asdict

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"results": [_asdict(r) for r in results]}, f, ensure_ascii=False, indent=2)


def _write_stage_error(path: Path, *, stage: str, error: Exception, details: dict[str, Any]) -> None:
    """Persist a stage failure with enough context for rerun debugging."""
    import json

    payload = {
        "stage": stage,
        "error_type": type(error).__name__,
        "error": str(error),
        "details": details,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _read_input_harness_refs(harness_refs_path: str) -> dict[str, str]:
    """Read input harness refs as a simple role->path mapping."""
    if not harness_refs_path:
        return {}
    import json

    p = Path(harness_refs_path).expanduser().resolve()
    if not p.exists():
        return {}
    if p.is_dir():
        return {entry.name: str(entry.resolve()) for entry in sorted(p.iterdir()) if entry.is_dir()}
    try:
        with open(p, encoding="utf-8") as f:
            if p.suffix.lower() in {".yaml", ".yml"}:
                data = yaml.safe_load(f) or {}
            else:
                data = json.load(f)
        if not isinstance(data, dict):
            return {}
        refs = data.get("harness_refs", {})
        roles = data.get("roles", [])
        role_refs: dict[str, str] = {}
        if isinstance(roles, list):
            for role_entry in roles:
                if not isinstance(role_entry, dict):
                    continue
                role = str(role_entry.get("role", "") or "")
                ref = role_entry.get("harness_ref_path", "")
                if not ref and isinstance(refs, dict):
                    ref = refs.get(role, "")
                if role and isinstance(ref, str):
                    role_refs[role] = _resolve_harness_ref_value(ref, p)
        if role_refs:
            return role_refs
        if isinstance(refs, dict):
            resolved_refs: dict[str, str] = {}
            for key, value in refs.items():
                normalized_key = str(key)
                if isinstance(value, str) and normalized_key:
                    resolved_refs[normalized_key] = _resolve_harness_ref_value(value, p)
            return resolved_refs
        resolved_refs = {}
        metadata_keys = {"version", "source", "source_harness_refs_path"}
        for role, ref in data.items():
            normalized_role = str(role)
            if isinstance(ref, str) and normalized_role not in metadata_keys:
                resolved_refs[normalized_role] = _resolve_harness_ref_value(ref, p)
        return resolved_refs
    except Exception:
        return {}


def _resolve_harness_ref_value(value: str, refs_path: Path) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    return str((refs_path.parent / path).resolve())


def _validate_candidate_harness_refs(candidate_roles: list[Any]) -> None:
    """Require every resolved candidate role to point at an existing harness."""
    errors: list[str] = []
    for candidate in candidate_roles:
        role = getattr(candidate, "role", "") or getattr(candidate, "member_name", "") or "<unknown>"
        ref = str(getattr(candidate, "harness_ref_path", "") or "").strip()
        if not ref:
            errors.append(f"role '{role}' has empty harness_ref_path")
            continue
        ref_path = Path(ref).expanduser()
        if not ref_path.exists():
            errors.append(f"role '{role}' harness_ref_path does not exist: {ref_path}")

    if errors:
        raise RuntimeError(
            "MemberOptimizer requires pre-initialized role ExpertHarness refs. "
            "Create the minimal role harness before optimization. " + "; ".join(errors)
        )


def _write_current_harness_refs(
    request: _HarnessRefsWriteRequest,
) -> Path:
    """Write the run-scoped current_harness_refs.yaml.

    Per spec Section 4.11 and design.md Section 5.2.
    """
    output_root = request.output_root
    harness_refs_path = request.harness_refs_path
    published_roles = request.published_roles
    failed_roles = request.failed_roles
    skipped_roles = request.skipped_roles
    role_results = request.role_results
    path_layout = request.path_layout
    published_ref_paths = request.published_ref_paths
    cumulative_publish = request.cumulative_publish
    output_root.mkdir(parents=True, exist_ok=True)
    refs_path = request.refs_path or (output_root / "current_harness_refs.yaml")
    current_harnesses_dir = output_root / "current_harnesses"

    harness_refs: dict[str, str] = _read_input_harness_refs(harness_refs_path)
    for role in published_roles:
        if published_ref_paths and published_ref_paths.get(role):
            harness_refs[role] = published_ref_paths[role]
        elif path_layout is not None:
            path_layout.write_role_mapping(role)
            harness_refs[role] = str(path_layout.current_harness_dir(role))
        else:
            harness_refs[role] = str(current_harnesses_dir / role)
    for role in failed_roles + skipped_roles:
        if role not in harness_refs:
            harness_refs[role] = ""

    role_results_out = {}
    for role, rr in role_results.items():
        role_results_out[role] = {
            "status": rr.status,
            "before_harness_ref_path": rr.before_harness_ref_path,
            "after_harness_ref_path": rr.after_harness_ref_path,
        }

    source_payload: dict[str, Any] = {}
    source_path = Path(harness_refs_path).expanduser()
    if source_path.is_file():
        loaded = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            source_payload = loaded

    role_identities: list[dict[str, Any]] = []
    source_roles = source_payload.get("roles", [])
    if isinstance(source_roles, list):
        for source_role in source_roles:
            if not isinstance(source_role, dict):
                continue
            role_identity = dict(source_role)
            member_name = str(role_identity.get("member_name", "") or "")
            role_name = str(role_identity.get("role", "") or "")
            ref = harness_refs.get(member_name) or harness_refs.get(role_name)
            if ref:
                role_identity["harness_ref_path"] = ref
            metadata = dict(role_identity.get("metadata") or {})
            metadata.setdefault("member_id", member_name or role_name)
            aliases = metadata.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            metadata["aliases"] = list(
                dict.fromkeys(
                    [
                        *[str(alias) for alias in aliases if str(alias).strip()],
                        role_name,
                        member_name,
                    ]
                )
            )
            role_identity["metadata"] = metadata
            role_identities.append(role_identity)

    effective_published_roles = list(published_roles)
    effective_role_results = dict(role_results_out)
    if cumulative_publish:
        previous_published = source_payload.get("published_roles", [])
        if isinstance(previous_published, list):
            effective_published_roles = list(
                dict.fromkeys(
                    [
                        *[str(role) for role in previous_published if str(role).strip()],
                        *published_roles,
                    ]
                )
            )
        previous_results = source_payload.get("role_results", {})
        if isinstance(previous_results, dict):
            effective_role_results = {**previous_results, **role_results_out}

    payload = {
        "version": 1,
        "source_harness_refs_path": harness_refs_path,
        "harness_refs": harness_refs,
        "published_roles": effective_published_roles,
        "candidate_ready_roles": effective_published_roles,
        "staged_roles": effective_published_roles,
        "verified_roles": effective_published_roles,
        "promoted_roles": [],
        "promotion_status": "pending_gate" if effective_published_roles else "not_applicable",
        "failed_roles": [] if cumulative_publish else failed_roles,
        "skipped_roles": [] if cumulative_publish else skipped_roles,
        "role_results": effective_role_results,
    }
    if role_identities:
        payload["roles"] = role_identities
    if cumulative_publish:
        payload["last_attempt"] = {
            "published_roles": published_roles,
            "failed_roles": failed_roles,
            "skipped_roles": skipped_roles,
            "role_results": role_results_out,
        }

    with open(refs_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

    return refs_path


def _is_action_plan_rejection(error: Exception) -> bool:
    """Treat an LLM plan that fails the action contract as a rejected candidate."""
    return isinstance(error, RuntimeError) and str(error).startswith("Member action plan validation failed:")


__all__ = [
    "MemberOptimizer",
]
