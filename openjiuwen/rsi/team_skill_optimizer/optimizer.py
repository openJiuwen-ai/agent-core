# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline Team Skill optimizer facade for auto-coordinating harness."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.rsi.config import TeamSkillOptimizerConfig
from openjiuwen.rsi.team_skill_optimizer.analysis import (
    load_team_skill_issues_from_analysis,
)
from openjiuwen.rsi.team_skill_optimizer.artifacts import (
    allocate_optimization_dir,
    existing_current_or_source,
    prepare_candidate_workspace,
    publish_candidate,
    resolve_source_skill,
    write_current_ref,
    write_metadata,
    write_yaml,
)
from openjiuwen.rsi.team_skill_optimizer.evolution_pipeline import (
    evolve_and_rebuild,
    read_candidate_skill_content,
)
from openjiuwen.rsi.team_skill_optimizer.signals import (
    build_signals,
    build_user_query,
    issue_ids,
)
from openjiuwen.rsi.team_skill_optimizer.trajectory_loader import (
    load_offline_trajectory,
    trajectory_trace_paths,
)


class TeamSkillOptimizer:
    """Adapt analyzer-attributed Team Skill issues into offline evolution."""

    def __init__(self, config: TeamSkillOptimizerConfig) -> None:
        self.config = config

    async def optimize(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        team_skill_ref_path: str,
        output_dir: str,
    ) -> str:
        """Optimize a Team Skill and return the stable published skill directory."""
        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)

        self._validate_config()
        if self.config.freeze:
            return self._write_noop(
                output_root=output_root,
                status="frozen",
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                team_skill_ref_path=team_skill_ref_path,
                returned_path=existing_current_or_source(output_root, team_skill_ref_path),
            )

        source_ref = resolve_source_skill(team_skill_ref_path)
        run_dir = allocate_optimization_dir(output_root)
        issue_analysis = load_team_skill_issues_from_analysis(analysis_result_path)
        selected_issue_ids = issue_ids(issue_analysis.issues)
        if not issue_analysis.issues:
            returned_path = existing_current_or_source(output_root, str(source_ref.skill_dir))
            write_metadata(
                run_dir=run_dir,
                status="no_team_skill_issues",
                config=self.config,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                team_skill_ref_path=team_skill_ref_path,
                source_skill_ref=source_ref,
                selected_issue_ids=selected_issue_ids,
                skipped_warnings=issue_analysis.skipped_warnings,
                source_issues_path=issue_analysis.issues_path,
                returned_path=returned_path,
            )
            return returned_path

        artifacts = prepare_candidate_workspace(
            output_root=output_root,
            run_dir=run_dir,
            source_ref=source_ref,
        )
        skill_content = await read_candidate_skill_content(
            artifacts.candidate_root,
            source_ref.skill_name,
        )
        signals = build_signals(
            issues=issue_analysis.issues,
            skill_name=source_ref.skill_name,
            skill_content=skill_content,
            eval_ref_path=eval_ref_path,
            analysis_result_path=analysis_result_path,
        )
        user_query = build_user_query(issue_analysis.issues)
        trajectory = load_offline_trajectory(eval_ref_path, issue_analysis.issues)
        trace_paths = trajectory_trace_paths(trajectory)
        write_yaml(artifacts.run_dir / "signals.yaml", {"signals": [signal.to_dict() for signal in signals]})

        try:
            pipeline_result = await evolve_and_rebuild(
                config=self.config,
                candidate_root=artifacts.candidate_root,
                candidate_skill_dir=artifacts.candidate_skill_dir,
                run_dir=artifacts.run_dir,
                skill_name=source_ref.skill_name,
                signals=signals,
                user_query=user_query,
                trajectory=trajectory,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
            )
        except RuntimeError as exc:
            write_metadata(
                run_dir=artifacts.run_dir,
                status="failed",
                config=self.config,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                team_skill_ref_path=team_skill_ref_path,
                source_skill_ref=source_ref,
                selected_issue_ids=selected_issue_ids,
                skipped_warnings=issue_analysis.skipped_warnings,
                source_issues_path=issue_analysis.issues_path,
                signal_count=len(signals),
                candidate_path=str(artifacts.candidate_skill_dir),
                trajectory_trace_paths=trace_paths,
                error=str(exc),
            )
            raise

        if not pipeline_result.record_ids:
            returned_path = existing_current_or_source(output_root, str(source_ref.skill_dir))
            write_metadata(
                run_dir=artifacts.run_dir,
                status="no_records_generated",
                config=self.config,
                eval_ref_path=eval_ref_path,
                analysis_result_path=analysis_result_path,
                team_skill_ref_path=team_skill_ref_path,
                source_skill_ref=source_ref,
                selected_issue_ids=selected_issue_ids,
                skipped_warnings=issue_analysis.skipped_warnings,
                source_issues_path=issue_analysis.issues_path,
                signal_count=len(signals),
                request_id=pipeline_result.request_id,
                candidate_path=str(artifacts.candidate_skill_dir),
                returned_path=returned_path,
                trajectory_trace_paths=trace_paths,
            )
            return returned_path

        publish_candidate(artifacts.candidate_skill_dir, artifacts.publish_dir)
        returned_path = str(artifacts.publish_dir.resolve())
        optimization_ref_path = artifacts.run_dir / "team_skill_optimization_ref.yaml"
        write_metadata(
            run_dir=artifacts.run_dir,
            status="success",
            config=self.config,
            eval_ref_path=eval_ref_path,
            analysis_result_path=analysis_result_path,
            team_skill_ref_path=team_skill_ref_path,
            source_skill_ref=source_ref,
            selected_issue_ids=selected_issue_ids,
            skipped_warnings=issue_analysis.skipped_warnings,
            source_issues_path=issue_analysis.issues_path,
            signal_count=len(signals),
            request_id=pipeline_result.request_id,
            record_ids=pipeline_result.record_ids,
            applied_count=pipeline_result.applied_count,
            candidate_path=str(artifacts.candidate_skill_dir),
            returned_path=returned_path,
            trajectory_trace_paths=trace_paths,
        )
        write_current_ref(
            output_root=output_root,
            returned_path=returned_path,
            optimization_ref_path=str(optimization_ref_path.resolve()),
        )
        return returned_path

    def _validate_config(self) -> None:
        if self.config.max_candidates != 1:
            raise RuntimeError(
                "team_skill_optimizer.max_candidates > 1 is not supported in v1; "
                "multi-candidate selector/verifier is not implemented"
            )
        if not self.config.freeze and not str(self.config.model_config_ref or "").strip():
            raise RuntimeError("team_skill_optimizer.model_config_ref is required")

    def _write_noop(
        self,
        *,
        output_root: Path,
        status: str,
        eval_ref_path: str,
        analysis_result_path: str,
        team_skill_ref_path: str,
        returned_path: str,
    ) -> str:
        run_dir = allocate_optimization_dir(output_root)
        write_metadata(
            run_dir=run_dir,
            status=status,
            config=self.config,
            eval_ref_path=eval_ref_path,
            analysis_result_path=analysis_result_path,
            team_skill_ref_path=team_skill_ref_path,
            returned_path=returned_path,
        )
        return returned_path


__all__ = [
    "TeamSkillOptimizer",
]
