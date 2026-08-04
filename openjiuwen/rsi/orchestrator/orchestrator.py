# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Top-level orchestration entry point."""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from openjiuwen.core.common.logging import logger
from openjiuwen.rsi.artifact_io import (
    read_json_mapping,
    read_yaml_mapping,
    write_json_mapping,
    write_yaml_mapping,
)
from openjiuwen.rsi.config import (
    AutoCoordinatingHarnessConfig,
    load_auto_coordinating_harness_config,
)
from openjiuwen.rsi.data_loader import DataLoader
from openjiuwen.rsi.dataset_curator import DatasetCurator
from openjiuwen.rsi.dataset_generator import DatasetGenerator
from openjiuwen.rsi.evaluation_result_analyzer import (
    EvaluationResultAnalyzer,
)
from openjiuwen.rsi.evaluator import TeamEvaluator
from openjiuwen.rsi.evaluator.team_factory import (
    TeamSkillTeamFactory,
    resolve_team_name_from_skill_path,
)
from openjiuwen.rsi.evaluator.trajectory_usage import (
    collect_jsonl_successful_usage,
    collect_successful_tool_names,
)
from openjiuwen.rsi.member_optimizer import MemberOptimizer
from openjiuwen.rsi.optimization_experience_learner import (
    OptimizationExperienceLearner,
)
from openjiuwen.rsi.orchestrator.checkpoint import CheckpointManager
from openjiuwen.rsi.orchestrator.context import OrchestratorContextStore
from openjiuwen.rsi.orchestrator.initial_harness_builder import (
    build_initial_harness_refs_from_team_skill,
)
from openjiuwen.rsi.orchestrator.workspace_paths import (
    OrchestratorWorkspacePaths,
)
from openjiuwen.rsi.progress import (
    OptimizationProgressEvent,
    ProgressCallback,
    SeedOptimizationDecisionCallback,
)
from openjiuwen.rsi.schema import (
    BatchOptimizationResult,
    CaseMapping,
    CurrentArtifactRefs,
    DatasetArtifact,
    EvaluationHistoryItem,
    EvaluationResultAnalysisInvocation,
    MemberOptimizationHistoryItem,
    OrchestratorPhase,
    OrchestratorRunContext,
    RunStrategyMetadata,
    TeamSkillOptimizationHistoryItem,
)
from openjiuwen.rsi.team_skill_generator import TeamSkillGenerator
from openjiuwen.rsi.team_skill_optimizer import TeamSkillOptimizer
from openjiuwen.rsi.usage_recorder import (
    activate_llm_usage_ledger,
    llm_usage_scope,
    reset_llm_usage_ledger,
    summarize_llm_usage_file,
)


class OptimizationOrchestrator:
    """Single coordinator for the end-to-end optimization workflow."""

    def __init__(
        self,
        config_path: str,
    ) -> None:
        self.config_path = config_path
        config = load_auto_coordinating_harness_config(config_path)
        self.config: AutoCoordinatingHarnessConfig = config
        self.data_loader = DataLoader(config.data_loader)
        self.dataset_curator = DatasetCurator(config.dataset_curation)
        self.dataset_generator = DatasetGenerator(config.dataset_generator)
        self.evaluator = TeamEvaluator(config.evaluator)
        self.optimization_experience_learner = OptimizationExperienceLearner(config.optimization_experience_learner)
        self.evaluation_result_analyzer = EvaluationResultAnalyzer(
            config.evaluation_result_analyzer,
            experience_learner=self.optimization_experience_learner,
        )
        self.team_skill_optimizer = TeamSkillOptimizer(config.team_skill_optimizer)
        self.member_optimizer = MemberOptimizer(config.member_optimizer)
        self.workspace_paths = OrchestratorWorkspacePaths(config.workspace_dir)
        self.context_store = OrchestratorContextStore(str(self.workspace_paths.context_path))
        self.checkpoint_manager = CheckpointManager(str(self.workspace_paths.checkpoint_dir))
        self.team_factory = TeamSkillTeamFactory(config=config.evaluator)
        self.team_skill_generator = TeamSkillGenerator(model_config_ref=config.team_skill_optimizer.model_config_ref)
        self.current_dataset_artifact: DatasetArtifact | None = None
        self.current_eval_ref_paths: list[str] = []
        self.current_analysis_ref_paths: list[str] = []
        self.analysis_ref_by_eval_ref_path: dict[str, str] = {}
        self.optimized_team_skill_ref_path: str | None = None
        self.optimized_eval_ref_paths: list[str] = []
        self.member_optimization_ref_path: str | None = None
        self.optimized_harness_refs_path: str | None = None
        self.experience_ref_paths: list[str] = []
        self.dataset_curation_ref_paths: list[str] = []
        self._epoch_experience_ref_paths: list[str] = []
        self.team_skill_locked = False
        self._team_skill_no_improvement_epochs = 0
        self._team_skill_optimized_in_epoch = False
        self.initial_harness_refs_path: str | None = None
        self.resume_enabled = False
        self.llm_usage_path = self.workspace_paths.base_root / "llm_usage.jsonl"
        self.llm_usage_run_id = ""
        self._progress_callback: ProgressCallback | None = None
        self._current_epoch = 0
        self._current_batch_index = 0
        self._reused_best_context_path: str | None = None
        self._published_context_path = ""
        self._workspace_run_id = ""

    def _emit(self, **kwargs: Any) -> None:
        """Forward a progress event to an optional observer without affecting the run."""
        callback = self._progress_callback
        if callback is None:
            return
        try:
            callback(OptimizationProgressEvent(**kwargs))
        except Exception:
            logger.exception("[auto_coordinating_harness] progress callback error (ignored)")

    async def run(  # pylint: disable=huawei-too-many-arguments
        self,
        task: str,
        dataset_dir: str | None = None,
        team_skill_ref_path: str = "",
        harness_refs_path: str = "",
        resume: bool = False,
        reuse_best_context: bool = False,
        published_context_path: str = "",
        workspace_run_id: str = "",
        progress_callback: ProgressCallback | None = None,
        seed_optimization_decision_callback: SeedOptimizationDecisionCallback | None = None,
    ) -> str:
        """Prepare data and run evaluation batch-by-batch."""
        self._progress_callback = progress_callback
        self._emit(
            phase=OrchestratorPhase.INITIALIZING.value,
            message="initializing auto-coordinating harness run",
        )
        self.llm_usage_path = self.workspace_paths.base_root / "llm_usage.jsonl"
        local_now = datetime.now(UTC).astimezone()
        self.llm_usage_run_id = f"ach_{local_now.strftime('%Y%m%d%H%M%S%f')}"
        usage_token = activate_llm_usage_ledger(
            self.llm_usage_path,
            run_id=self.llm_usage_run_id,
        )
        self.current_eval_ref_paths = []
        self.optimized_eval_ref_paths = []
        self.current_analysis_ref_paths = []
        self.analysis_ref_by_eval_ref_path = {}
        self._reused_best_context_path = None
        self._published_context_path = str(published_context_path or "").strip()
        self._workspace_run_id = str(workspace_run_id or "").strip()
        self.resume_enabled = resume
        resume_context = self._load_resume_context() if resume else None
        best_context = (
            self._load_best_context()
            if reuse_best_context
            and resume_context is None
            and not str(team_skill_ref_path or "").strip()
            and not str(harness_refs_path or "").strip()
            else None
        )
        if resume_context is not None:
            self._restore_workspace_from_context(resume_context)
            team_skill_ref_path = team_skill_ref_path or resume_context.current.team_skill_ref_path
            harness_refs_path = harness_refs_path or resume_context.current.harness_refs_path
        elif best_context is not None:
            team_skill_ref_path = best_context.best.team_skill_ref_path or best_context.current.team_skill_ref_path
            harness_refs_path = best_context.best.harness_refs_path or best_context.current.harness_refs_path
            self._reused_best_context_path = best_context.context_path
        # A standalone single-harness run already has its complete execution
        # identity in harness_refs. Generating a Team Skill here would add Team
        # semantics and an extra evaluation stage to an otherwise isolated flow.
        if not (self.config.evaluator.backend == "single_harness" and str(harness_refs_path or "").strip()):
            with llm_usage_scope(
                stage="team_skill_generation",
                operation="ensure_initial_team_skill",
            ):
                team_skill_ref_path = await self._ensure_initial_team_skill(
                    task=task,
                    team_skill_ref_path=team_skill_ref_path,
                )
        # _restore_workspace_from_context() already selected the exact run-scoped
        # workspace. Reconfiguring by Team name here would silently switch a
        # resumed ``team--schedule`` run into the legacy shared ``team`` folder.
        if resume_context is None:
            self._configure_team_workspace(
                team_skill_ref_path,
                workspace_run_id=self._workspace_run_id,
            )
        harness_refs_path = self._ensure_initial_member_harness_refs(
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
        )
        seed_evaluation: dict[str, Any] | None = None
        resumed_dataset = (
            self._resume_dataset_artifact(resume_context) if resume_context is not None and not dataset_dir else None
        )
        if resume_context is not None and not dataset_dir and resumed_dataset is None:
            seed_evaluation = self._resume_seed_evaluation(resume_context)
            if seed_evaluation is not None:
                seed_score = _numeric(seed_evaluation.get("score"))
                self._emit(
                    phase=OrchestratorPhase.EVALUATING.value,
                    stage="seed_evaluation",
                    score=seed_score,
                    message=(
                        "seed evaluation reused: "
                        f"score={_fmt_score(seed_score)} "
                        f"status={seed_evaluation.get('status', 'unknown')}"
                    ),
                    metrics={
                        "seed_score": seed_score,
                        "quality_gap_count": seed_evaluation.get("quality_gap_count"),
                        "dataset_generation_skipped": seed_evaluation.get("dataset_generation_skipped"),
                    },
                    artifacts={
                        "eval_ref_path": seed_evaluation.get("eval_ref_path", ""),
                        "targeted_dataset_seed_file": seed_evaluation.get("targeted_dataset_seed_file", ""),
                    },
                )
            if seed_evaluation is not None and seed_evaluation.get("dataset_generation_skipped") is True:
                self._save_epoch_context(0, OrchestratorPhase.COMPLETED)
                self._write_run_report()
                reset_llm_usage_ledger(usage_token)
                self._emit(
                    phase=OrchestratorPhase.COMPLETED.value,
                    message="seed evaluation passed; dataset generation skipped",
                    artifacts={"workspace_dir": str(self.workspace_paths.root)},
                )
                self._progress_callback = None
                return str(self.workspace_paths.root)
            if seed_evaluation is not None and not await self._should_continue_after_seed(
                seed_evaluation,
                seed_optimization_decision_callback,
            ):
                reset_llm_usage_ledger(usage_token)
                self._progress_callback = None
                return str(self.workspace_paths.root)
            seed_ref = str((seed_evaluation or {}).get("targeted_dataset_seed_file", "") or "")
            if seed_ref:
                self.dataset_generator.config = replace(
                    self.dataset_generator.config,
                    known_failures_ref=seed_ref,
                )
        should_run_seed_evaluation = (
            seed_evaluation is None
            and not dataset_dir
            and resumed_dataset is None
            and self.config.seed_evaluation.enabled
        )
        if should_run_seed_evaluation:
            with llm_usage_scope(
                stage="seed_evaluation",
                operation="evaluate_original_task",
            ):
                self._emit(
                    phase=OrchestratorPhase.EVALUATING.value,
                    stage="seed_evaluation",
                    message="seed evaluation started",
                    artifacts={"workspace_dir": str(self.workspace_paths.root)},
                )
                seed_evaluation = await self._run_seed_evaluation(
                    task=task,
                    team_skill_ref_path=team_skill_ref_path,
                    harness_refs_path=harness_refs_path,
                )
                seed_score = _numeric(seed_evaluation.get("score"))
                self._emit(
                    phase=OrchestratorPhase.EVALUATING.value,
                    stage="seed_evaluation",
                    score=seed_score,
                    improved=not bool(seed_evaluation.get("dataset_generation_skipped")),
                    message=(
                        "seed evaluation completed: "
                        f"score={_fmt_score(seed_score)} "
                        f"status={seed_evaluation.get('status', 'unknown')}"
                    ),
                    metrics={
                        "seed_score": seed_score,
                        "quality_gap_count": seed_evaluation.get("quality_gap_count"),
                        "dataset_generation_skipped": seed_evaluation.get("dataset_generation_skipped"),
                    },
                    artifacts={
                        "eval_ref_path": seed_evaluation.get("eval_ref_path", ""),
                        "targeted_dataset_seed_file": seed_evaluation.get("targeted_dataset_seed_file", ""),
                    },
                )
            if seed_evaluation.get("dataset_generation_skipped") is True:
                self._save_epoch_context(0, OrchestratorPhase.COMPLETED)
                self._write_run_report()
                reset_llm_usage_ledger(usage_token)
                self._emit(
                    phase=OrchestratorPhase.COMPLETED.value,
                    message="seed evaluation passed; dataset generation skipped",
                    artifacts={"workspace_dir": str(self.workspace_paths.root)},
                )
                self._progress_callback = None
                return str(self.workspace_paths.root)
            if not await self._should_continue_after_seed(
                seed_evaluation,
                seed_optimization_decision_callback,
            ):
                reset_llm_usage_ledger(usage_token)
                self._progress_callback = None
                return str(self.workspace_paths.root)
            seed_ref = str(seed_evaluation.get("targeted_dataset_seed_file", "") or "")
            if seed_ref:
                self.dataset_generator.config = replace(
                    self.dataset_generator.config,
                    known_failures_ref=seed_ref,
                )
        reused_dataset = False
        if dataset_dir:
            loaded_dataset_dir = Path(dataset_dir).expanduser().resolve()
            dataset_artifact = self._build_dataset_artifact_from_loaded_dir(loaded_dataset_dir)
            self.current_dataset_artifact = None
        elif resumed_dataset is not None:
            dataset_artifact = resumed_dataset
            loaded_dataset_dir = Path(dataset_artifact.dataset_dir).expanduser().resolve()
            self.current_dataset_artifact = dataset_artifact
            reused_dataset = True
        else:
            with llm_usage_scope(
                stage="dataset_generation",
                operation="generate_dataset",
            ):
                self._emit(
                    phase=OrchestratorPhase.GENERATING_DATASET.value,
                    stage="dataset_generation",
                    message="dataset generation started",
                    artifacts={"workspace_dir": str(self.workspace_paths.root)},
                )
                dataset_artifact = await self.dataset_generator.generate(
                    task,
                    str(self.workspace_paths.allocate_dataset_dir()),
                )
            loaded_dataset_dir = Path(dataset_artifact.dataset_dir)
            self.current_dataset_artifact = dataset_artifact
        self._emit(
            phase=OrchestratorPhase.GENERATING_DATASET.value,
            message=(f"dataset ready: {dataset_artifact.dataset_id or Path(dataset_artifact.dataset_dir).name}"),
            artifacts={"dataset_dir": str(loaded_dataset_dir)},
        )

        if not reused_dataset:
            self._save_dataset_context(
                task,
                dataset_artifact,
                team_skill_ref_path=team_skill_ref_path,
                harness_refs_path=harness_refs_path,
                seed_evaluation=seed_evaluation,
            )

        current_team_skill_ref_path = team_skill_ref_path
        current_harness_refs_path = harness_refs_path
        if resume_context is not None:
            current_team_skill_ref_path = resume_context.current.team_skill_ref_path or current_team_skill_ref_path
            current_harness_refs_path = resume_context.current.harness_refs_path or current_harness_refs_path
        resume_position = _resume_eval_position(resume_context.current.eval_ref_path) if resume_context else None
        for epoch in range(1, self.config.max_epochs + 1):
            self._epoch_experience_ref_paths = []
            self._team_skill_optimized_in_epoch = False
            self._current_epoch = epoch
            self._save_epoch_context(epoch, OrchestratorPhase.EVALUATING)
            self._emit(
                phase=OrchestratorPhase.EVALUATING.value,
                epoch=epoch,
                message=f"epoch {epoch} evaluation started",
            )
            epoch_eval_ref_paths: list[str] = []
            for batch_index, batch in enumerate(self._iter_dataset_batches(loaded_dataset_dir, epoch=epoch), start=1):
                self._current_batch_index = batch_index
                completed_batch = self._resume_completed_batch(epoch=epoch, batch_index=batch_index)
                if completed_batch is not None:
                    epoch_eval_ref_paths.extend(completed_batch.eval_ref_paths)
                    continue
                if _resume_should_skip_batch(resume_position, epoch=epoch, batch_index=batch_index):
                    continue
                batch_result = await self._run_batch_optimization(
                    batch=batch,
                    batch_index=batch_index,
                    epoch=epoch,
                    team_skill_ref_path=current_team_skill_ref_path,
                    harness_refs_path=current_harness_refs_path,
                    dataset=dataset_artifact,
                )
                current_team_skill_ref_path = batch_result.team_skill_ref_path
                current_harness_refs_path = batch_result.harness_refs_path
                epoch_eval_ref_paths.extend(batch_result.eval_ref_paths)
                self._save_completed_batch_context(
                    epoch=epoch,
                    batch_index=batch_index,
                    result=batch_result,
                )

            self.current_eval_ref_paths = epoch_eval_ref_paths
            if not self.config.scheduling.full_evaluation_enabled:
                terminal_eval_ref_path = epoch_eval_ref_paths[-1] if epoch_eval_ref_paths else ""
                promotion_source = "batch_terminal_without_full_evaluation"
                accepted_candidate_eval_ref_path = self._accepted_candidate_eval_ref_path(
                    harness_refs_path=current_harness_refs_path,
                )
                if accepted_candidate_eval_ref_path:
                    terminal_eval_ref_path = accepted_candidate_eval_ref_path
                    promotion_source = "accepted_candidate_gate_without_full_evaluation"
                self._record_full_evaluation_skipped(
                    epoch=epoch,
                    eval_ref_path=terminal_eval_ref_path,
                )
                terminal_score = _eval_score(terminal_eval_ref_path) if terminal_eval_ref_path else None
                self._save_epoch_checkpoint(
                    epoch=epoch,
                    eval_ref_path=terminal_eval_ref_path,
                    score=terminal_score,
                    promotion_source=promotion_source,
                    force_best=True,
                )
                await self._finalize_epoch_experiences(
                    epoch=epoch,
                    eval_ref_path=terminal_eval_ref_path,
                    score=terminal_score,
                    improved=True,
                    confirmation_mode=promotion_source,
                )
                continue
            with llm_usage_scope(
                stage=f"epoch_{epoch}.full_evaluation",
                operation="evaluate_epoch_dataset",
            ):
                epoch_eval_ref_path = await self._evaluate_epoch_dataset(
                    epoch=epoch,
                    team_skill_ref_path=current_team_skill_ref_path,
                    harness_refs_path=current_harness_refs_path,
                    dataset=dataset_artifact,
                    dataset_dir=loaded_dataset_dir,
                )
            epoch_score = _eval_score(epoch_eval_ref_path)
            previous_best_score = self.context_store.load().best.score
            epoch_improved = epoch_score is not None and (
                previous_best_score is None or epoch_score > previous_best_score
            )
            self._save_epoch_checkpoint(
                epoch=epoch,
                eval_ref_path=epoch_eval_ref_path,
                score=epoch_score,
            )
            await self._finalize_epoch_experiences(
                epoch=epoch,
                eval_ref_path=epoch_eval_ref_path,
                score=epoch_score,
                improved=epoch_improved,
            )
            await self._curate_epoch_replay_dataset(
                epoch=epoch,
                eval_ref_path=epoch_eval_ref_path,
            )
            if self._should_stop_optimization(epoch_score=epoch_score, epoch=epoch):
                break

        self._save_epoch_context(self.context_store.load().epoch, OrchestratorPhase.COMPLETED)
        accepted_change_count = self._record_optimization_outcome()
        published = self._publish_best_context()
        self._write_run_report()
        best_score = self.context_store.load().best.score
        completion_message = (
            f"optimization completed and published: best_score={_fmt_score(best_score)}"
            if published
            else (
                f"optimization completed with {accepted_change_count} accepted change(s): "
                f"best_score={_fmt_score(best_score)}"
                if accepted_change_count
                else "optimization completed without an accepted change"
            )
        )
        self._emit(
            phase=OrchestratorPhase.COMPLETED.value,
            message=completion_message,
            metrics={
                "best_score": best_score,
                "epochs_run": self.context_store.load().epoch,
                "accepted_change_count": accepted_change_count,
                "published": published,
            },
            artifacts={"dataset_dir": str(loaded_dataset_dir)},
        )
        reset_llm_usage_ledger(usage_token)
        self._progress_callback = None
        return str(loaded_dataset_dir)

    async def _run_seed_evaluation(
        self,
        *,
        task: str,
        team_skill_ref_path: str,
        harness_refs_path: str,
    ) -> dict[str, Any]:
        """Run the original task once before generating synthetic training data."""
        self._save_initial_context(
            task,
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
        )
        seed_case = _seed_case_from_task(
            task,
            pass_threshold=self.config.seed_evaluation.pass_threshold,
        )
        eval_dir = self.workspace_paths.evaluation_dir() / "seed"
        eval_dir.mkdir(parents=True, exist_ok=True)
        eval_ref_path = await self.evaluator.evaluate_batch(
            cases=[seed_case],
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
            output_dir=str(eval_dir),
            context_path=self.context_store.context_path,
            dataset=None,
        )
        score = _eval_score(eval_ref_path)
        passed = score >= self.config.seed_evaluation.pass_threshold
        metadata: dict[str, Any] = {
            "status": "passed" if passed else "failed",
            "score": score,
            "pass_threshold": self.config.seed_evaluation.pass_threshold,
            "excellent_threshold": self.config.seed_evaluation.excellent_threshold,
            "eval_ref_path": eval_ref_path,
        }
        feedback = _seed_feedback_from_eval(
            eval_ref_path,
            max_cases=self.config.seed_evaluation.max_cases,
            include_default_gap=False,
        )
        seed_is_excellent = (
            score >= self.config.seed_evaluation.excellent_threshold
            and not feedback["quality_gaps"]
            and not feedback.get("runtime_blockers", [])
        )
        if seed_is_excellent:
            metadata["dataset_generation_skipped"] = True
            self._record_seed_evaluation(metadata)
            return metadata

        if not feedback["quality_gaps"] and _has_seed_judge_runtime_blocker(feedback):
            metadata.update(
                {
                    "dataset_generation_skipped": True,
                    "runtime_blockers": feedback.get("runtime_blockers", []),
                }
            )
            self._record_seed_evaluation(metadata)
            return metadata

        if not feedback["quality_gaps"]:
            feedback = _seed_feedback_from_eval(
                eval_ref_path,
                max_cases=self.config.seed_evaluation.max_cases,
            )
        seed_payload = {
            "source": "seed_evaluation",
            "task": task,
            "seed_score": score,
            "source_eval_ref_path": eval_ref_path,
            "quality_gaps": feedback["quality_gaps"],
            "dataset_budget": feedback["dataset_budget"],
            "recommended_synthetic_tasks": feedback["recommended_synthetic_tasks"],
            "runtime_blockers": feedback.get("runtime_blockers", []),
        }
        seed_ref = write_json_mapping(
            self.workspace_paths.root / "datasets" / "seed_evaluation" / "targeted_dataset_seed.json",
            seed_payload,
        )
        metadata.update(
            {
                "dataset_generation_skipped": False,
                "targeted_dataset_seed_file": seed_ref,
                "quality_gap_count": len(feedback["quality_gaps"]),
                "dataset_budget": feedback["dataset_budget"],
            }
        )
        self._record_seed_evaluation(metadata)
        return metadata

    async def _should_continue_after_seed(
        self,
        seed_evaluation: dict[str, Any],
        callback: SeedOptimizationDecisionCallback | None,
    ) -> bool:
        """Ask an optional observer whether to continue from seed into optimization."""
        if callback is None:
            return True
        seed_score = _numeric(seed_evaluation.get("score"))
        self._emit(
            phase=OrchestratorPhase.PAUSED.value,
            stage="optimization_confirmation",
            score=seed_score,
            message=(
                "seed evaluation waiting for optimization confirmation: "
                f"score={_fmt_score(seed_score)} "
                f"status={seed_evaluation.get('status', 'unknown')}"
            ),
            metrics={
                "seed_score": seed_score,
                "quality_gap_count": seed_evaluation.get("quality_gap_count"),
            },
            artifacts={
                "eval_ref_path": seed_evaluation.get("eval_ref_path", ""),
                "targeted_dataset_seed_file": seed_evaluation.get("targeted_dataset_seed_file", ""),
            },
        )
        decision = callback(seed_evaluation)
        if inspect.isawaitable(decision):
            decision = await decision
        should_continue = bool(decision)
        if should_continue:
            self._emit(
                phase=OrchestratorPhase.EVALUATING.value,
                stage="optimization_confirmation",
                score=seed_score,
                message="seed optimization confirmed; continuing optimization",
                artifacts={
                    "eval_ref_path": seed_evaluation.get("eval_ref_path", ""),
                    "targeted_dataset_seed_file": seed_evaluation.get("targeted_dataset_seed_file", ""),
                },
            )
            return True

        context = self.context_store.load()
        self.context_store.save(
            replace(
                context,
                phase=OrchestratorPhase.COMPLETED,
                metadata={
                    **context.metadata,
                    "seed_optimization_confirmation": {
                        "continue": False,
                        "reason": "user_declined",
                    },
                },
            )
        )
        self._write_run_report()
        self._emit(
            phase=OrchestratorPhase.COMPLETED.value,
            stage="optimization_confirmation",
            score=seed_score,
            message="seed evaluation completed; optimization skipped by user",
            artifacts={
                "workspace_dir": str(self.workspace_paths.root),
                "eval_ref_path": seed_evaluation.get("eval_ref_path", ""),
                "targeted_dataset_seed_file": seed_evaluation.get("targeted_dataset_seed_file", ""),
            },
        )
        return False

    async def _evaluate_epoch_dataset(
        self,
        *,
        epoch: int,
        team_skill_ref_path: str,
        harness_refs_path: str,
        dataset: DatasetArtifact,
        dataset_dir: Path,
    ) -> str:
        """Run a full-dataset evaluation before creating the epoch checkpoint."""
        eval_ref_path = await self.evaluator.evaluate_batch(
            cases=_flatten_batches(self.data_loader.load(str(dataset_dir), epoch=epoch)),
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
            output_dir=str(self.workspace_paths.epoch_evaluation_dir(epoch)),
            context_path=self.context_store.context_path,
            dataset=dataset,
        )
        self._save_batch_plan_context()
        self.optimized_eval_ref_paths.append(eval_ref_path)
        self._save_epoch_evaluation_context(
            eval_ref_paths=[eval_ref_path],
            phase=f"epoch_{epoch}:full_dataset_checkpoint",
            score=_eval_score(eval_ref_path),
        )
        return eval_ref_path

    async def _run_batch_optimization(
        self,
        *,
        batch: list[CaseMapping],
        batch_index: int,
        epoch: int,
        team_skill_ref_path: str,
        harness_refs_path: str,
        dataset: DatasetArtifact,
    ) -> BatchOptimizationResult:
        """Run evaluation, analysis, and optimizers for a single batch."""
        current_team_skill_ref_path = team_skill_ref_path
        current_harness_refs_path = harness_refs_path
        batch_eval_ref_paths: list[str] = []

        if current_team_skill_ref_path:
            before_team_skill_ref_path = current_team_skill_ref_path
            eval_ref_path = await self._evaluate_batch(
                batch=batch,
                batch_index=batch_index,
                epoch=epoch,
                optimization_stage="team_skill_optimization",
                team_skill_ref_path=current_team_skill_ref_path,
                harness_refs_path=current_harness_refs_path,
                dataset=dataset,
                phase=f"epoch_{epoch}:batch_{batch_index}:current",
            )
            batch_eval_ref_paths.append(eval_ref_path)
            analysis_ref_path = self._analysis_ref_for_eval(eval_ref_path)
            source_stage = _eval_source_stage(eval_ref_path)
            has_team_skill_issue = bool(
                analysis_ref_path
                and _analysis_has_team_skill_issue(
                    analysis_ref_path,
                    source_stage=source_stage,
                )
            )
            current_team_skill_ref_path = await self._maybe_optimize_team_skill(
                baseline_eval_ref_path=eval_ref_path,
                team_skill_ref_path=current_team_skill_ref_path,
            )
            team_skill_changed = current_team_skill_ref_path != before_team_skill_ref_path
            if not has_team_skill_issue or not team_skill_changed:
                current_harness_refs_path = await self._maybe_optimize_member_harness(
                    eval_ref_paths=batch_eval_ref_paths,
                    harness_refs_path=current_harness_refs_path,
                )
                return BatchOptimizationResult(
                    team_skill_ref_path=current_team_skill_ref_path,
                    harness_refs_path=current_harness_refs_path,
                    eval_ref_paths=batch_eval_ref_paths,
                )

        member_eval_ref_path = await self._evaluate_batch(
            batch=batch,
            batch_index=batch_index,
            epoch=epoch,
            optimization_stage="member_optimization",
            team_skill_ref_path=current_team_skill_ref_path,
            harness_refs_path=current_harness_refs_path,
            dataset=dataset,
            phase=f"epoch_{epoch}:batch_{batch_index}:member_current",
        )
        if member_eval_ref_path not in batch_eval_ref_paths:
            batch_eval_ref_paths.append(member_eval_ref_path)
        current_harness_refs_path = await self._maybe_optimize_member_harness(
            eval_ref_paths=[member_eval_ref_path],
            harness_refs_path=current_harness_refs_path,
        )

        return BatchOptimizationResult(
            team_skill_ref_path=current_team_skill_ref_path,
            harness_refs_path=current_harness_refs_path,
            eval_ref_paths=batch_eval_ref_paths,
        )

    async def _evaluate_batch(
        self,
        *,
        batch: list[CaseMapping],
        batch_index: int,
        epoch: int,
        optimization_stage: str,
        team_skill_ref_path: str,
        harness_refs_path: str,
        dataset: DatasetArtifact,
        phase: str,
    ) -> str:
        """Evaluate and analyze one dataset batch for one optimization stage."""
        stage_label = _usage_batch_stage(epoch, batch_index, optimization_stage)
        stage_dir = self.workspace_paths.batch_stage_dir(epoch, batch_index, optimization_stage)
        progress_metrics = _batch_progress_metrics(
            dataset=dataset,
            batch_size=self.config.data_loader.batch_size,
            case_count=len(batch),
        )
        self._emit(
            phase=OrchestratorPhase.EVALUATING.value,
            epoch=epoch,
            batch_index=batch_index,
            stage=optimization_stage,
            message=f"{optimization_stage} batch {batch_index} evaluation started",
            metrics=progress_metrics,
            artifacts={"output_dir": str(stage_dir)},
        )
        existing_eval_ref_path = str(stage_dir / "eval_ref.yaml")
        if self.resume_enabled and _eval_ref_complete(existing_eval_ref_path):
            eval_ref_path = existing_eval_ref_path
        else:
            with llm_usage_scope(
                stage=f"{stage_label}.evaluate",
                operation="evaluate_batch",
                metadata={
                    "epoch": epoch,
                    "batch_index": batch_index,
                    "optimization_stage": optimization_stage,
                    "case_count": len(batch),
                },
            ):
                eval_ref_path = await self.evaluator.evaluate_batch(
                    cases=batch,
                    team_skill_ref_path=team_skill_ref_path,
                    harness_refs_path=harness_refs_path,
                    output_dir=str(stage_dir),
                    context_path=self.context_store.context_path,
                    dataset=dataset,
                )
        analysis_ref_path = self._analysis_ref_for_eval(eval_ref_path)
        if not (self.resume_enabled and _analysis_ref_complete(analysis_ref_path)):
            with llm_usage_scope(
                stage=f"{stage_label}.analyze",
                operation="analyze_evaluation_result",
                metadata={
                    "epoch": epoch,
                    "batch_index": batch_index,
                    "optimization_stage": optimization_stage,
                    "eval_ref_path": eval_ref_path,
                },
            ):
                analysis_ref_path = await self._analyze_evaluation_result(
                    eval_ref_path=eval_ref_path,
                    team_skill_ref_path=team_skill_ref_path,
                    harness_refs_path=harness_refs_path,
                    output_dir=str(stage_dir / "a"),
                )
        self.optimized_eval_ref_paths.append(eval_ref_path)
        self.current_analysis_ref_paths.append(analysis_ref_path)
        self.analysis_ref_by_eval_ref_path[eval_ref_path] = analysis_ref_path
        self._save_epoch_evaluation_context(
            eval_ref_paths=[eval_ref_path],
            phase=phase,
            score=_eval_score(eval_ref_path),
        )
        self._emit(
            phase=OrchestratorPhase.EVALUATING.value,
            epoch=epoch,
            batch_index=batch_index,
            stage=optimization_stage,
            score=_eval_score(eval_ref_path),
            message=f"{optimization_stage} batch {batch_index} evaluation completed",
            metrics=progress_metrics,
            artifacts={
                "eval_ref_path": eval_ref_path,
                "analysis_ref_path": analysis_ref_path,
            },
        )
        return eval_ref_path

    async def _analyze_evaluation_result(
        self,
        *,
        eval_ref_path: str,
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
    ) -> str:
        """Analyze one evaluation and store analysis artifacts beside it."""
        eval_ref = _load_eval_ref(eval_ref_path)
        eval_dir = Path(eval_ref.get("eval_dir") or eval_ref_path).expanduser().resolve()
        if eval_dir.is_file():
            eval_dir = eval_dir.parent
        case_results_dir = _eval_case_results_dir(eval_ref, eval_dir)
        analysis_ref_path = await self.evaluation_result_analyzer.analyze(
            EvaluationResultAnalysisInvocation(
                eval_ref_path=eval_ref_path,
                case_results_dir=str(case_results_dir),
                case_traces_dir=str(_eval_case_traces_dir(eval_ref, case_results_dir)),
                team_skill_ref_path=team_skill_ref_path,
                harness_refs_path=harness_refs_path,
                output_dir=output_dir,
                source_stage=_eval_source_stage(eval_ref_path),
            )
        )
        _write_eval_analysis_ref(eval_ref_path, analysis_ref_path)
        return analysis_ref_path

    def _build_dataset_artifact_from_loaded_dir(self, dataset_dir: Path) -> DatasetArtifact:
        """Build a dataset artifact reference for an existing dataset directory."""
        dataset_files = sorted(
            str(path) for path in dataset_dir.glob(self.config.data_loader.file_pattern) if path.is_file()
        )
        return DatasetArtifact(
            dataset_id=dataset_dir.name,
            dataset_dir=str(dataset_dir),
            dataset_files=dataset_files,
        )

    def _configure_team_workspace(
        self,
        team_skill_ref_path: str,
        *,
        workspace_run_id: str = "",
    ) -> None:
        """Scope run artifacts without changing the reusable Team identity."""
        team_name = self._resolve_team_name(team_skill_ref_path)
        workspace_name = f"{team_name}--{workspace_run_id}" if str(workspace_run_id or "").strip() else team_name
        self.workspace_paths = self.workspace_paths.for_team(workspace_name)
        self.workspace_paths.ensure_workspace_structure()
        self.context_store = OrchestratorContextStore(str(self.workspace_paths.context_path))
        self.checkpoint_manager = CheckpointManager(str(self.workspace_paths.checkpoint_dir))

    @staticmethod
    def _resolve_team_name(team_skill_ref_path: str) -> str:
        """Resolve Team name from Team Skill metadata."""
        return resolve_team_name_from_skill_path(team_skill_ref_path)

    def _load_resume_context(self) -> OrchestratorRunContext | None:
        """Load the newest team-scoped context under this workspace root."""
        base_root = self.workspace_paths.base_root
        if not base_root.is_dir():
            return None
        contexts: list[tuple[float, OrchestratorRunContext]] = []
        for path in base_root.glob("*/orchestrator_context.yaml"):
            if not path.is_file():
                continue
            try:
                context = OrchestratorContextStore(str(path)).load()
            except Exception as exc:
                logger.warning("failed to load resume context %s: %s", path, exc)
                context = None
            if context is None:
                continue
            timestamp = path.stat().st_mtime
            if context.updated_at is not None:
                timestamp = context.updated_at.timestamp()
            contexts.append((timestamp, context))
        if not contexts:
            return None
        return sorted(contexts, key=lambda item: item[0], reverse=True)[0][1]

    def _load_best_context(self) -> OrchestratorRunContext | None:
        """Load an explicitly published profile or the newest reusable run."""
        if self._published_context_path:
            published_path = Path(self._published_context_path).expanduser().resolve()
            if published_path.is_file():
                try:
                    published = OrchestratorContextStore(str(published_path)).load()
                except Exception:
                    logger.exception(
                        "[auto_coordinating_harness] failed to load published context: %s",
                        published_path,
                    )
                else:
                    if self._context_has_reusable_refs(published):
                        return published
        base_root = self.workspace_paths.base_root
        if not base_root.is_dir():
            return None
        contexts: list[tuple[float, OrchestratorRunContext]] = []
        for path in base_root.glob("*/orchestrator_context.yaml"):
            if not path.is_file():
                continue
            try:
                context = OrchestratorContextStore(str(path)).load()
            except Exception as exc:
                logger.warning("failed to load reusable context %s: %s", path, exc)
                context = None
            if context is None:
                continue
            if not self._context_has_reusable_refs(context):
                continue
            timestamp = path.stat().st_mtime
            if context.updated_at is not None:
                timestamp = context.updated_at.timestamp()
            contexts.append((timestamp, context))
        if not contexts:
            return None
        return sorted(contexts, key=lambda item: item[0], reverse=True)[0][1]

    @staticmethod
    def _context_has_reusable_refs(context: OrchestratorRunContext) -> bool:
        """Accept published best refs and completed legacy current refs."""
        best_complete = bool(
            str(context.best.team_skill_ref_path or "").strip() and str(context.best.harness_refs_path or "").strip()
        )
        current_complete = bool(
            context.phase == OrchestratorPhase.COMPLETED
            and context.epoch > 0
            and str(context.current.team_skill_ref_path or "").strip()
            and str(context.current.harness_refs_path or "").strip()
        )
        return best_complete or current_complete

    def _record_optimization_outcome(self) -> int:
        """Persist whether this run produced a genuinely accepted change."""
        context = self.context_store.load()
        accepted_change_count = len(context.history.team_skill_optimizations) + len(
            context.history.member_optimizations
        )
        outcome = "accepted_change" if accepted_change_count else "no_accepted_change"
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "optimization_outcome": outcome,
                    "accepted_change_count": accepted_change_count,
                    "published_optimization": False,
                },
            )
        )
        return accepted_change_count

    def _publish_best_context(self) -> bool:
        """Publish stable Team refs independently from a run workspace."""
        if not self._published_context_path:
            return False
        context = self.context_store.load()
        if not int(context.metadata.get("accepted_change_count", 0) or 0):
            return False
        if not self._context_has_reusable_refs(context):
            return False
        if not (
            str(context.best.team_skill_ref_path or "").strip() and str(context.best.harness_refs_path or "").strip()
        ):
            promoted_score = (
                _eval_score(str(context.current.eval_ref_path))
                if str(context.current.eval_ref_path or "").strip()
                else None
            )
            context = replace(
                context,
                best=replace(
                    context.best,
                    team_skill_ref_path=context.current.team_skill_ref_path,
                    harness_refs_path=context.current.harness_refs_path,
                    harness_refs=context.current.harness_refs,
                    eval_ref_path=context.current.eval_ref_path,
                    score=promoted_score,
                ),
            )
        published_path = Path(self._published_context_path).expanduser().resolve()
        published = replace(
            context,
            metadata={
                **context.metadata,
                "published_from_context_path": context.context_path,
                "published_profile_path": str(published_path),
                "published_optimization": True,
            },
        )
        OrchestratorContextStore(str(published_path)).save(published)
        self.context_store.save(published)
        return True

    def _restore_workspace_from_context(self, context: OrchestratorRunContext) -> None:
        """Point workspace helpers at the team workspace stored in context."""
        context_path = Path(context.context_path).expanduser().resolve()
        team_name = context_path.parent.name
        self.workspace_paths = self.workspace_paths.for_team(team_name)
        self.workspace_paths.ensure_workspace_structure()
        self.context_store = OrchestratorContextStore(str(self.workspace_paths.context_path))
        self.checkpoint_manager = CheckpointManager(str(self.workspace_paths.checkpoint_dir))
        self.initial_harness_refs_path = str(context.metadata.get("initial_harness_refs_path", "") or "") or None

    def _resume_completed_batch(
        self,
        *,
        epoch: int,
        batch_index: int,
    ) -> BatchOptimizationResult | None:
        """Return a terminal batch checkpoint instead of replaying its optimizers."""
        if not self.resume_enabled or not Path(self.context_store.context_path).is_file():
            return None
        context = self.context_store.load()
        completions = context.metadata.get("completed_batches", {})
        if not isinstance(completions, dict):
            return None
        item = completions.get(_batch_completion_key(epoch, batch_index))
        if not isinstance(item, dict) or item.get("status") != "completed":
            return None
        eval_ref_paths = [
            str(path) for path in item.get("eval_ref_paths", []) if str(path).strip() and _eval_ref_complete(str(path))
        ]
        expected_eval_refs = item.get("eval_ref_paths", [])
        if len(eval_ref_paths) != len(expected_eval_refs):
            return None
        return BatchOptimizationResult(
            team_skill_ref_path=str(item.get("team_skill_ref_path", "")),
            harness_refs_path=str(item.get("harness_refs_path", "")),
            eval_ref_paths=eval_ref_paths,
        )

    def _save_completed_batch_context(
        self,
        *,
        epoch: int,
        batch_index: int,
        result: BatchOptimizationResult,
    ) -> None:
        """Persist the terminal output of a batch as a resume idempotency boundary."""
        context = self.context_store.load()
        completions = dict(context.metadata.get("completed_batches", {}) or {})
        completions[_batch_completion_key(epoch, batch_index)] = {
            "status": "completed",
            "epoch": epoch,
            "batch_index": batch_index,
            "team_skill_ref_path": result.team_skill_ref_path,
            "harness_refs_path": result.harness_refs_path,
            "eval_ref_paths": list(result.eval_ref_paths),
        }
        self.context_store.save(
            replace(
                context,
                metadata={**context.metadata, "completed_batches": completions},
            )
        )

    @staticmethod
    def _resume_seed_evaluation(context: OrchestratorRunContext) -> dict[str, Any] | None:
        """Return reusable seed metadata when its referenced artifacts are complete."""
        seed = context.metadata.get("seed_evaluation")
        if not isinstance(seed, dict):
            return None
        eval_ref_path = str(seed.get("eval_ref_path", "") or "")
        if eval_ref_path and not _eval_ref_complete(eval_ref_path):
            return None
        if seed.get("dataset_generation_skipped") is True:
            return dict(seed)
        seed_ref = str(seed.get("targeted_dataset_seed_file", "") or "")
        if seed_ref and Path(seed_ref).expanduser().is_file():
            return dict(seed)
        return None

    def _resume_dataset_artifact(self, context: OrchestratorRunContext) -> DatasetArtifact | None:
        """Return the current dataset only when all referenced files still exist."""
        dataset = context.current.dataset
        if dataset is None:
            return None
        dataset_dir = Path(dataset.dataset_dir).expanduser().resolve()
        if not dataset_dir.is_dir():
            return None
        dataset_files = [
            str(Path(path).expanduser().resolve())
            for path in dataset.dataset_files
            if Path(path).expanduser().is_file()
        ]
        if dataset.dataset_files and len(dataset_files) != len(dataset.dataset_files):
            return None
        if not dataset_files:
            dataset_files = sorted(
                str(path) for path in dataset_dir.glob(self.config.data_loader.file_pattern) if path.is_file()
            )
        if not dataset_files:
            return None
        return DatasetArtifact(
            dataset_id=dataset.dataset_id or dataset_dir.name,
            dataset_dir=str(dataset_dir),
            dataset_files=dataset_files,
        )

    async def _ensure_initial_team_skill(
        self,
        *,
        task: str,
        team_skill_ref_path: str,
    ) -> str:
        """Generate an initial Team Skill when the run starts without one."""
        if str(team_skill_ref_path or "").strip():
            return team_skill_ref_path
        generator = self.team_skill_generator
        can_generate = getattr(generator, "can_generate", None)
        if callable(can_generate):
            if not can_generate():
                return team_skill_ref_path
        elif not str(getattr(generator, "model_config_ref", "") or "").strip():
            return team_skill_ref_path
        generated = await generator.generate(
            task,
            self.workspace_paths.base_root / "initial_team_skills",
        )
        return str(generated)

    def _ensure_initial_member_harness_refs(
        self,
        *,
        team_skill_ref_path: str,
        harness_refs_path: str,
    ) -> str:
        """Create initial role harness refs from Team Skill when none are supplied."""
        if str(harness_refs_path or "").strip():
            return harness_refs_path
        if not str(team_skill_ref_path or "").strip():
            return harness_refs_path
        if self.config.evaluator.backend != "local":
            return harness_refs_path

        generated = build_initial_harness_refs_from_team_skill(
            team_skill_ref_path=team_skill_ref_path,
            output_dir=self.workspace_paths.initial_harness_dir(),
        )
        if generated is None:
            return harness_refs_path
        self.initial_harness_refs_path = generated.refs_path
        return generated.refs_path

    async def _maybe_optimize_team_skill(
        self,
        *,
        baseline_eval_ref_path: str,
        team_skill_ref_path: str,
    ) -> str:
        """Optimize Team Skill using one batch result."""
        if self.config.freeze_team_skill or self.config.team_skill_optimizer.freeze:
            return team_skill_ref_path
        if self.team_skill_locked:
            return team_skill_ref_path
        analysis_ref_path = self._analysis_ref_for_eval(baseline_eval_ref_path)
        source_stage = _eval_source_stage(baseline_eval_ref_path)
        if analysis_ref_path and not _analysis_has_team_skill_issue(
            analysis_ref_path,
            source_stage=source_stage,
        ):
            if _analysis_has_actionable_member_issue(
                analysis_ref_path,
                source_stage=source_stage,
            ):
                self._save_optimization_issue_route(
                    eval_ref_path=baseline_eval_ref_path,
                    analysis_ref_path=analysis_ref_path,
                    source_stage=source_stage,
                    target_scope="member_harness",
                    route="member_optimizer",
                    status="deferred",
                    reason="analysis_identified_member_harness_issue_in_team_stage",
                )
            self._save_team_skill_gate_context(
                eval_ref_path=baseline_eval_ref_path,
                analysis_ref_path=analysis_ref_path,
                reason="analysis_did_not_identify_team_skill_issue",
            )
            return team_skill_ref_path

        with llm_usage_scope(
            stage=f"{_eval_usage_stage_prefix(baseline_eval_ref_path)}.team_skill_optimize",
            operation="optimize_team_skill",
            metadata={
                "eval_ref_path": baseline_eval_ref_path,
                "analysis_ref_path": analysis_ref_path,
            },
        ):
            self._emit(
                phase=OrchestratorPhase.OPTIMIZING_TEAM_SKILL.value,
                epoch=self._current_epoch,
                batch_index=self._current_batch_index,
                stage="team_skill_optimization",
                score=_eval_score(baseline_eval_ref_path),
                message="team skill optimization started",
                artifacts={
                    "eval_ref_path": baseline_eval_ref_path,
                    "analysis_ref_path": analysis_ref_path,
                    "team_skill_ref_path": team_skill_ref_path,
                },
            )
            optimized_workspace_path = await self.team_skill_optimizer.optimize(
                eval_ref_path=baseline_eval_ref_path,
                analysis_result_path=analysis_ref_path,
                team_skill_ref_path=team_skill_ref_path,
                output_dir=str(self.workspace_paths.team_skill_dir()),
            )
        effective_team_skill_ref_path = optimized_workspace_path or team_skill_ref_path
        self.optimized_team_skill_ref_path = effective_team_skill_ref_path
        self._save_effective_team_skill_context(
            before_team_skill_ref_path=team_skill_ref_path,
            after_team_skill_ref_path=effective_team_skill_ref_path,
            eval_ref_path=baseline_eval_ref_path,
            score=_eval_score(baseline_eval_ref_path),
        )
        self._emit(
            phase=OrchestratorPhase.OPTIMIZING_TEAM_SKILL.value,
            epoch=self._current_epoch,
            batch_index=self._current_batch_index,
            stage="team_skill_optimization",
            score=_eval_score(baseline_eval_ref_path),
            message="team skill optimization completed",
            artifacts={
                "eval_ref_path": baseline_eval_ref_path,
                "team_skill_ref_path": effective_team_skill_ref_path,
            },
        )
        with llm_usage_scope(
            stage=f"{_eval_usage_stage_prefix(baseline_eval_ref_path)}.experience_learning",
            operation="record_team_skill_experience",
            metadata={"eval_ref_path": baseline_eval_ref_path},
        ):
            experience_ref_path = await self.optimization_experience_learner.record_team_skill_experience(
                before_team_skill_ref_path=team_skill_ref_path,
                after_team_skill_ref_path=effective_team_skill_ref_path,
                eval_ref_path=baseline_eval_ref_path,
                candidate_dir=optimized_workspace_path,
                output_dir=str(self.workspace_paths.optimization_experience_dir()),
                score=_eval_score(baseline_eval_ref_path),
            )
        if experience_ref_path:
            self.experience_ref_paths.append(experience_ref_path)
            self._epoch_experience_ref_paths.append(experience_ref_path)
        self._team_skill_optimized_in_epoch = True
        return effective_team_skill_ref_path

    def _save_team_skill_gate_context(
        self,
        *,
        eval_ref_path: str,
        analysis_ref_path: str,
        reason: str,
    ) -> None:
        """Record why Team Skill optimization was skipped for this batch."""
        context = self.context_store.load()
        skipped = [
            *context.metadata.get("team_skill_optimizer_skips", []),
            {
                "eval_ref_path": eval_ref_path,
                "analysis_ref_path": analysis_ref_path,
                "reason": reason,
            },
        ]
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "team_skill_optimizer_skips": skipped,
                    "team_skill_locked": self.team_skill_locked,
                },
            )
        )

    async def _maybe_optimize_member_harness(
        self,
        *,
        eval_ref_paths: list[str],
        harness_refs_path: str,
    ) -> str:
        """Optimize member harnesses after Team Skill optimization."""
        if self.config.freeze_team_members or self.config.member_optimizer.freeze:
            return harness_refs_path
        if not eval_ref_paths:
            return harness_refs_path

        eval_ref_path, analysis_result_path = self._select_member_optimization_input(eval_ref_paths)
        if not eval_ref_path or not analysis_result_path:
            latest_eval_ref_path = eval_ref_paths[-1]
            latest_analysis_path = self._analysis_ref_for_eval(latest_eval_ref_path)
            if not latest_analysis_path:
                latest_analysis_path = self._write_member_analysis_input(latest_eval_ref_path)
            self._save_member_gate_context(
                eval_ref_path=latest_eval_ref_path,
                analysis_ref_path=latest_analysis_path,
                reason="analysis_did_not_identify_member_harness_issue",
            )
            return harness_refs_path
        if _eval_source_stage(eval_ref_path) != "member_stage":
            self._save_optimization_issue_route(
                eval_ref_path=eval_ref_path,
                analysis_ref_path=analysis_result_path,
                source_stage=_eval_source_stage(eval_ref_path),
                target_scope="member_harness",
                route="member_optimizer",
                status="handled",
                reason="analysis_identified_member_harness_issue_in_team_stage",
            )
        with llm_usage_scope(
            stage=f"{_eval_usage_stage_prefix(eval_ref_path)}.member_optimize",
            operation="optimize_member_harness",
            metadata={
                "eval_ref_path": eval_ref_path,
                "analysis_ref_path": analysis_result_path,
            },
        ):
            self._emit(
                phase=OrchestratorPhase.OPTIMIZING_MEMBER.value,
                epoch=self._current_epoch,
                batch_index=self._current_batch_index,
                stage="member_optimization",
                score=_eval_score(eval_ref_path),
                message="member harness optimization started",
                artifacts={
                    "eval_ref_path": eval_ref_path,
                    "analysis_ref_path": analysis_result_path,
                    "harness_refs_path": harness_refs_path,
                },
            )
            optimize_kwargs: dict[str, Any] = {
                "eval_ref_path": eval_ref_path,
                "analysis_result_path": analysis_result_path,
                "harness_refs_path": harness_refs_path,
                "output_dir": str(self.workspace_paths.member_optimization_dir()),
            }
            if "defer_publish" in inspect.signature(self.member_optimizer.optimize).parameters:
                optimize_kwargs["defer_publish"] = True
            if "rejected_capabilities" in inspect.signature(self.member_optimizer.optimize).parameters:
                context = self.context_store.load()
                attempts = context.metadata.get("member_capability_attempts", [])
                rejected_capabilities = []
                for item in attempts:
                    if not isinstance(item, dict) or item.get("status") != "rejected":
                        continue
                    capability = item.get("capability")
                    if not isinstance(capability, dict):
                        continue
                    rejected_capabilities.append(
                        {
                            **dict(capability),
                            "rejection_reason": str(item.get("reason", "")),
                        }
                    )
                optimize_kwargs["rejected_capabilities"] = rejected_capabilities
            member_optimization_ref_path = await self.member_optimizer.optimize(
                **optimize_kwargs,
            )
        self.member_optimization_ref_path = member_optimization_ref_path
        member_info = _member_optimization_info(member_optimization_ref_path)
        published_roles = _member_optimization_published_roles(member_info)
        role = ",".join(published_roles) if published_roles else str(member_info.get("role", "default") or "default")
        optimized_harness_refs_path = str(member_info.get("optimized_harness_refs_path", ""))
        candidate_capabilities = _member_candidate_capabilities(member_info)
        expected_tool_names = _candidate_expected_tool_names(candidate_capabilities)
        optimization_status = str(member_info.get("status", "") or "")
        if optimization_status and optimization_status not in {"success", "partial_success"}:
            self._save_member_candidate_gate_context(
                source_eval_ref_path=eval_ref_path,
                candidate_eval_ref_path="",
                before_harness_refs_path=harness_refs_path,
                candidate_harness_refs_path=optimized_harness_refs_path,
                accepted=False,
                reason=f"member_optimization_status_{optimization_status}",
                source_score=_eval_score(eval_ref_path),
                candidate_score=None,
            )
            self.optimized_harness_refs_path = harness_refs_path
            return harness_refs_path

        candidate_harness_refs_path = optimized_harness_refs_path or harness_refs_path
        duplicate_capability = self._find_rejected_duplicate_capability(candidate_capabilities)
        if duplicate_capability is not None:
            gate = {
                "source_eval_ref_path": eval_ref_path,
                "candidate_eval_ref_path": "",
                "before_harness_refs_path": harness_refs_path,
                "candidate_harness_refs_path": candidate_harness_refs_path,
                "source_score": _eval_score(eval_ref_path),
                "candidate_score": None,
                "target_behavior_delta": 0.0,
                "status": "rejected",
                "reason": "duplicate_rejected_capability",
                "capabilities": candidate_capabilities,
                "duplicate_of": duplicate_capability,
            }
            self._append_member_candidate_gate_context(gate)
            self._persist_member_promotion_outcome(
                member_optimization_ref_path=member_optimization_ref_path,
                candidate_harness_refs_path=candidate_harness_refs_path,
                accepted=False,
                gate=gate,
            )
            self.optimized_harness_refs_path = harness_refs_path
            return harness_refs_path
        with llm_usage_scope(
            stage=f"{_eval_usage_stage_prefix(eval_ref_path)}.candidate_gate",
            operation="evaluate_member_candidate_gate",
            metadata={"source_eval_ref_path": eval_ref_path},
        ):
            self._emit(
                phase=OrchestratorPhase.OPTIMIZING_MEMBER.value,
                epoch=self._current_epoch,
                batch_index=self._current_batch_index,
                stage="candidate_gate",
                score=_eval_score(eval_ref_path),
                message="member harness candidate gate started",
                artifacts={
                    "source_eval_ref_path": eval_ref_path,
                    "candidate_harness_refs_path": candidate_harness_refs_path,
                },
            )
            gate = await self._evaluate_member_candidate_gate(
                source_eval_ref_path=eval_ref_path,
                before_harness_refs_path=harness_refs_path,
                candidate_harness_refs_path=candidate_harness_refs_path,
                expected_tool_names=expected_tool_names,
                capabilities=candidate_capabilities,
            )
        self._persist_member_promotion_outcome(
            member_optimization_ref_path=member_optimization_ref_path,
            candidate_harness_refs_path=candidate_harness_refs_path,
            accepted=bool(gate.get("accepted", False)),
            gate=gate,
        )
        if not gate.get("accepted", False):
            self.optimized_harness_refs_path = harness_refs_path
            self._emit(
                phase=OrchestratorPhase.OPTIMIZING_MEMBER.value,
                epoch=self._current_epoch,
                batch_index=self._current_batch_index,
                stage="candidate_gate",
                score=gate.get("candidate_score"),
                improved=False,
                message=f"member harness candidate rejected: {gate.get('reason', '')}",
                metrics={
                    "source_score": gate.get("source_score"),
                    "candidate_score": gate.get("candidate_score"),
                },
                artifacts={
                    "source_eval_ref_path": eval_ref_path,
                    "candidate_eval_ref_path": gate.get("candidate_eval_ref_path", ""),
                },
            )
            return harness_refs_path

        effective_harness_refs_path = candidate_harness_refs_path
        self.optimized_harness_refs_path = effective_harness_refs_path
        self._save_effective_member_context(
            before_harness_refs_path=harness_refs_path,
            after_harness_refs_path=effective_harness_refs_path,
            eval_ref_path=eval_ref_path,
            role=role,
        )
        self._emit(
            phase=OrchestratorPhase.OPTIMIZING_MEMBER.value,
            epoch=self._current_epoch,
            batch_index=self._current_batch_index,
            stage="member_optimization",
            score=gate.get("candidate_score"),
            improved=True,
            message="member harness optimization completed",
            metrics={
                "source_score": gate.get("source_score"),
                "candidate_score": gate.get("candidate_score"),
            },
            artifacts={
                "eval_ref_path": eval_ref_path,
                "harness_refs_path": effective_harness_refs_path,
                "candidate_eval_ref_path": gate.get("candidate_eval_ref_path", ""),
            },
        )
        with llm_usage_scope(
            stage=f"{_eval_usage_stage_prefix(eval_ref_path)}.experience_learning",
            operation="record_member_experience",
            metadata={"eval_ref_path": eval_ref_path, "role": role},
        ):
            experience_ref_path = await self.optimization_experience_learner.record_member_experience(
                before_harness_refs_path=harness_refs_path,
                after_harness_refs_path=effective_harness_refs_path,
                eval_ref_path=eval_ref_path,
                member_optimization_ref_path=member_optimization_ref_path,
                analysis_result_path=analysis_result_path,
                plan_path=str(member_info.get("plan_path", "")),
                execution_result_path=str(member_info.get("execution_result_path", "")),
                verification_path=str(member_info.get("verification_path", "")),
                fix_result_path=str(member_info.get("fix_result_path", "")),
                output_dir=str(self.workspace_paths.optimization_experience_dir()),
                role=role,
            )
        if experience_ref_path:
            self.experience_ref_paths.append(experience_ref_path)
            self._epoch_experience_ref_paths.append(experience_ref_path)
        return effective_harness_refs_path

    def _find_rejected_duplicate_capability(
        self,
        capabilities: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Find a prior rejected add action with the same effective capability."""
        context = self.context_store.load()
        attempts = context.metadata.get("member_capability_attempts", [])
        if not isinstance(attempts, list):
            return None
        for capability in capabilities:
            if capability.get("operation") != "add":
                continue
            for attempt in reversed(attempts):
                if not isinstance(attempt, dict) or attempt.get("status") != "rejected":
                    continue
                previous = attempt.get("capability", {})
                if not isinstance(previous, dict) or previous.get("operation") != "add":
                    continue
                if _capabilities_equivalent(capability, previous):
                    return previous
        return None

    @staticmethod
    def _persist_member_promotion_outcome(
        *,
        member_optimization_ref_path: str,
        candidate_harness_refs_path: str,
        accepted: bool,
        gate: dict[str, Any],
    ) -> None:
        """Persist the distinction between a verified candidate and promotion."""
        candidate_roles: list[str] = []
        member_ref = Path(member_optimization_ref_path).expanduser()
        if member_ref.is_file():
            payload = read_yaml_mapping(member_ref)
            raw_roles = payload.get("candidate_ready_roles", payload.get("published_roles", []))
            if isinstance(raw_roles, list):
                candidate_roles = [str(role) for role in raw_roles if str(role).strip()]
            payload["candidate_ready_roles"] = candidate_roles
            payload["promoted_roles"] = candidate_roles if accepted else []
            payload["promotion_status"] = "promoted" if accepted else "rejected"
            payload["candidate_gate"] = {
                "status": "accepted" if accepted else "rejected",
                "reason": str(gate.get("reason", "")),
                "source_score": gate.get("source_score"),
                "candidate_score": gate.get("candidate_score"),
                "target_behavior_delta": gate.get("target_behavior_delta"),
            }
            write_yaml_mapping(member_ref, payload)

        candidate_ref = Path(candidate_harness_refs_path).expanduser()
        if candidate_ref.is_file():
            payload = read_yaml_mapping(candidate_ref)
            payload["candidate_ready_roles"] = candidate_roles
            payload["promoted_roles"] = candidate_roles if accepted else []
            payload["promotion_status"] = "promoted" if accepted else "rejected"
            write_yaml_mapping(candidate_ref, payload)

    def _select_member_optimization_input(self, eval_ref_paths: list[str]) -> tuple[str, str]:
        """Combine actionable, still-failing member analyses for one optimizer call."""
        selected_pending: list[tuple[str, str]] = []
        selected_passed: list[tuple[str, str]] = []
        for eval_ref_path in eval_ref_paths:
            analysis_result_path = self._analysis_ref_for_eval(eval_ref_path)
            if not analysis_result_path:
                analysis_result_path = self._write_member_analysis_input(eval_ref_path)
            source_stage = _eval_source_stage(eval_ref_path)
            if not _analysis_has_actionable_member_issue(
                analysis_result_path,
                source_stage=source_stage,
            ):
                analysis_result_path = self._adapt_frozen_team_analysis_for_member(
                    eval_ref_path=eval_ref_path,
                    analysis_ref_path=analysis_result_path,
                    source_stage=source_stage,
                )
                if not analysis_result_path:
                    continue
            selected = (eval_ref_path, analysis_result_path)
            if _eval_ref_all_cases_passed(
                eval_ref_path,
                success_score=self.config.evaluator.success_score,
            ):
                selected_passed.append(selected)
            else:
                selected_pending.append(selected)
        selected = selected_pending or selected_passed
        if not selected:
            return "", ""
        latest_eval_ref_path, latest_analysis_path = selected[-1]
        if len(selected) == 1:
            return latest_eval_ref_path, latest_analysis_path
        return latest_eval_ref_path, self._write_combined_member_analysis_input(selected)

    def _adapt_frozen_team_analysis_for_member(
        self,
        *,
        eval_ref_path: str,
        analysis_ref_path: str,
        source_stage: str,
    ) -> str:
        """Encode an unavailable Team Skill fix as one local member procedure."""
        if not self.config.member_optimizer.adapt_frozen_team_issues:
            return ""
        if source_stage != "team_skill_stage":
            return ""
        team_optimizer_unavailable = bool(
            self.config.freeze_team_skill or self.config.team_skill_optimizer.freeze or self.team_skill_locked
        )
        if not team_optimizer_unavailable:
            return ""
        analysis = read_yaml_mapping(analysis_ref_path)
        issues = analysis.get("issues", [])
        if not isinstance(issues, list):
            return ""

        adapted_issues: list[dict[str, Any]] = []
        for issue in issues:
            if not isinstance(issue, dict) or _issue_optimization_target(issue) != "team_skill":
                continue
            target_members = _issue_member_candidates(issue)
            if not target_members:
                continue
            adapted_issue = dict(issue)
            metadata = dict(adapted_issue.get("metadata") or {})
            attribution = dict(metadata.get("attribution") or {})
            original_target_ref = str(attribution.get("target_ref") or adapted_issue.get("target_ref") or "")
            target_member = _preferred_issue_member_candidate(
                issue,
                target_members,
                target_ref=original_target_ref,
            )
            adapted_target_ref = f"member_harness.{target_member}.skill"
            attribution["target_ref"] = adapted_target_ref
            metadata["attribution"] = attribution
            metadata["restricted_scope_adaptation"] = {
                "source_scope": "team_skill",
                "adapted_scope": "member_harness",
                "source_target_ref": original_target_ref,
                "adapted_target_ref": adapted_target_ref,
                "candidate_members": target_members,
                "selected_member": target_member,
                "reason": "team_skill_optimizer_unavailable",
            }
            adapted_issue.update(
                {
                    "optimization_target": "member_harness",
                    "target_members": [target_member],
                    "target_ref": adapted_target_ref,
                    "likely_surfaces": ["skill", "prompt_section"],
                    "recommendation": (
                        "Encode the unavailable team-level integration gate as a "
                        "reusable local pre-delivery procedure for one responsible "
                        "member while preserving the original team-level diagnosis. "
                        + str(adapted_issue.get("recommendation", "") or "")
                    ).strip(),
                    "metadata": metadata,
                }
            )
            adapted_issues.append(adapted_issue)
        if not adapted_issues:
            return ""

        analysis_dir = _analysis_dir_for_eval_ref(eval_ref_path)
        index = 1
        while True:
            adapted_path = analysis_dir / f"member_analysis_restricted_team_{index:03d}.yaml"
            if not adapted_path.exists():
                break
            index += 1
        payload = {
            "analysis_id": adapted_path.stem,
            "source_eval_ref_path": eval_ref_path,
            "source_analysis_ref_path": analysis_ref_path,
            "issues": adapted_issues,
            "metadata": {
                "adaptation_reason": "team_skill_optimizer_unavailable",
                "source_scope": "team_skill",
                "adapted_scope": "member_harness",
            },
        }
        written_path = write_yaml_mapping(adapted_path, payload)
        self._save_optimization_issue_route(
            eval_ref_path=eval_ref_path,
            analysis_ref_path=written_path,
            source_stage=source_stage,
            target_scope="member_harness",
            route="member_optimizer",
            status="adapted",
            reason="team_skill_issue_adapted_because_team_optimizer_unavailable",
        )
        return written_path

    @staticmethod
    def _write_combined_member_analysis_input(
        selected: list[tuple[str, str]],
    ) -> str:
        """Write one analysis artifact that preserves all actionable member issues."""
        latest_eval_ref_path = selected[-1][0]
        analysis_dir = _analysis_dir_for_eval_ref(latest_eval_ref_path)
        index = 1
        while True:
            analysis_path = analysis_dir / f"member_analysis_combined_{index:03d}.yaml"
            if not analysis_path.exists():
                break
            index += 1
        merged_issues: list[dict[str, Any]] = []
        source_analysis_ref_paths: list[str] = []
        source_eval_ref_paths: list[str] = []
        for eval_ref_path, analysis_ref_path in selected:
            source_analysis_ref_paths.append(analysis_ref_path)
            source_eval_ref_paths.append(eval_ref_path)
            source_stage = _eval_source_stage(eval_ref_path)
            analysis = read_yaml_mapping(analysis_ref_path)
            issues = analysis.get("issues", [])
            if not isinstance(issues, list):
                continue
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                if _issue_optimization_target(issue) != "member_harness":
                    continue
                if _issue_targets_generated_team_contract(issue, source_stage=source_stage):
                    continue
                merged_issue = dict(issue)
                metadata = dict(merged_issue.get("metadata") or {})
                metadata.update(
                    {
                        "source_eval_ref_path": eval_ref_path,
                        "source_analysis_ref_path": analysis_ref_path,
                        "source_stage": source_stage,
                    }
                )
                merged_issue["metadata"] = metadata
                merged_issues.append(merged_issue)
        payload = {
            "analysis_id": analysis_path.stem,
            "source_eval_ref_path": latest_eval_ref_path,
            "source_eval_ref_paths": source_eval_ref_paths,
            "source_analysis_ref_paths": source_analysis_ref_paths,
            "issues": merged_issues,
            "metadata": {
                "merge_reason": "multiple_member_harness_analyses_in_batch",
            },
        }
        return write_yaml_mapping(analysis_path, payload)

    async def _evaluate_member_candidate_gate(
        self,
        *,
        source_eval_ref_path: str,
        before_harness_refs_path: str,
        candidate_harness_refs_path: str,
        expected_tool_names: list[str] | None = None,
        capabilities: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a candidate on the same batch before promoting it to current."""
        source_eval = _load_eval_ref(source_eval_ref_path)
        source_cases, source_score, filtered_source_cases = _member_gate_source_cases_and_score(source_eval_ref_path)
        if not source_cases and filtered_source_cases > 0:
            gate = {
                "source_eval_ref_path": source_eval_ref_path,
                "candidate_eval_ref_path": "",
                "before_harness_refs_path": before_harness_refs_path,
                "candidate_harness_refs_path": candidate_harness_refs_path,
                "source_score": source_score,
                "candidate_score": None,
                "status": "inconclusive",
                "reason": "source_batch_has_no_comparable_member_cases",
                "filtered_source_cases": filtered_source_cases,
            }
            self._append_member_candidate_gate_context(gate)
            return {"accepted": False, **gate}
        if not source_cases or candidate_harness_refs_path == before_harness_refs_path:
            gate = {
                "source_eval_ref_path": source_eval_ref_path,
                "candidate_eval_ref_path": "",
                "before_harness_refs_path": before_harness_refs_path,
                "candidate_harness_refs_path": candidate_harness_refs_path,
                "source_score": source_score,
                "candidate_score": None,
                "status": "accepted",
                "reason": "candidate_gate_skipped_no_source_cases",
                "filtered_source_cases": filtered_source_cases,
            }
            self._append_member_candidate_gate_context(gate)
            return {"accepted": True, **gate}

        eval_path = Path(source_eval_ref_path).expanduser().resolve()
        gate_root = eval_path.parent / "cg"
        try:
            candidate_eval_ref_path = await self.evaluator.evaluate_batch(
                cases=source_cases,
                team_skill_ref_path=str(source_eval.get("team_skill_ref_path", "")),
                harness_refs_path=candidate_harness_refs_path,
                output_dir=str(gate_root),
                context_path=self.context_store.context_path,
                dataset=self.context_store.load().current.dataset,
            )
        except Exception as exc:
            gate = {
                "source_eval_ref_path": source_eval_ref_path,
                "candidate_eval_ref_path": "",
                "before_harness_refs_path": before_harness_refs_path,
                "candidate_harness_refs_path": candidate_harness_refs_path,
                "source_score": source_score,
                "candidate_score": None,
                "status": "gate_error",
                "reason": "candidate_gate_evaluation_failed",
                "filtered_source_cases": filtered_source_cases,
                "error": str(exc),
            }
            self._append_member_candidate_gate_context(gate)
            return {"accepted": False, **gate}
        candidate_score = _eval_score(candidate_eval_ref_path)
        if _eval_ref_has_inconclusive_cases(candidate_eval_ref_path):
            gate = {
                "source_eval_ref_path": source_eval_ref_path,
                "candidate_eval_ref_path": candidate_eval_ref_path,
                "before_harness_refs_path": before_harness_refs_path,
                "candidate_harness_refs_path": candidate_harness_refs_path,
                "source_score": source_score,
                "candidate_score": candidate_score,
                "status": "inconclusive",
                "reason": "candidate_gate_inconclusive_due_to_error_cases",
                "filtered_source_cases": filtered_source_cases,
            }
            self._append_member_candidate_gate_context(gate)
            return {"accepted": False, **gate}
        target_roles: set[str] = set()
        for capability in capabilities or []:
            if not isinstance(capability, dict):
                continue
            role = str(capability.get("role", "") or "").strip()
            if role:
                target_roles.add(role)
        target_behavior_delta = _eval_target_behavior_delta(
            source_eval_ref_path,
            candidate_eval_ref_path,
            target_roles=target_roles,
        )
        expected_tools = sorted(set(expected_tool_names or []))
        invoked_tools = sorted(_eval_invoked_tool_names(candidate_eval_ref_path))
        failed_machine_evidence = _eval_failed_machine_evidence(candidate_eval_ref_path)
        missing_expected_tools = []
        for expected_name in expected_tools:
            was_invoked = any(_runtime_tool_names_match(expected_name, invoked_name) for invoked_name in invoked_tools)
            if not was_invoked:
                missing_expected_tools.append(expected_name)
        missing_expected_tools.sort()
        score_delta = candidate_score - source_score
        min_score_delta = float(self.config.member_optimizer.candidate_min_score_delta)
        min_target_delta = float(self.config.member_optimizer.candidate_min_target_behavior_delta)
        accepted = score_delta > min_score_delta
        reason = "candidate_improved_source_batch" if accepted else "candidate_did_not_improve_source_batch"
        if not accepted and score_delta >= 0 and target_behavior_delta > min_target_delta:
            accepted = True
            reason = "candidate_improved_target_behavior"
        if missing_expected_tools:
            accepted = False
            reason = "expected_tool_not_invoked"
        if failed_machine_evidence:
            accepted = False
            reason = "candidate_machine_evidence_failed"
        holdout: dict[str, Any] = {
            "status": "skipped",
            "reason": "candidate_failed_primary_gate",
            "case_count": 0,
        }
        if accepted:
            holdout = await self._evaluate_member_candidate_holdout(
                source_cases=source_cases,
                source_eval=source_eval,
                before_harness_refs_path=before_harness_refs_path,
                candidate_harness_refs_path=candidate_harness_refs_path,
                gate_root=gate_root,
                capabilities=list(capabilities or []),
            )
            if holdout.get("status") == "inconclusive":
                accepted = False
                reason = "candidate_holdout_inconclusive"
            elif holdout.get("candidate_failed_machine_evidence"):
                accepted = False
                reason = "candidate_holdout_machine_evidence_failed"
        holdout_delta = _numeric(holdout.get("score_delta"))
        max_holdout_regression = float(self.config.member_optimizer.candidate_holdout_max_regression)
        if holdout_delta is not None and holdout_delta < -max_holdout_regression:
            accepted = False
            reason = "candidate_regressed_holdout"
        gate = {
            "source_eval_ref_path": source_eval_ref_path,
            "candidate_eval_ref_path": candidate_eval_ref_path,
            "before_harness_refs_path": before_harness_refs_path,
            "candidate_harness_refs_path": candidate_harness_refs_path,
            "source_score": source_score,
            "candidate_score": candidate_score,
            "score_delta": score_delta,
            "minimum_score_delta": min_score_delta,
            "target_behavior_delta": target_behavior_delta,
            "minimum_target_behavior_delta": min_target_delta,
            "expected_tool_names": expected_tools,
            "invoked_tool_names": invoked_tools,
            "missing_expected_tool_names": missing_expected_tools,
            "failed_machine_evidence": failed_machine_evidence,
            "capabilities": list(capabilities or []),
            "holdout": holdout,
            "status": "accepted" if accepted else "rejected",
            "reason": reason,
            "filtered_source_cases": filtered_source_cases,
        }
        self._append_member_candidate_gate_context(gate)
        if not accepted:
            context = self.context_store.load()
            self.context_store.save(
                replace(
                    context,
                    current=replace(context.current, eval_ref_path=source_eval_ref_path),
                )
            )
        return {"accepted": accepted, **gate}

    async def _evaluate_member_candidate_holdout(
        self,
        *,
        source_cases: list[CaseMapping],
        source_eval: dict[str, Any],
        before_harness_refs_path: str,
        candidate_harness_refs_path: str,
        gate_root: Path,
        capabilities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Evaluate one frozen, unseen case with source and candidate harnesses."""
        limit = int(self.config.member_optimizer.candidate_holdout_cases)
        if limit <= 0:
            return {"status": "disabled", "case_count": 0}
        context = self.context_store.load()
        dataset_dir = str(getattr(context.current.dataset, "dataset_dir", "") or "")
        if not dataset_dir:
            return {"status": "inconclusive", "reason": "holdout_dataset_missing"}
        all_cases = _flatten_batches(self.data_loader.load(dataset_dir, epoch=self._current_epoch))
        source_ids = {str(case.get("case_id", "") or "") for case in source_cases}
        holdout_cases = [case for case in all_cases if str(case.get("case_id", "") or "") not in source_ids][:limit]
        generated_dataset: dict[str, Any] = {}
        if not holdout_cases:
            try:
                holdout_cases, generated_dataset = await self._generate_member_candidate_holdout_cases(
                    source_cases=source_cases,
                    capabilities=capabilities,
                    gate_root=gate_root,
                    limit=limit,
                )
            except Exception as exc:
                return {
                    "status": "inconclusive",
                    "reason": "holdout_generation_failed",
                    "case_count": 0,
                    "error": str(exc),
                }
        if not holdout_cases:
            return {
                "status": "inconclusive",
                "reason": "holdout_generation_returned_no_cases",
                "case_count": 0,
                **generated_dataset,
            }
        try:
            source_ref = await self.evaluator.evaluate_batch(
                cases=holdout_cases,
                team_skill_ref_path=str(source_eval.get("team_skill_ref_path", "")),
                harness_refs_path=before_harness_refs_path,
                output_dir=str(gate_root / "holdout" / "source"),
                context_path=self.context_store.context_path,
                dataset=context.current.dataset,
            )
            candidate_ref = await self.evaluator.evaluate_batch(
                cases=holdout_cases,
                team_skill_ref_path=str(source_eval.get("team_skill_ref_path", "")),
                harness_refs_path=candidate_harness_refs_path,
                output_dir=str(gate_root / "holdout" / "candidate"),
                context_path=self.context_store.context_path,
                dataset=context.current.dataset,
            )
        except Exception as exc:
            return {
                "status": "inconclusive",
                "reason": "holdout_evaluation_failed",
                "error": str(exc),
                "case_count": len(holdout_cases),
            }
        if _eval_ref_has_inconclusive_cases(source_ref) or _eval_ref_has_inconclusive_cases(candidate_ref):
            return {
                "status": "inconclusive",
                "reason": "holdout_has_error_cases",
                "source_eval_ref_path": source_ref,
                "candidate_eval_ref_path": candidate_ref,
                "case_count": len(holdout_cases),
            }
        source_failed_machine_evidence = _eval_failed_machine_evidence(source_ref)
        if source_failed_machine_evidence:
            return {
                "status": "inconclusive",
                "reason": "holdout_source_machine_evidence_failed",
                "source_eval_ref_path": source_ref,
                "candidate_eval_ref_path": candidate_ref,
                "source_failed_machine_evidence": source_failed_machine_evidence,
                "case_count": len(holdout_cases),
            }
        candidate_failed_machine_evidence = _eval_failed_machine_evidence(candidate_ref)
        source_score = _eval_score(source_ref)
        candidate_score = _eval_score(candidate_ref)
        return {
            "status": "completed",
            "case_count": len(holdout_cases),
            "case_ids": [str(case.get("case_id", "") or "") for case in holdout_cases],
            "source_eval_ref_path": source_ref,
            "candidate_eval_ref_path": candidate_ref,
            "source_score": source_score,
            "candidate_score": candidate_score,
            "score_delta": candidate_score - source_score,
            "candidate_failed_machine_evidence": candidate_failed_machine_evidence,
            **generated_dataset,
        }

    async def _generate_member_candidate_holdout_cases(
        self,
        *,
        source_cases: list[CaseMapping],
        capabilities: list[dict[str, Any]],
        gate_root: Path,
        limit: int,
    ) -> tuple[list[CaseMapping], dict[str, Any]]:
        """Generate a delayed transfer holdout only after the primary gate passes."""
        holdout_root = gate_root / "holdout" / "generated"
        capability_effects = [
            str(item.get("expected_effect", "") or "").strip()
            for item in capabilities
            if isinstance(item, dict) and str(item.get("expected_effect", "") or "").strip()
        ]
        target_roles = sorted(
            {
                str(item.get("role", "") or "").strip()
                for item in capabilities
                if isinstance(item, dict) and str(item.get("role", "") or "").strip()
            }
        )
        seed_payload = {
            "source": "member_candidate_delayed_holdout",
            "task": self.context_store.load().task,
            "quality_gaps": [
                {
                    "id": "candidate_capability_transfer",
                    "gap_type": "artifact_quality_gap",
                    "dimension": "capability transfer",
                    "severity": "high",
                    "affected_roles": target_roles,
                    "likely_surfaces": ["skill", "prompt_section"],
                    "missing_capability": "\n".join(capability_effects),
                    "data_needed_to_fix": (
                        "A structurally distinct task that exercises the same reusable "
                        "methodology without repeating the source case or artifact."
                    ),
                }
            ],
            "dataset_budget": {
                "total_cases": limit,
                "case_groups": [
                    {
                        "source_gap": "candidate_capability_transfer",
                        "case_count": limit,
                        "target_roles": target_roles,
                        "target_surfaces": ["skill", "prompt_section"],
                    }
                ],
            },
            "recommended_synthetic_tasks": [
                (
                    "Create a transfer case in a different domain and artifact context. "
                    "Do not mention the candidate patch, source case, or expected solution."
                )
            ],
            "excluded_source_case_ids": [str(case.get("case_id", "") or "") for case in source_cases],
        }
        seed_ref = write_json_mapping(holdout_root / "targeted_dataset_seed.json", seed_payload)
        generator = DatasetGenerator(
            replace(
                self.config.dataset_generator,
                known_failures_ref=seed_ref,
            )
        )
        artifact = await generator.generate(
            (
                f"{self.context_store.load().task}\n\n"
                "Generate delayed holdout cases for transfer validation. Each case must use "
                "a structurally different domain or artifact context from the source case and "
                "must not reveal the expected solution."
            ),
            str(holdout_root / "dataset"),
        )
        loader = DataLoader(self.config.data_loader)
        generated = _flatten_batches(loader.load(artifact.dataset_dir, epoch=self._current_epoch))
        source_ids = {str(case.get("case_id", "") or "") for case in source_cases}
        selected: list[CaseMapping] = []
        for index, case in enumerate(generated, start=1):
            candidate = dict(case)
            case_id = str(candidate.get("case_id", "") or f"case_{index:03d}")
            if case_id in source_ids:
                candidate["case_id"] = f"holdout_{case_id}_{index:03d}"
            selected.append(candidate)
            if len(selected) >= limit:
                break
        return selected, {
            "generated": True,
            "generated_dataset_dir": artifact.dataset_dir,
            "generated_dataset_files": list(artifact.dataset_files),
            "generated_seed_ref": seed_ref,
        }

    def _accepted_candidate_eval_ref_path(self, *, harness_refs_path: str) -> str:
        """Return the accepted gate result matching the refs promoted to current."""
        context = self.context_store.load()
        expected = str(Path(harness_refs_path).expanduser().resolve()) if harness_refs_path else ""
        for gate in reversed(list(context.metadata.get("member_candidate_gates", []))):
            if not isinstance(gate, dict) or gate.get("status") != "accepted":
                continue
            candidate_refs = str(gate.get("candidate_harness_refs_path", "") or "")
            candidate_eval = str(gate.get("candidate_eval_ref_path", "") or "")
            if not candidate_refs or not candidate_eval:
                continue
            if str(Path(candidate_refs).expanduser().resolve()) != expected:
                continue
            if _eval_ref_complete(candidate_eval):
                return candidate_eval
        return ""

    def _append_member_candidate_gate_context(self, gate: dict[str, Any]) -> None:
        context = self.context_store.load()
        existing = list(context.metadata.get("member_candidate_gates", []))
        capability_attempts = list(context.metadata.get("member_capability_attempts", []))
        capabilities = gate.get("capabilities", [])
        if isinstance(capabilities, list):
            for capability in capabilities:
                if not isinstance(capability, dict):
                    continue
                capability_attempts.append(
                    {
                        "status": str(gate.get("status", "")),
                        "reason": str(gate.get("reason", "")),
                        "source_eval_ref_path": str(gate.get("source_eval_ref_path", "")),
                        "candidate_eval_ref_path": str(gate.get("candidate_eval_ref_path", "")),
                        "capability": capability,
                    }
                )
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "member_candidate_gates": [*existing, gate],
                    "latest_member_candidate_gate": gate,
                    "member_capability_attempts": capability_attempts,
                },
            )
        )

    def _save_member_candidate_gate_context(
        self,
        *,
        source_eval_ref_path: str,
        candidate_eval_ref_path: str,
        before_harness_refs_path: str,
        candidate_harness_refs_path: str,
        accepted: bool,
        reason: str,
        source_score: float | None,
        candidate_score: float | None,
    ) -> None:
        self._append_member_candidate_gate_context(
            {
                "source_eval_ref_path": source_eval_ref_path,
                "candidate_eval_ref_path": candidate_eval_ref_path,
                "before_harness_refs_path": before_harness_refs_path,
                "candidate_harness_refs_path": candidate_harness_refs_path,
                "source_score": source_score,
                "candidate_score": candidate_score,
                "status": "accepted" if accepted else "rejected",
                "reason": reason,
            }
        )

    async def _curate_epoch_replay_dataset(self, *, epoch: int, eval_ref_path: str) -> None:
        """Mine failed full-evaluation cases into a replay dataset artifact."""
        artifact = self.dataset_curator.curate(
            eval_ref_path=eval_ref_path,
            output_dir=str(self.workspace_paths.dataset_curation_dir(epoch)),
        )
        if artifact.status == "disabled":
            return
        artifact_data = asdict(artifact)
        if artifact.report_path:
            self.dataset_curation_ref_paths.append(artifact.report_path)
        context = self.context_store.load()
        existing = list(context.metadata.get("dataset_curation_refs", []))
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "dataset_curation_refs": [
                        *existing,
                        artifact_data,
                    ],
                    "latest_dataset_curation_report_path": artifact.report_path,
                    "latest_replay_dataset_file": artifact.dataset_file,
                    "latest_targeted_dataset_seed_file": artifact.targeted_seed_file,
                },
            )
        )

    async def _finalize_epoch_experiences(
        self,
        *,
        epoch: int,
        eval_ref_path: str,
        score: float | None,
        improved: bool,
        confirmation_mode: str = "epoch_full_evaluation",
    ) -> None:
        """Promote or reject batch experiences after epoch full evaluation."""
        if not self._epoch_experience_ref_paths:
            return
        if score is None:
            status = "expired"
            reason = f"{confirmation_mode}_missing_score"
        elif improved:
            status = "accepted"
            reason = f"{confirmation_mode}_improved_best"
        else:
            status = "rejected"
            reason = f"{confirmation_mode}_no_best_improvement"

        updated_refs: list[dict[str, Any]] = []
        for experience_ref_path in self._epoch_experience_ref_paths:
            await self.optimization_experience_learner.update_experience_status(
                experience_ref_path,
                status,
                reason=reason,
                metadata={
                    "confirmation_epoch": epoch,
                    "confirmation_eval_ref_path": eval_ref_path,
                    "confirmation_score": score,
                    "confirmation_mode": confirmation_mode,
                },
            )
            updated_refs.append(
                {
                    "experience_ref_path": experience_ref_path,
                    "learning_status": status,
                    "reason": reason,
                }
            )

        context = self.context_store.load()
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "latest_experience_status_updates": updated_refs,
                    "latest_experience_confirmation_epoch": epoch,
                    "latest_experience_confirmation_mode": confirmation_mode,
                },
            )
        )

    def _save_member_gate_context(
        self,
        *,
        eval_ref_path: str,
        analysis_ref_path: str,
        reason: str,
    ) -> None:
        """Record why Member Harness optimization was skipped for this batch."""
        context = self.context_store.load()
        skipped = [
            *context.metadata.get("member_optimizer_skips", []),
            {
                "eval_ref_path": eval_ref_path,
                "analysis_ref_path": analysis_ref_path,
                "reason": reason,
            },
        ]
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "member_optimizer_skips": skipped,
                },
            )
        )

    def _save_optimization_issue_route(
        self,
        *,
        eval_ref_path: str,
        analysis_ref_path: str,
        source_stage: str,
        target_scope: str,
        route: str,
        status: str,
        reason: str,
    ) -> None:
        """Record a cross-stage analysis route without changing artifact ownership."""
        context = self.context_store.load()
        routes = [
            *context.metadata.get("optimization_issue_routes", []),
            {
                "eval_ref_path": eval_ref_path,
                "analysis_ref_path": analysis_ref_path,
                "source_stage": source_stage,
                "target_scope": target_scope,
                "route": route,
                "status": status,
                "reason": reason,
            },
        ]
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "optimization_issue_routes": routes,
                },
            )
        )

    def _analysis_ref_for_eval(self, eval_ref_path: str) -> str:
        """Return the analysis artifact paired with an evaluation ref."""
        if eval_ref_path in self.analysis_ref_by_eval_ref_path:
            return self.analysis_ref_by_eval_ref_path[eval_ref_path]
        eval_ref = _load_eval_ref(eval_ref_path)
        analysis_ref_path = str(eval_ref.get("analysis_ref_path", ""))
        if analysis_ref_path:
            self.analysis_ref_by_eval_ref_path[eval_ref_path] = analysis_ref_path
        return analysis_ref_path

    @staticmethod
    def _write_member_analysis_input(eval_ref_path: str) -> str:
        """Write a lightweight analysis artifact for the member optimizer handoff."""
        analysis_dir = _analysis_dir_for_eval_ref(eval_ref_path)
        index = 1
        while True:
            analysis_path = analysis_dir / f"member_analysis_{index:03d}.yaml"
            if not analysis_path.exists():
                break
            index += 1
        payload = {
            "analysis_id": analysis_path.stem,
            "source_eval_ref_path": eval_ref_path,
            "issues": [
                {
                    "member_name": "team_leader",
                    "role": "leader",
                    "summary": "Mock issue handoff generated from latest evaluation result.",
                    "evidence": {
                        "eval_ref_path": eval_ref_path,
                        "average_score": _eval_score(eval_ref_path),
                    },
                }
            ],
        }
        return write_yaml_mapping(analysis_path, payload)

    def _iter_dataset_batches(self, dataset_dir: Path, *, epoch: int) -> Iterator[list[CaseMapping]]:
        """Yield DataLoader batches without retaining the full dataset in orchestrator state."""
        for batch in self.data_loader.load(str(dataset_dir), epoch=epoch):
            self._save_batch_plan_context()
            yield batch
        self._save_batch_plan_context()

    def _save_batch_plan_context(self) -> None:
        """Record the batch plan reference exposed by DataLoader."""
        batch_plan_path = str(getattr(self.data_loader, "batch_plan_path", "") or "")
        if not batch_plan_path:
            return
        context = self.context_store.load()
        if context.metadata.get("batch_plan_path") == batch_plan_path:
            return
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "batch_plan_path": batch_plan_path,
                },
            )
        )

    def _save_dataset_context(
        self,
        task: str,
        dataset: DatasetArtifact,
        *,
        team_skill_ref_path: str,
        harness_refs_path: str,
        seed_evaluation: dict[str, Any] | None = None,
    ) -> None:
        """Persist the current dataset reference in orchestrator context."""
        context = self.context_store.create(
            task,
            strategy=RunStrategyMetadata(
                evaluation_strategy=self.config.scheduling.evaluation_strategy,
                coordination_strategy=self.config.scheduling.coordination_strategy,
                promotion_policy=self.config.scheduling.promotion_policy,
                full_evaluation_enabled=self.config.scheduling.full_evaluation_enabled,
                strategy_name="hybrid_team_first_single_pass",
            ),
        )
        metadata = {
            **context.metadata,
            "batch_plan_path": str(getattr(self.data_loader, "batch_plan_path", "") or ""),
            "batch_balance_keys": list(self.config.data_loader.batch_balance_keys),
            "full_evaluation_enabled": self.config.scheduling.full_evaluation_enabled,
        }
        if seed_evaluation:
            metadata["seed_evaluation"] = seed_evaluation
        if self.initial_harness_refs_path:
            metadata["initial_harness_refs_path"] = self.initial_harness_refs_path
        if self._reused_best_context_path:
            metadata["reused_best_context_path"] = self._reused_best_context_path
            metadata["reused_best_context_mode"] = "best_refs_only"
        if self._workspace_run_id:
            metadata["workspace_run_id"] = self._workspace_run_id
        context = replace(
            context,
            current=CurrentArtifactRefs(
                dataset=dataset,
                team_skill_ref_path=team_skill_ref_path,
                harness_refs_path=harness_refs_path,
                harness_refs=_read_harness_refs_for_context(harness_refs_path),
            ),
            metadata=metadata,
        )
        self.context_store.save(context)

    def _save_initial_context(
        self,
        task: str,
        *,
        team_skill_ref_path: str,
        harness_refs_path: str,
    ) -> None:
        """Persist initial refs before the seed task has produced a dataset."""
        context = self.context_store.create(
            task,
            strategy=RunStrategyMetadata(
                evaluation_strategy=self.config.scheduling.evaluation_strategy,
                coordination_strategy=self.config.scheduling.coordination_strategy,
                promotion_policy=self.config.scheduling.promotion_policy,
                full_evaluation_enabled=self.config.scheduling.full_evaluation_enabled,
                strategy_name="hybrid_team_first_single_pass",
            ),
        )
        metadata = {
            **context.metadata,
            "batch_balance_keys": list(self.config.data_loader.batch_balance_keys),
            "full_evaluation_enabled": self.config.scheduling.full_evaluation_enabled,
        }
        if self.initial_harness_refs_path:
            metadata["initial_harness_refs_path"] = self.initial_harness_refs_path
        if self._reused_best_context_path:
            metadata["reused_best_context_path"] = self._reused_best_context_path
            metadata["reused_best_context_mode"] = "best_refs_only"
        if self._workspace_run_id:
            metadata["workspace_run_id"] = self._workspace_run_id
        self.context_store.save(
            replace(
                context,
                current=CurrentArtifactRefs(
                    dataset=None,
                    team_skill_ref_path=team_skill_ref_path,
                    harness_refs_path=harness_refs_path,
                    harness_refs=_read_harness_refs_for_context(harness_refs_path),
                ),
                metadata=metadata,
            )
        )

    def _record_seed_evaluation(self, seed_evaluation: dict[str, Any]) -> None:
        """Attach seed evaluation status to the current run context."""
        context = self.context_store.load()
        self.context_store.save(
            replace(
                context,
                current=replace(
                    context.current,
                    eval_ref_path=str(seed_evaluation.get("eval_ref_path", "") or "") or context.current.eval_ref_path,
                ),
                metadata={
                    **context.metadata,
                    "seed_evaluation": seed_evaluation,
                },
            )
        )

    def _save_epoch_context(
        self,
        epoch: int,
        phase: OrchestratorPhase,
    ) -> None:
        """Persist current epoch and phase in orchestrator context."""
        context = self.context_store.load()
        self.context_store.save(replace(context, epoch=epoch, phase=phase))

    def _save_epoch_evaluation_context(
        self,
        *,
        eval_ref_paths: list[str],
        phase: str,
        score: float | None,
    ) -> None:
        """Record all batch evaluation refs for the current epoch."""
        if not eval_ref_paths:
            return
        context = self.context_store.load()
        current = replace(context.current, eval_ref_path=eval_ref_paths[-1])
        history = replace(
            context.history,
            evaluations=[
                *context.history.evaluations,
                *[
                    EvaluationHistoryItem(
                        eval_ref_path=eval_ref_path,
                        phase=phase,
                        score=_eval_score(eval_ref_path),
                    )
                    for eval_ref_path in eval_ref_paths
                ],
            ],
        )
        self.context_store.save(
            replace(
                context,
                phase=OrchestratorPhase.EVALUATING,
                current=current,
                history=history,
            )
        )

    def _record_full_evaluation_skipped(self, *, epoch: int, eval_ref_path: str) -> None:
        """Record that this epoch intentionally ended without full-dataset evaluation."""
        context = self.context_store.load()
        skipped_epochs = list(context.metadata.get("full_evaluation_skipped_epochs", []))
        if epoch not in skipped_epochs:
            skipped_epochs.append(epoch)
        self.context_store.save(
            replace(
                context,
                current=replace(context.current, eval_ref_path=eval_ref_path or context.current.eval_ref_path),
                metadata={
                    **context.metadata,
                    "full_evaluation_enabled": False,
                    "full_evaluation_skipped_epochs": skipped_epochs,
                    "latest_epoch_terminal_eval_ref_path": eval_ref_path,
                },
            )
        )

    def _save_epoch_checkpoint(
        self,
        *,
        epoch: int,
        eval_ref_path: str,
        score: float | None,
        promotion_source: str = "epoch_full_evaluation",
        force_best: bool = False,
    ) -> str:
        """Save one epoch checkpoint and update best only on full-eval improvement."""
        context = self.context_store.load()
        checkpoint_id = f"epoch_{epoch:03d}"
        checkpoint_path = str((Path(self.checkpoint_manager.checkpoint_dir).expanduser().resolve() / checkpoint_id))
        best_score = context.best.score
        improved = force_best or (score is not None and (best_score is None or score > best_score))
        metadata = {
            **context.metadata,
            "latest_checkpoint_path": checkpoint_path,
            "latest_checkpoint_epoch": epoch,
            "checkpoint_scope": "epoch",
            "source_eval_ref_path": eval_ref_path,
            "best_promotion_source": promotion_source,
            "strategy_name": context.strategy.strategy_name,
            "evaluation_strategy": context.strategy.evaluation_strategy,
            "coordination_strategy": context.strategy.coordination_strategy,
            "promotion_policy": context.strategy.promotion_policy,
            "full_evaluation_enabled": context.strategy.full_evaluation_enabled,
        }
        best = context.best
        if improved:
            best_harness_refs_path, best_harness_refs = self.checkpoint_manager.snapshot_harness_refs(
                checkpoint_id=checkpoint_id,
                harness_refs_path=context.current.harness_refs_path,
                harness_refs=context.current.harness_refs,
            )
            best = replace(
                context.best,
                team_skill_ref_path=context.current.team_skill_ref_path,
                harness_refs_path=best_harness_refs_path,
                harness_refs=best_harness_refs,
                eval_ref_path=eval_ref_path,
                score=score,
            )
            metadata.update(
                {
                    "best_checkpoint_path": checkpoint_path,
                    "best_checkpoint_epoch": epoch,
                }
            )
        self._update_team_skill_lock_state(improved=improved)
        metadata.update(
            {
                "team_skill_locked": self.team_skill_locked,
                "team_skill_no_improvement_epochs": self._team_skill_no_improvement_epochs,
            }
        )

        now = datetime.now(UTC).astimezone()
        context = replace(
            context,
            phase=OrchestratorPhase.SAVING_CHECKPOINT,
            best=best,
            metadata=metadata,
            last_checkpoint_at=now,
            updated_at=now,
        )
        self.context_store.save(context)
        saved_checkpoint_path = self.checkpoint_manager.save(context, checkpoint_id=checkpoint_id)
        self._emit(
            phase=OrchestratorPhase.SAVING_CHECKPOINT.value,
            epoch=epoch,
            stage="checkpoint",
            score=score,
            improved=improved,
            message=f"checkpoint saved: {checkpoint_id}",
            metrics={"best_score": best.score},
            artifacts={
                "checkpoint_path": saved_checkpoint_path,
                "eval_ref_path": eval_ref_path,
            },
        )
        return saved_checkpoint_path

    def _update_team_skill_lock_state(self, *, improved: bool) -> None:
        """Lock Team Skill optimization after repeated non-improving epochs."""
        if not self._team_skill_optimized_in_epoch:
            return
        if improved:
            self._team_skill_no_improvement_epochs = 0
            return
        self._team_skill_no_improvement_epochs += 1
        if self._team_skill_no_improvement_epochs >= 2:
            self.team_skill_locked = True

    def _save_effective_team_skill_context(
        self,
        *,
        before_team_skill_ref_path: str,
        after_team_skill_ref_path: str,
        eval_ref_path: str,
        score: float | None,
    ) -> None:
        """Persist accepted Team Skill candidate references in orchestrator context."""
        context = self.context_store.load()
        current = replace(
            context.current,
            team_skill_ref_path=after_team_skill_ref_path,
            eval_ref_path=eval_ref_path,
        )
        history = replace(
            context.history,
            team_skill_optimizations=[
                *context.history.team_skill_optimizations,
                TeamSkillOptimizationHistoryItem(
                    before_team_skill_ref_path=before_team_skill_ref_path,
                    after_team_skill_ref_path=after_team_skill_ref_path,
                    eval_ref_path=eval_ref_path,
                ),
            ],
        )
        self.context_store.save(
            replace(
                context,
                phase=OrchestratorPhase.OPTIMIZING_TEAM_SKILL,
                current=current,
                history=history,
            )
        )

    def _save_effective_member_context(
        self,
        *,
        before_harness_refs_path: str,
        after_harness_refs_path: str,
        eval_ref_path: str,
        role: str,
    ) -> None:
        """Persist accepted member harness candidate references in orchestrator context."""
        context = self.context_store.load()
        after_harness_refs = _read_harness_refs_for_context(after_harness_refs_path)
        if not after_harness_refs and Path(after_harness_refs_path).expanduser().is_dir() and role:
            after_harness_refs = {role: str(Path(after_harness_refs_path).expanduser().resolve())}
        before_harness_refs = context.current.harness_refs or _read_harness_refs_for_context(before_harness_refs_path)
        harness_refs = after_harness_refs or before_harness_refs
        changed_roles = [
            role_name
            for role_name, after_ref in harness_refs.items()
            if before_harness_refs.get(role_name, "") != after_ref
        ]
        history_role = ",".join(changed_roles) if changed_roles else role
        before_role_harness_ref_path = ";".join(
            f"{role_name}={before_harness_refs.get(role_name, '')}" for role_name in changed_roles
        )
        after_role_harness_ref_path = ";".join(
            f"{role_name}={harness_refs.get(role_name, '')}" for role_name in changed_roles
        )
        if not before_role_harness_ref_path:
            before_role_harness_ref_path = before_harness_refs.get(role, "")
        if not after_role_harness_ref_path:
            after_role_harness_ref_path = harness_refs.get(role, after_harness_refs_path)
        current = replace(
            context.current,
            harness_refs_path=after_harness_refs_path,
            harness_refs=harness_refs,
            eval_ref_path=eval_ref_path,
        )
        history = replace(
            context.history,
            member_optimizations=[
                *context.history.member_optimizations,
                MemberOptimizationHistoryItem(
                    before_harness_refs_path=before_harness_refs_path,
                    after_harness_refs_path=after_harness_refs_path,
                    eval_ref_path=eval_ref_path,
                    role=history_role,
                    before_role_harness_ref_path=before_role_harness_ref_path,
                    after_role_harness_ref_path=after_role_harness_ref_path,
                ),
            ],
        )
        self.context_store.save(
            replace(
                context,
                phase=OrchestratorPhase.OPTIMIZING_MEMBER,
                current=current,
                history=history,
            )
        )

    async def resume(self, checkpoint_id: str) -> str:
        """TODO: resume a run from a checkpoint and return ``result_ref.yaml``."""
        raise NotImplementedError("TODO: resume optimization from checkpoint")

    async def pause(self) -> str:
        """TODO: persist a paused context checkpoint and return its path."""
        raise NotImplementedError("TODO: pause optimization run")

    async def save_checkpoint(self) -> str:
        """Persist the current context as a checkpoint snapshot."""
        context = self.context_store.load()
        return self.checkpoint_manager.save(context)

    async def load_checkpoint(self, checkpoint_id: str) -> str:
        """Load checkpoint state and overwrite the active context."""
        context = self.checkpoint_manager.load(checkpoint_id)
        self.context_store.save(context)
        return self.context_store.context_path

    def list_checkpoints(self) -> list[str]:
        """List checkpoints available for this workspace."""
        return self.checkpoint_manager.list()

    async def apply_intervention(self, intervention_path: str) -> None:
        """TODO: apply a validated human intervention file to the current run context."""
        raise NotImplementedError("TODO: apply optimization intervention")

    def _should_stop_optimization(
        self,
        *,
        epoch_score: float | None,
        epoch: int,
    ) -> bool:
        """Return whether optimization should stop after this epoch checkpoint."""
        if self._is_target_score_reached(epoch_score):
            return True
        return epoch >= self.config.max_epochs

    def _is_target_score_reached(self, epoch_score: float | None) -> bool:
        """Return whether the aggregate epoch score reaches the configured target."""
        if epoch_score is None:
            return False
        return epoch_score >= self.config.evaluator.success_score

    def _write_run_report(self) -> None:
        """Write human-readable and machine-readable summaries for the run."""
        context = self.context_store.load()
        report_path = self.workspace_paths.root / "run_report.yaml"
        markdown_path = self.workspace_paths.root / "run_report.md"
        report = _build_run_report(
            context=self.context_store.load(),
            team_skill_optimization_dir=self.workspace_paths.team_skill_dir(),
            member_optimization_dir=self.workspace_paths.member_optimization_dir(),
            config=self.config,
            llm_usage_summary=summarize_llm_usage_file(
                self.llm_usage_path,
                run_id=self.llm_usage_run_id,
            ),
        )
        write_yaml_mapping(report_path, report)
        markdown_path.write_text(_format_run_report_markdown(report), encoding="utf-8")
        self.context_store.save(
            replace(
                context,
                metadata={
                    **context.metadata,
                    "run_report_path": str(report_path.resolve()),
                    "run_report_markdown_path": str(markdown_path.resolve()),
                },
            )
        )


__all__ = [
    "OptimizationOrchestrator",
]


def _build_run_report(
    *,
    context,
    team_skill_optimization_dir: Path,
    member_optimization_dir: Path,
    config: Any | None = None,
    llm_usage_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact report from persisted run artifacts."""
    return {
        "task_id": context.task_id,
        "task": context.task,
        "phase": context.phase.value,
        "epoch": context.epoch,
        "score_semantics": {
            "best_scope": "generated_dataset_cases_only",
            "seed_score_comparable_to_best": False,
            "candidate_gate_scores_comparable": True,
            "note": (
                "Only source/candidate scores inside the same candidate gate use the "
                "same frozen cases and are valid optimization-effect comparisons."
            ),
        },
        "optimization_policy": _run_report_optimization_policy(config),
        "best": {
            "score": context.best.score,
            "eval_ref_path": context.best.eval_ref_path,
            "team_skill_ref_path": context.best.team_skill_ref_path,
            "harness_refs_path": context.best.harness_refs_path,
            "harness_refs": context.best.harness_refs,
        },
        "current": {
            "team_skill_ref_path": context.current.team_skill_ref_path,
            "harness_refs_path": context.current.harness_refs_path,
            "harness_refs": context.current.harness_refs,
            "eval_ref_path": context.current.eval_ref_path,
        },
        "evaluations": [asdict(item) for item in context.history.evaluations],
        "accepted_team_skill_optimizations": [asdict(item) for item in context.history.team_skill_optimizations],
        "accepted_member_optimizations": [asdict(item) for item in context.history.member_optimizations],
        "team_skill_optimization_attempts": _collect_team_skill_attempts_for_context(
            context,
            team_skill_optimization_dir,
        ),
        "member_optimization_attempts": _collect_member_optimization_attempts(member_optimization_dir),
        "member_candidate_gates": list(context.metadata.get("member_candidate_gates", [])),
        "optimization_routes": list(context.metadata.get("optimization_issue_routes", [])),
        "team_skill_optimizer_skips": list(context.metadata.get("team_skill_optimizer_skips", [])),
        "member_optimizer_skips": list(context.metadata.get("member_optimizer_skips", [])),
        "experience_status_updates": list(context.metadata.get("latest_experience_status_updates", [])),
        "optimization_consumption": _collect_optimization_consumption_report(context),
        "llm_usage": llm_usage_summary or {},
    }


def _run_report_optimization_policy(config: Any | None) -> dict[str, Any]:
    if config is None:
        return {}
    return {
        "team_skill_frozen": bool(config.freeze_team_skill or config.team_skill_optimizer.freeze),
        "member_allowed_action_groups": list(config.member_optimizer.allowed_action_groups),
        "member_allowed_prompt_surfaces": list(config.member_optimizer.allowed_prompt_surfaces),
        "member_max_actions_per_plan": config.member_optimizer.max_actions_per_plan,
        "candidate_holdout_cases": config.member_optimizer.candidate_holdout_cases,
    }


def _collect_team_skill_attempts_for_context(
    context: OrchestratorRunContext,
    root: Path,
) -> list[dict[str, Any]]:
    """Collect Team Skill optimizer attempts that belong to the current run context."""
    attempts = _collect_team_skill_attempts(root)
    if not attempts:
        return []
    eval_ref_paths = _context_eval_ref_path_variants(context)
    if not eval_ref_paths:
        return []
    matching_attempts = []
    for attempt in attempts:
        eval_matches = _path_variants(attempt.get("eval_ref_path", "")) & eval_ref_paths
        source_matches = _path_variants(attempt.get("source_eval_ref_path", "")) & eval_ref_paths
        if eval_matches or source_matches:
            matching_attempts.append(attempt)
    return matching_attempts


def _collect_team_skill_attempts(root: Path) -> list[dict[str, Any]]:
    """Collect Team Skill optimizer attempts from artifact refs."""
    attempts: list[dict[str, Any]] = []
    for ref_path in sorted(root.rglob("team_skill_optimization_ref.yaml")):
        data = read_yaml_mapping(ref_path)
        eval_ref_path = data.get("eval_ref_path") or data.get("source_eval_ref_path", "")
        analysis_result_path = data.get("analysis_result_path") or data.get("source_analysis_result_path", "")
        candidate_path = data.get("candidate_path") or data.get("optimized_team_skill_ref_path", "")
        published_path = data.get("published_path") or data.get("returned_team_skill_ref_path", "")
        attempts.append(
            {
                "ref_path": str(ref_path.resolve()),
                "status": data.get("status", ""),
                "eval_ref_path": eval_ref_path,
                "source_eval_ref_path": data.get("source_eval_ref_path", eval_ref_path),
                "analysis_result_path": analysis_result_path,
                "candidate_path": candidate_path,
                "published_path": published_path,
                "metadata": data.get("metadata", {}),
            }
        )
    return attempts


def _context_eval_ref_path_variants(context: OrchestratorRunContext) -> set[str]:
    """Return normalized eval refs that are part of the persisted run context."""
    paths: set[str] = set()
    paths.update(_path_variants(context.current.eval_ref_path))
    paths.update(_path_variants(context.best.eval_ref_path))
    for item in context.history.evaluations:
        paths.update(_path_variants(item.eval_ref_path))
    for item in context.history.team_skill_optimizations:
        paths.update(_path_variants(item.eval_ref_path))
    for item in context.history.member_optimizations:
        paths.update(_path_variants(item.eval_ref_path))
    for route in context.metadata.get("optimization_issue_routes", []):
        if isinstance(route, dict):
            paths.update(_path_variants(route.get("eval_ref_path", "")))
    return paths


def _path_variants(path: Any) -> set[str]:
    """Return comparable string forms for persisted artifact paths."""
    value = str(path or "").strip()
    if not value:
        return set()
    variants = {value}
    try:
        variants.add(str(Path(value).expanduser().resolve()))
    except (OSError, RuntimeError):
        pass
    return variants


def _collect_member_optimization_attempts(root: Path) -> list[dict[str, Any]]:
    """Collect Member optimizer attempts from artifact refs."""
    attempts: list[dict[str, Any]] = []
    for ref_path in sorted(root.rglob("member_optimization_ref.yaml")):
        data = read_yaml_mapping(ref_path)
        verification = read_json_mapping(str(data.get("verification_path", "") or ""))
        candidate_ready_roles = data.get(
            "candidate_ready_roles",
            data.get("published_roles", []),
        )
        attempts.append(
            {
                "ref_path": str(ref_path.resolve()),
                "status": data.get("status", ""),
                "candidate_ready_roles": candidate_ready_roles,
                "candidate_published_roles": candidate_ready_roles,
                "published_roles": data.get("promoted_roles", []),
                "promoted_roles": data.get("promoted_roles", []),
                "promotion_status": data.get("promotion_status", "not_evaluated"),
                "failed_roles": data.get("failed_roles", []),
                "skipped_roles": data.get("skipped_roles", []),
                "verification_status": data.get("verification_status", ""),
                "optimized_harness_refs_path": data.get("optimized_harness_refs_path", ""),
                "plan_path": data.get("plan_path", ""),
                "planned_actions": _planned_actions(str(data.get("plan_path", "") or "")),
                "verification_summary": _verification_summary(verification),
                "role_results": data.get("role_results", []),
            }
        )
    return attempts


def _planned_actions(plan_path: str) -> list[dict[str, Any]]:
    """Read action plan details relevant for a run report."""
    plan = read_yaml_mapping(plan_path)
    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        return []
    result: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        result.append(
            {
                "action_id": action.get("action_id", action.get("id", "")),
                "role": action.get("role", ""),
                "action_group": action.get("action_group", action.get("group", "")),
                "operation": action.get("operation", ""),
                "target_path": action.get("target_path", ""),
                "declared_write_paths": action.get("declared_write_paths", []),
                "rationale": action.get("rationale", ""),
            }
        )
    return result


def _verification_summary(verification: dict[str, Any]) -> dict[str, Any]:
    """Keep report verification details small but actionable."""
    if not verification:
        return {}
    checks = verification.get("checks", [])
    failed_checks: list[dict[str, Any]] = []
    if isinstance(checks, list):
        failed_checks = [
            {
                "name": check.get("name", ""),
                "status": check.get("status", ""),
                "message": check.get("message", ""),
            }
            for check in checks
            if isinstance(check, dict) and str(check.get("status", "")) != "passed"
        ]
    return {
        "status": verification.get("status", ""),
        "passed": verification.get("passed"),
        "failed_checks": failed_checks,
    }


def _collect_optimization_consumption_report(context) -> dict[str, Any]:
    """Report whether active member skill/tool resources were observed in traces."""
    harness_refs = _active_harness_refs(context)
    eval_ref_paths = _context_eval_ref_paths(context)
    trace_observations = _scan_consumption_trace_observations(eval_ref_paths)
    roles: dict[str, Any] = {}
    for role, harness_ref_path in sorted(harness_refs.items()):
        harness_ref_text = str(harness_ref_path or "")
        harness_dir = Path(harness_ref_text) if harness_ref_text else None
        harness_exists = bool(harness_dir and harness_dir.is_dir())
        role_report = {
            "harness_ref_path": harness_ref_text,
            "harness_exists": harness_exists,
            "skills": [],
            "tools": [],
        }
        if harness_dir is not None and harness_exists:
            role_report["skills"] = [
                _resource_skill_report(skill, trace_observations) for skill in _discover_harness_skills(harness_dir)
            ]
            role_report["tools"] = [
                _resource_tool_report(tool, trace_observations) for tool in _discover_harness_tools(harness_dir)
            ]
        roles[str(role)] = role_report
    return {
        "harness_refs_path": str(getattr(context.current, "harness_refs_path", "") or ""),
        "evaluations_scanned": eval_ref_paths,
        "roles": roles,
    }


def _active_harness_refs(context) -> dict[str, str]:
    """Return current harness refs, falling back to current_harness_refs.yaml."""
    refs = dict(getattr(context.current, "harness_refs", {}) or {})
    if refs:
        return {str(role): str(path) for role, path in refs.items()}
    refs_path = str(getattr(context.current, "harness_refs_path", "") or "")
    data = read_yaml_mapping(refs_path)
    loaded = data.get("harness_refs", {})
    if isinstance(loaded, dict):
        return {str(role): str(path) for role, path in loaded.items()}
    return {}


def _context_eval_ref_paths(context) -> list[str]:
    """Collect unique evaluation refs that can contain resource-consumption traces."""
    paths: list[str] = []
    current_eval = str(getattr(context.current, "eval_ref_path", "") or "")
    if current_eval:
        paths.append(current_eval)
    for item in getattr(context.history, "evaluations", []) or []:
        path = str(getattr(item, "eval_ref_path", "") or "")
        if path:
            paths.append(path)
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _discover_harness_skills(harness_dir: Path) -> list[dict[str, str]]:
    """Discover package-local SKILL.md files under an ExpertHarness package."""
    skills_root = harness_dir / "skills"
    if not skills_root.is_dir():
        return []
    skills: list[dict[str, str]] = []
    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        metadata = _read_skill_frontmatter(skill_md)
        skill_dir_name = skill_md.parent.name
        skills.append(
            {
                "name": skill_dir_name,
                "frontmatter_name": metadata.get("name", ""),
                "description": metadata.get("description", ""),
                "path": str(skill_md.resolve()),
            }
        )
    return skills


def _read_skill_frontmatter(skill_md: Path) -> dict[str, str]:
    """Read minimal SKILL.md frontmatter fields used for reporting."""
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"name", "description"}:
            metadata[key] = value.strip().strip("\"'")
    return metadata


def _discover_harness_tools(harness_dir: Path) -> list[dict[str, str]]:
    """Discover package-local tools declared in tools/tools.yaml."""
    manifest = read_yaml_mapping(harness_dir / "tools" / "tools.yaml")
    entries = manifest.get("tools", [])
    if not isinstance(entries, list):
        return []
    tools: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_ref = str(entry.get("file", "") or "")
        class_name = str(entry.get("class_name", "") or "")
        if not file_ref and not class_name:
            continue
        tool_name = Path(file_ref).stem if file_ref else _camel_to_snake(class_name)
        tools.append(
            {
                "name": tool_name,
                "class_name": class_name,
                "file": file_ref,
                "path": str((harness_dir / file_ref).resolve()) if file_ref else "",
            }
        )
    return tools


def _resource_skill_report(
    skill: dict[str, str],
    observations: dict[str, Any],
) -> dict[str, Any]:
    """Attach trace observation flags to one skill resource."""
    names = [skill.get("name", ""), skill.get("frontmatter_name", "")]
    normalized_names = {name for name in names if name}
    skill_reads = observations.get("skill_reads", set())
    return {
        **skill,
        "observed": {
            "skill_file_read": any(name in skill_reads for name in normalized_names),
        },
    }


def _resource_tool_report(
    tool: dict[str, str],
    observations: dict[str, Any],
) -> dict[str, Any]:
    """Attach trace observation flags to one tool resource."""
    tool_calls = observations.get("tool_calls", {})
    tool_name = str(tool.get("name", "") or "")
    class_tool_name = _camel_to_snake(str(tool.get("class_name", "") or ""))
    candidates = {name for name in (tool_name, class_tool_name) if name}
    return {
        **tool,
        "observed": {
            "tool_called": any(int(tool_calls.get(name, 0) or 0) > 0 for name in candidates),
        },
    }


def _scan_consumption_trace_observations(eval_ref_paths: list[str]) -> dict[str, Any]:
    """Scan evaluation trace files for actual skill reads and tool calls."""
    observations: dict[str, Any] = {
        "skill_reads": set(),
        "tool_calls": {},
    }
    for eval_ref_path in eval_ref_paths:
        for trace_file in _trace_files_for_eval_ref(eval_ref_path):
            _scan_consumption_trace_file(trace_file, observations)
    return observations


def _trace_files_for_eval_ref(eval_ref_path: str) -> list[Path]:
    """Return bounded trace files referenced by one eval_ref."""
    eval_ref = read_yaml_mapping(eval_ref_path)
    files: list[Path] = []
    for case in eval_ref.get("cases", []) or []:
        if not isinstance(case, dict):
            continue
        trace_path = str(case.get("trace_path", "") or "")
        if trace_path:
            path = Path(trace_path)
            if path.is_file():
                files.append(path)
        result_path = str(case.get("result_path", "") or "")
        if result_path:
            case_dir = Path(result_path).parent
            trace_dir = case_dir / "tr"
            if trace_dir.is_dir():
                files.extend(sorted(trace_dir.glob("*.jsonl")))
    return files[:200]


def _scan_consumption_trace_file(path: Path, observations: dict[str, Any]) -> None:
    """Scan one trace file for tool calls relevant to skill/tool consumption."""
    try:
        with path.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for call in _iter_trace_tool_calls(payload):
                    name = str(call.get("name", "") or "")
                    if name:
                        tool_calls = observations.setdefault("tool_calls", {})
                        tool_calls[name] = int(tool_calls.get(name, 0) or 0) + 1
                    args_text = str(call.get("arguments", "") or "")
                    if name == "skill_tool" or (name == "read_file" and "SKILL.md" in args_text):
                        for token in _skill_tokens_from_arguments(args_text):
                            observations.setdefault("skill_reads", set()).add(token)
    except OSError:
        return


def _iter_trace_tool_calls(payload: Any) -> Iterator[dict[str, str]]:
    """Yield normalized tool calls from nested trace payloads."""
    if isinstance(payload, dict):
        calls = payload.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                normalized = _normalize_trace_tool_call(call)
                if normalized:
                    yield normalized
        for value in payload.values():
            yield from _iter_trace_tool_calls(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_trace_tool_calls(item)


def _normalize_trace_tool_call(call: Any) -> dict[str, str]:
    """Return tool call name and raw argument text from one trace entry."""
    if not isinstance(call, dict):
        return {}
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or call.get("name") or "")
    arguments = function.get("arguments", call.get("arguments", ""))
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, default=str)
    return {"name": name, "arguments": arguments}


def _skill_tokens_from_arguments(arguments: str) -> set[str]:
    """Extract likely skill names from a SKILL.md read path or skill_tool args."""
    tokens: set[str] = set()
    try:
        data = json.loads(arguments)
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict):
        for key in ("skill_name", "skill"):
            value = str(data.get(key, "") or "").strip()
            if value:
                tokens.add(Path(value.replace("\\", "/")).name)
        for key in ("path", "file_path", "relative_file_path"):
            value = str(data.get(key, "") or "")
            if value:
                tokens.update(_skill_tokens_from_path(value))
    tokens.update(_skill_tokens_from_path(arguments))
    return {token for token in tokens if token and token != "SKILL.md"}


def _skill_tokens_from_path(value: str) -> set[str]:
    """Extract directory/name tokens around SKILL.md from a path-like string."""
    normalized = value.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    tokens: set[str] = set()
    for index, part in enumerate(parts):
        if part == "SKILL.md" and index > 0:
            tokens.add(parts[index - 1])
    return tokens


def _camel_to_snake(value: str) -> str:
    """Convert a simple class name to snake_case without regex dependencies."""
    chars: list[str] = []
    for char in value:
        if char.isupper() and chars:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars).strip("_")


def _format_run_report_markdown(report: dict[str, Any]) -> str:
    """Render the run report for humans."""
    lines = [
        "# Auto Coordinating Harness Run Report",
        "",
        f"- Task ID: {report.get('task_id', '')}",
        f"- Phase: {report.get('phase', '')}",
        f"- Epoch: {report.get('epoch', '')}",
        f"- Best score: {report.get('best', {}).get('score')}",
        f"- Best eval: {report.get('best', {}).get('eval_ref_path')}",
        "",
        "## Evaluations",
    ]
    for item in report.get("evaluations", []):
        lines.append(f"- {item.get('phase', '')}: score={item.get('score')} eval={item.get('eval_ref_path', '')}")
    lines.extend(["", "## Member Optimization Attempts"])
    for item in report.get("member_optimization_attempts", []):
        lines.append(
            f"- {Path(str(item.get('ref_path', ''))).parent.name}: "
            f"status={item.get('status', '')}, "
            f"verification={item.get('verification_status', '')}, "
            f"candidate_published_roles={item.get('candidate_published_roles', [])}"
        )
        for action in item.get("planned_actions", []):
            lines.append(
                f"  - action={action.get('action_id', '')}, "
                f"role={action.get('role', '')}, "
                f"group={action.get('action_group', '')}, "
                f"op={action.get('operation', '')}, "
                f"target={action.get('target_path', '')}"
            )
    consumption = report.get("optimization_consumption", {})
    roles = consumption.get("roles", {}) if isinstance(consumption, dict) else {}
    if roles:
        lines.extend(["", "## Optimization Consumption"])
        for role, role_info in sorted(roles.items()):
            lines.append(
                f"- {role}: harness_exists={role_info.get('harness_exists')}, "
                f"ref={role_info.get('harness_ref_path', '')}"
            )
            for skill in role_info.get("skills", []) or []:
                observed = skill.get("observed", {})
                lines.append(f"  - skill={skill.get('name', '')}, read={observed.get('skill_file_read', False)}")
            for tool in role_info.get("tools", []) or []:
                observed = tool.get("observed", {})
                lines.append(f"  - tool={tool.get('name', '')}, called={observed.get('tool_called', False)}")
    lines.extend(["", "## Candidate Gates"])
    for gate in report.get("member_candidate_gates", []):
        lines.append(
            f"- status={gate.get('status', '')}, "
            f"source_score={gate.get('source_score')}, "
            f"candidate_score={gate.get('candidate_score')}, "
            f"reason={gate.get('reason', '')}"
        )
    lines.extend(["", "## Team Skill Optimization Attempts"])
    for item in report.get("team_skill_optimization_attempts", []):
        lines.append(
            f"- {Path(str(item.get('ref_path', ''))).parent.name}: "
            f"status={item.get('status', '')}, "
            f"candidate={item.get('candidate_path', '')}"
        )
    usage = report.get("llm_usage", {})
    if usage:
        total = usage.get("total", {})
        lines.extend(
            [
                "",
                "## LLM Usage",
                f"- Usage ledger: {usage.get('path', '')}",
                f"- Run ID: {usage.get('run_id', '')}",
                (
                    "- Total: "
                    f"calls={total.get('calls', 0)}, "
                    f"input_tokens={total.get('input_tokens', 0)}, "
                    f"output_tokens={total.get('output_tokens', 0)}, "
                    f"cache_tokens={total.get('cache_tokens', 0)}, "
                    f"total_tokens={total.get('total_tokens', 0)}, "
                    f"cache_hit_rate={float(total.get('cache_hit_rate', 0.0)):.2%}, "
                    f"total_cost={float(total.get('total_cost', 0.0)):.6f}"
                ),
                "",
                "### By Stage",
            ]
        )
        for stage, item in sorted(
            usage.get("by_stage", {}).items(),
            key=lambda entry: int(entry[1].get("total_tokens", 0) or 0),
            reverse=True,
        ):
            lines.append(
                f"- {stage}: calls={item.get('calls', 0)}, "
                f"input={item.get('input_tokens', 0)}, "
                f"output={item.get('output_tokens', 0)}, "
                f"cache={item.get('cache_tokens', 0)}, "
                f"total={item.get('total_tokens', 0)}, "
                f"cache_hit_rate={float(item.get('cache_hit_rate', 0.0)):.2%}"
            )
        lines.extend(["", "### By Model"])
        for model, item in sorted(
            usage.get("by_model", {}).items(),
            key=lambda entry: int(entry[1].get("total_tokens", 0) or 0),
            reverse=True,
        ):
            lines.append(
                f"- {model}: calls={item.get('calls', 0)}, "
                f"input={item.get('input_tokens', 0)}, "
                f"output={item.get('output_tokens', 0)}, "
                f"cache={item.get('cache_tokens', 0)}, "
                f"total={item.get('total_tokens', 0)}, "
                f"cache_hit_rate={float(item.get('cache_hit_rate', 0.0)):.2%}"
            )
    return "\n".join(lines) + "\n"


def _usage_batch_stage(epoch: int, batch_index: int, optimization_stage: str) -> str:
    """Return a readable usage stage name for one epoch/batch phase."""
    stage_names = {
        "team_skill_optimization": "team_skill_stage",
        "member_optimization": "member_stage",
        "candidate_gate": "candidate_gate",
    }
    stage = stage_names.get(optimization_stage, optimization_stage or "stage")
    return f"epoch_{epoch}.batch_{batch_index}.{stage}"


def _batch_progress_metrics(
    *,
    dataset: DatasetArtifact,
    batch_size: int,
    case_count: int,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {"case_count": case_count}
    if dataset.cases is not None:
        metrics["dataset_cases"] = dataset.cases
    if batch_size > 0:
        metrics["batch_size"] = batch_size
        if dataset.cases is not None:
            metrics["batch_total"] = (dataset.cases + batch_size - 1) // batch_size
    return metrics


def _eval_usage_stage_prefix(eval_ref_path: str) -> str:
    """Infer usage stage prefix from an eval_ref path."""
    parts = Path(str(eval_ref_path or "")).parts
    epoch = "epoch_unknown"
    batch = "batch_unknown"
    stage = "stage"
    for part in parts:
        if part.startswith("e") and part[1:].isdigit():
            epoch = f"epoch_{int(part[1:])}"
        elif part.startswith("b") and part[1:].isdigit():
            batch = f"batch_{int(part[1:])}"
        elif part in {"ts", "mh", "cg", "full", "seed"}:
            stage = {
                "ts": "team_skill_stage",
                "mh": "member_stage",
                "cg": "candidate_gate",
                "full": "full_evaluation",
                "seed": "seed_evaluation",
            }[part]
    return f"{epoch}.{batch}.{stage}"


def _eval_score(eval_ref_path: str) -> float:
    """Read average score from an evaluator reference artifact."""
    eval_ref = _load_eval_ref(eval_ref_path)
    summary_path = eval_ref.get("summary_path")
    if not summary_path:
        return 0.0
    score = read_json_mapping(str(summary_path)).get("average_score")
    if isinstance(score, int | float) and not isinstance(score, bool):
        return float(score)
    return 0.0


def _seed_case_from_task(task: str, *, pass_threshold: float) -> CaseMapping:
    """Build the initial real-task evaluation case."""
    return {
        "case_id": "seed_task",
        "input": {
            "user_message": task,
        },
        "reference": {
            "required_behaviors": [
                {
                    "id": "user_goal_fulfillment",
                    "description": (
                        "The final deliverable directly satisfies the user's stated "
                        "goal and would be useful to a real end user."
                    ),
                    "rubric": (
                        "Score from the user's requested outcome, not from completion claims or file existence alone."
                    ),
                },
                {
                    "id": "core_task_semantics",
                    "description": (
                        "The task-specific core semantics are coherent and effective for this artifact type."
                    ),
                    "rubric": (
                        "Infer the core semantics from the original request: gameplay "
                        "for games, narrative and design logic for decks, correctness "
                        "for code, and analogous core meaning for other tasks."
                    ),
                },
                {
                    "id": "user_experience_quality",
                    "description": (
                        "The visible output or interaction experience is clear, "
                        "polished, and aligned with the intended audience."
                    ),
                    "rubric": (
                        "Score from user-facing evidence such as affordances, feedback, "
                        "visual hierarchy, readability, flow, and domain fit."
                    ),
                },
                {
                    "id": "validation_depth",
                    "description": (
                        "Validation evidence covers the task's important success "
                        "conditions, including core behavior and user-visible quality."
                    ),
                    "rubric": ("Score high only when checks go beyond smoke tests and support the actual user goal."),
                },
            ],
            "judge_rubric": {
                "pass_threshold": pass_threshold,
                "purpose": "Decide whether this task already meets the requested quality bar.",
            },
        },
        "metadata": {
            "source": "seed_evaluation",
            "generation": "original_task",
        },
    }


def _seed_feedback_from_eval(
    eval_ref_path: str,
    *,
    max_cases: int,
    include_default_gap: bool = True,
) -> dict[str, Any]:
    """Extract gap-driven dataset generation guidance from seed evaluation output."""
    quality_gaps: list[dict[str, Any]] = []
    recommended_tasks: list[dict[str, Any]] = []
    dataset_budget: dict[str, Any] = {}
    runtime_blockers: list[dict[str, Any]] = []
    eval_ref = _load_eval_ref(eval_ref_path)
    for eval_case in _eval_cases(eval_ref):
        result = _read_eval_case_result(eval_case)
        lifecycle_blocker = _team_lifecycle_seed_blocker(
            result,
        )
        if lifecycle_blocker is not None:
            runtime_blockers.append(lifecycle_blocker)
        judge_blocker = _seed_judge_runtime_blocker(result)
        if judge_blocker is not None:
            runtime_blockers.append(judge_blocker)
            continue
        parsed = _result_parsed_metadata(result)
        gaps = parsed.get("quality_gaps")
        parsed_gap_found = False
        if isinstance(gaps, list):
            parsed_quality_gaps = []
            parsed_verification_gaps = []
            for item in gaps:
                if not isinstance(item, dict):
                    continue
                if _is_seed_verification_gap(item):
                    parsed_verification_gaps.append(item)
                    runtime_blockers.append(_seed_verification_gap_blocker(item))
                else:
                    parsed_quality_gaps.append(item)
            parsed_gap_found = bool(parsed_quality_gaps or parsed_verification_gaps)
            quality_gaps.extend(parsed_quality_gaps)
        tasks = parsed.get("recommended_synthetic_tasks")
        if isinstance(tasks, list):
            recommended_tasks.extend(item for item in tasks if isinstance(item, dict))
        budget = parsed.get("dataset_budget")
        if isinstance(budget, dict) and not dataset_budget:
            dataset_budget = dict(budget)
        if not parsed_gap_found:
            artifact_gap = _artifact_quality_seed_gap(result)
            if artifact_gap is not None:
                quality_gaps.append(artifact_gap)
                recommended_tasks.append(_artifact_quality_seed_task())

    if not quality_gaps and not include_default_gap:
        return {
            "quality_gaps": [],
            "dataset_budget": {},
            "recommended_synthetic_tasks": recommended_tasks,
            "runtime_blockers": runtime_blockers,
        }

    if not quality_gaps and _has_seed_verification_gap_blocker(runtime_blockers):
        return {
            "quality_gaps": [],
            "dataset_budget": {},
            "recommended_synthetic_tasks": [],
            "runtime_blockers": runtime_blockers,
        }

    if not quality_gaps:
        quality_gaps = [
            {
                "id": "seed_delivery_quality_gap",
                "dimension": "end_to_end_delivery_quality",
                "severity": "medium",
                "data_needed_to_fix": (
                    "Generate task-specific cases that exercise the weakest "
                    "end-to-end delivery capabilities observed in the seed run."
                ),
            }
        ]
    quality_gaps = _dedupe_seed_gaps(quality_gaps)
    quality_gaps = _enrich_seed_gap_surfaces(quality_gaps)
    recommended_tasks = _dedupe_seed_tasks(recommended_tasks)
    dataset_budget = _filter_seed_dataset_budget_for_quality_gaps(
        dataset_budget,
        quality_gaps,
    )
    if not dataset_budget:
        case_count = max(1, min(max_cases, max(3, len(quality_gaps) * 2)))
        dataset_budget = {
            "total_cases": case_count,
            "case_groups": [
                {
                    "source_gap": str(gap.get("id", f"gap_{index}")),
                    "case_count": max(1, case_count // len(quality_gaps)),
                    "target_roles": gap.get("affected_roles", []),
                    "target_surfaces": gap.get("likely_surfaces", []),
                }
                for index, gap in enumerate(quality_gaps, start=1)
            ],
        }
    dataset_budget = _align_seed_dataset_budget_surfaces(
        quality_gaps,
        dataset_budget,
    )
    return {
        "quality_gaps": quality_gaps,
        "dataset_budget": dataset_budget,
        "recommended_synthetic_tasks": recommended_tasks,
        "runtime_blockers": runtime_blockers,
    }


def _is_seed_verification_gap(gap: dict[str, Any]) -> bool:
    return str(gap.get("gap_type", "") or "").strip() == "verification_gap"


def _seed_verification_gap_blocker(gap: dict[str, Any]) -> dict[str, Any]:
    gap_id = str(gap.get("id", "") or "unnamed").strip() or "unnamed"
    return {
        "id": f"seed_verification_gap_{gap_id}",
        "dimension": str(gap.get("dimension", "") or "verification_evidence"),
        "severity": str(gap.get("severity", "") or "medium"),
        "failure_type": "verification_gap",
        "resolution_owner": "evaluation_pipeline",
        "affected_roles": gap.get("affected_roles", []),
        "likely_surfaces": [],
        "evidence": gap.get("evidence", ""),
        "fix_guidance": (
            "Collect stronger evaluation evidence or rerun judge with complete "
            "artifact/runtime evidence before generating capability-training data."
        ),
    }


def _has_seed_verification_gap_blocker(blockers: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("id", "") or "").startswith("seed_verification_gap_")
        for item in blockers
    )


def _artifact_quality_seed_gap(result: dict[str, Any]) -> dict[str, Any] | None:
    evaluation = result.get("evaluation") if isinstance(result, dict) else None
    if isinstance(evaluation, dict) and bool(evaluation.get("passed", False)):
        return None
    artifacts = result.get("artifacts") if isinstance(result, dict) else None
    if not isinstance(artifacts, dict):
        return None
    harvested = [str(item) for item in artifacts.get("harvested", []) if str(item or "").strip()]
    if not harvested:
        return None
    missing = [str(item) for item in artifacts.get("missing", []) if str(item or "").strip()]
    low_behaviors = _low_score_seed_behaviors(result)
    return {
        "id": "seed_artifact_quality_gap",
        "dimension": "end_to_end_artifact_quality",
        "severity": "high",
        "failure_type": "seed_artifact_quality_gap",
        "affected_roles": _seed_result_roles(result),
        "likely_surfaces": ["skill", "prompt_section", "tool"],
        "evidence": {
            "reason": _seed_result_reason(result),
            "score": result.get("score"),
            "harvested_artifacts": harvested,
            "missing_artifacts": missing,
            "low_score_behaviors": low_behaviors,
        },
        "quality_axes": [
            {
                "name": "functional_effectiveness",
                "description": (
                    "The delivered artifact must actually perform the task-specific "
                    "core behavior, not only exist as files or prose."
                ),
            },
            {
                "name": "interaction_or_effect_quality",
                "description": (
                    "User-visible actions, transitions, feedback, or generated effects "
                    "must be clear enough to verify from inspectable artifacts."
                ),
            },
            {
                "name": "user_visible_output_quality",
                "description": (
                    "The artifact must meet the task's inspectable output quality "
                    "bar, including structure, readability, consistency, and "
                    "audience fit when relevant."
                ),
            },
            {
                "name": "acceptance_contract",
                "description": (
                    "The final output must satisfy explicit deliverable constraints and "
                    "provide evidence that those constraints were checked."
                ),
            },
        ],
        "data_needed_to_fix": (
            "Generate targeted cases from the actual seed deliverable gaps, "
            "covering task functionality, user-visible effects, output appearance, "
            "task-specific acceptance criteria, member-owned execution methods, "
            "and deterministic validation needs."
        ),
    }


def _artifact_quality_seed_task() -> dict[str, Any]:
    return {
        "task_pattern": "observed_deliverable_quality_gap",
        "difficulty_level": 3,
        "specific_trap_to_include": (
            "The task produces inspectable artifacts, but quality depends on "
            "case-specific acceptance criteria rather than file existence alone."
        ),
        "success_criteria": [
            "final artifacts satisfy the task-specific quality bar",
            "the relevant member capability is exercised through observable output",
            "verification evidence can distinguish a shallow artifact from a good one",
        ],
    }


def _low_score_seed_behaviors(result: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = _result_parsed_metadata(result)
    behaviors = parsed.get("behaviors")
    if not isinstance(behaviors, list):
        return []
    low_behaviors: list[dict[str, Any]] = []
    for behavior in behaviors:
        if not isinstance(behavior, dict):
            continue
        try:
            score = float(behavior.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        failure_reason = str(behavior.get("failure_reason", "") or "").strip()
        if score >= 0.8 and not failure_reason:
            continue
        low_behaviors.append(
            {
                "id": str(behavior.get("id", "") or ""),
                "score": score,
                "failure_reason": failure_reason,
                "missing_capability": str(behavior.get("missing_capability", "") or ""),
                "suggested_surface_hint": str(behavior.get("suggested_surface_hint", "") or ""),
            }
        )
    return low_behaviors


def _seed_result_roles(result: dict[str, Any]) -> list[str]:
    metadata = result.get("metadata") if isinstance(result, dict) else None
    if not isinstance(metadata, dict):
        return []
    roles: list[str] = []
    for key in ("role", "member", "agent_id"):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            roles.append(value)
    return list(dict.fromkeys(roles))


def _seed_result_reason(result: dict[str, Any]) -> str:
    evaluation = result.get("evaluation") if isinstance(result, dict) else None
    if isinstance(evaluation, dict):
        reason = str(evaluation.get("reason", "") or "").strip()
        if reason:
            return reason
    return str(result.get("error", "") or result.get("status", "") or "").strip()


def _dedupe_seed_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, gap in enumerate(gaps, start=1):
        gap_id = str(gap.get("id", "") or f"gap_{index}").strip()
        if gap_id in seen:
            continue
        seen.add(gap_id)
        normalized = dict(gap)
        normalized["id"] = gap_id
        deduped.append(normalized)
    return deduped


def _enrich_seed_gap_surfaces(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for gap in gaps:
        normalized = dict(gap)
        surfaces = _normalized_member_surfaces(normalized.get("likely_surfaces"))
        if _seed_gap_points_to_tool_surface(normalized):
            surfaces = _merge_member_surfaces(surfaces, ["tool"])
        if surfaces:
            normalized["likely_surfaces"] = surfaces
        enriched.append(normalized)
    return enriched


def _align_seed_dataset_budget_surfaces(
    gaps: list[dict[str, Any]],
    dataset_budget: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(dataset_budget, dict):
        return dataset_budget
    gap_surfaces = {
        str(gap.get("id", "") or ""): _normalized_member_surfaces(gap.get("likely_surfaces")) for gap in gaps
    }
    groups = dataset_budget.get("case_groups")
    if not isinstance(groups, list):
        return dataset_budget
    aligned_groups: list[Any] = []
    changed = False
    for group in groups:
        if not isinstance(group, dict):
            aligned_groups.append(group)
            continue
        source_gap = str(group.get("source_gap", "") or "")
        surfaces = gap_surfaces.get(source_gap, [])
        if not surfaces:
            aligned_groups.append(group)
            continue
        current = _normalized_member_surfaces(group.get("target_surfaces"))
        merged = _merge_member_surfaces(current, surfaces)
        if merged != current:
            changed = True
            updated = dict(group)
            updated["target_surfaces"] = merged
            aligned_groups.append(updated)
        else:
            aligned_groups.append(group)
    if not changed:
        return dataset_budget
    aligned = dict(dataset_budget)
    aligned["case_groups"] = aligned_groups
    return aligned


def _filter_seed_dataset_budget_for_quality_gaps(
    dataset_budget: dict[str, Any],
    quality_gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(dataset_budget, dict) or not dataset_budget:
        return {}
    quality_gap_ids: set[str] = set()
    for gap in quality_gaps:
        gap_id = str(gap.get("id", "") or "").strip()
        if gap_id:
            quality_gap_ids.add(gap_id)
    if not quality_gap_ids:
        return {}
    groups = dataset_budget.get("case_groups")
    if not isinstance(groups, list):
        return dict(dataset_budget)
    kept_groups = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        source_gap = str(group.get("source_gap", "") or "").strip()
        if source_gap in quality_gap_ids:
            kept_groups.append(dict(group))
    if not kept_groups:
        return {}
    filtered = dict(dataset_budget)
    filtered["case_groups"] = kept_groups
    total_cases = 0
    for group in kept_groups:
        try:
            total_cases += max(0, int(group.get("case_count", 0) or 0))
        except (TypeError, ValueError):
            total_cases += 0
    filtered["total_cases"] = total_cases or len(kept_groups)
    return filtered


def _seed_gap_points_to_tool_surface(gap: dict[str, Any]) -> bool:
    evidence_text = json.dumps(gap, ensure_ascii=False).lower()
    tool_terms = (
        "deterministic",
        "executable",
        "runtime",
        "static check",
        "automated check",
        "repeatable check",
        "smoke test",
        "headless",
        "console error",
        "referenceerror",
        "typeerror",
        "lint",
        "type check",
        "unit test",
        "integration test",
        "parser",
        "schema",
        "tool",
    )
    return any(term in evidence_text for term in tool_terms)


def _normalized_member_surfaces(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return _merge_member_surfaces([], [str(item) for item in raw])


def _merge_member_surfaces(existing: list[str], additions: list[str]) -> list[str]:
    allowed = {"prompt_section", "skill", "tool"}
    merged: list[str] = []
    for item in [*existing, *additions]:
        surface = str(item or "").strip()
        if surface not in allowed or surface in merged:
            continue
        merged.append(surface)
    return merged


def _dedupe_seed_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, task in enumerate(tasks, start=1):
        task_id = str(task.get("task_pattern", task.get("source_case_id", f"task_{index}")) or f"task_{index}").strip()
        if task_id in seen:
            continue
        seen.add(task_id)
        deduped.append(dict(task))
    return deduped


def _has_seed_judge_runtime_blocker(feedback: dict[str, Any]) -> bool:
    blockers = feedback.get("runtime_blockers")
    if not isinstance(blockers, list):
        return False
    return any(
        isinstance(item, dict)
        and (
            str(item.get("id", "")).startswith("seed_judge_")
            or str(item.get("id", "")).startswith("seed_verification_gap_")
            or str(item.get("resolution_owner", "") or "") in {"judge_pipeline", "evaluation_pipeline"}
        )
        for item in blockers
    )


def _seed_judge_runtime_blocker(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    evaluation = result.get("evaluation") if isinstance(result, dict) else None
    if not isinstance(evaluation, dict):
        return None
    metadata = evaluation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    reason = str(evaluation.get("reason", "") or "")
    judge_error_type = str(metadata.get("judge_error_type", "") or "")
    is_judge_error = bool(metadata.get("judge_error")) or (reason == "failed to parse llm_as_judge output")
    if not is_judge_error:
        return None

    return {
        "id": "seed_judge_parse_failed",
        "dimension": "judge_pipeline_parseability",
        "severity": "high",
        "failure_type": judge_error_type or "judge_parse_failed",
        "resolution_owner": "judge_pipeline",
        "affected_roles": [],
        "likely_surfaces": [],
        "evidence": {
            "reason": reason,
            "raw_output_truncated": bool(metadata.get("raw_output_truncated")),
        },
        "fix_guidance": (
            "Fix LLM judge output contract, segmentation, or parsing before using "
            "this seed result as dataset-generation signal."
        ),
    }


def _team_lifecycle_seed_blocker(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    metadata = result.get("metadata") if isinstance(result, dict) else None
    if not isinstance(metadata, dict):
        return None
    execution = metadata.get("execution")
    if not isinstance(execution, dict):
        return None
    if execution.get("failure_type") != "team_lifecycle_timeout":
        return None

    artifacts = result.get("artifacts")
    harvested = artifacts.get("harvested", []) if isinstance(artifacts, dict) else []
    expected_lifecycle = execution.get("expected_team_lifecycle", [])
    return {
        "id": "seed_team_lifecycle_timeout",
        "dimension": "team_lifecycle_and_completion",
        "severity": "high",
        "failure_type": "team_lifecycle_timeout",
        "resolution_owner": "code_flow",
        "affected_roles": ["team_leader"],
        "likely_surfaces": ["team_skill.workflow"],
        "evidence": {
            "error": str(result.get("error", "") or ""),
            "harvested_artifacts": harvested if isinstance(harvested, list) else [],
            "expected_team_lifecycle": (expected_lifecycle if isinstance(expected_lifecycle, list) else []),
        },
        "fix_guidance": (
            "Fix the orchestration/team-runtime lifecycle so completed teams finish "
            "bounded repair/review, shutdown members, and call clean_team. Do not "
            "route this as a synthetic dataset training target."
        ),
    }


def _result_parsed_metadata(result: dict[str, Any]) -> dict[str, Any]:
    evaluation = result.get("evaluation") if isinstance(result, dict) else None
    if not isinstance(evaluation, dict):
        return {}
    metadata = evaluation.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    parsed = metadata.get("parsed")
    return parsed if isinstance(parsed, dict) else {}


def _eval_cases(eval_ref: dict[str, Any]) -> list[CaseMapping]:
    """Recover case mappings from an evaluator reference artifact."""
    raw_cases = eval_ref.get("cases", [])
    if not isinstance(raw_cases, list):
        return []
    cases: list[CaseMapping] = []
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            continue
        case_id = str(raw_case.get("case_id", "") or "")
        if not case_id:
            continue
        case = dict(raw_case)
        case.setdefault("case_index", index)
        cases.append(case)
    return cases


def _source_cases_for_eval_ref(eval_ref: dict[str, Any]) -> list[CaseMapping]:
    """Recover original dataset cases for replaying an evaluation batch.

    Eval refs store lightweight case trace records. Candidate gates must replay
    the original judgeable case payload so fields such as reference.required_behaviors
    are still available to the evaluator.
    """
    return [_source_case_for_eval_case(case) for case in _eval_cases(eval_ref)]


def _source_case_for_eval_case(eval_case: CaseMapping) -> CaseMapping:
    case_path = str(eval_case.get("case_path", "") or "")
    if not case_path:
        return dict(eval_case)
    path = Path(case_path).expanduser().resolve()
    if not path.exists():
        return dict(eval_case)

    data = read_json_mapping(str(path))
    if isinstance(data.get("cases"), list):
        raw_cases = data["cases"]
    else:
        raw_cases = [data]

    case_id = str(eval_case.get("case_id", "") or "")
    case_index = _int_or_none(eval_case.get("case_index"))
    selected: CaseMapping | None = None
    if case_index is not None and 1 <= case_index <= len(raw_cases):
        candidate = raw_cases[case_index - 1]
        if isinstance(candidate, dict) and (not case_id or str(candidate.get("case_id", "") or "") == case_id):
            selected = dict(candidate)
    if selected is None and case_id:
        for candidate in raw_cases:
            if isinstance(candidate, dict) and str(candidate.get("case_id", "") or "") == case_id:
                selected = dict(candidate)
                break
    if selected is None:
        return dict(eval_case)

    selected["case_id"] = str(selected.get("case_id", "") or case_id)
    selected["case_path"] = str(path)
    if case_index is not None:
        selected["case_index"] = case_index
    else:
        selected.setdefault("case_index", 1)
    return selected


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _flatten_batches(batches: Iterable[list[CaseMapping]]) -> list[CaseMapping]:
    """Flatten a fresh DataLoader traversal for full-dataset epoch evaluation."""
    return [case for batch in batches for case in batch]


def _load_eval_ref(eval_ref_path: str) -> dict[str, Any]:
    """Load an evaluator reference artifact."""
    return read_yaml_mapping(eval_ref_path)


def _eval_ref_complete(eval_ref_path: str) -> bool:
    """Return whether an evaluation ref has the minimum reusable artifacts."""
    if not str(eval_ref_path or "").strip():
        return False
    path = Path(eval_ref_path).expanduser().resolve()
    if not path.is_file():
        return False
    try:
        eval_ref = _load_eval_ref(str(path))
    except Exception:
        return False
    summary_path = str(eval_ref.get("summary_path", "") or "")
    if summary_path:
        return Path(summary_path).expanduser().is_file()
    return (path.parent / "summary.json").is_file()


def _analysis_ref_complete(analysis_ref_path: str) -> bool:
    """Return whether an analysis ref is complete enough to reuse."""
    if not str(analysis_ref_path or "").strip():
        return False
    path = Path(analysis_ref_path).expanduser().resolve()
    if not path.is_file():
        return False
    try:
        analysis = read_yaml_mapping(str(path))
    except Exception:
        return False
    return isinstance(analysis.get("issues"), list)


def _resume_eval_position(eval_ref_path: str | None) -> tuple[int, int] | None:
    """Extract ``(epoch, batch)`` from a stage-local eval ref path."""
    if not str(eval_ref_path or "").strip():
        return None
    epoch: int | None = None
    batch: int | None = None
    for part in Path(str(eval_ref_path)).parts:
        if len(part) == 4 and part.startswith("e") and part[1:].isdigit():
            epoch = int(part[1:])
        elif len(part) == 4 and part.startswith("b") and part[1:].isdigit():
            batch = int(part[1:])
    if epoch is None or batch is None:
        return None
    return epoch, batch


def _batch_completion_key(epoch: int, batch_index: int) -> str:
    """Return the stable key for one terminal batch checkpoint."""
    return f"epoch_{epoch:03d}:batch_{batch_index:03d}"


def _resume_should_skip_batch(
    position: tuple[int, int] | None,
    *,
    epoch: int,
    batch_index: int,
) -> bool:
    """Skip batches before the last in-progress batch during resume."""
    if position is None:
        return False
    resume_epoch, resume_batch = position
    return epoch < resume_epoch or (epoch == resume_epoch and batch_index < resume_batch)


def _analysis_dir_for_eval_ref(eval_ref_path: str) -> Path:
    """Return the stage-local analysis directory paired with an evaluation ref."""
    eval_path = Path(eval_ref_path).expanduser().resolve()
    stage_dir = eval_path.parent.parent if eval_path.parent.name == "evaluation" else eval_path.parent
    analysis_dir = stage_dir / "a"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    return analysis_dir


def _eval_case_results_dir(eval_ref: dict[str, Any], eval_dir: Path) -> Path:
    case_results_dir = str(eval_ref.get("case_results_dir") or "").strip()
    if case_results_dir:
        return Path(case_results_dir).expanduser().resolve()
    cases_dir = eval_dir / "cases"
    if cases_dir.exists():
        return cases_dir
    return eval_dir / "case_results"


def _eval_case_traces_dir(eval_ref: dict[str, Any], case_results_dir: Path) -> Path:
    case_traces_dir = str(eval_ref.get("case_traces_dir") or "").strip()
    if case_traces_dir:
        return Path(case_traces_dir).expanduser().resolve()
    return case_results_dir


def _write_eval_analysis_ref(eval_ref_path: str, analysis_ref_path: str) -> None:
    """Persist the paired analysis ref beside the evaluation ref metadata."""
    eval_ref = _load_eval_ref(eval_ref_path)
    eval_ref["analysis_ref_path"] = analysis_ref_path
    write_yaml_mapping(eval_ref_path, eval_ref)


def _eval_source_stage(eval_ref_path: str) -> str:
    """Infer the optimization stage that produced an evaluation artifact."""
    parts = set(Path(eval_ref_path).parts)
    if "team_skill_optimization" in parts or "ts" in parts:
        return "team_skill_stage"
    if "member_optimization" in parts or "mh" in parts:
        return "member_stage"
    return "unknown"


def _analysis_has_team_skill_issue(
    analysis_ref_path: str,
    *,
    source_stage: str = "",
) -> bool:
    """Return whether analysis explicitly targets Team Skill optimization."""
    analysis_ref = read_yaml_mapping(analysis_ref_path)
    issues = analysis_ref.get("issues", [])
    if not isinstance(issues, list):
        return False
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if _issue_optimization_target(issue) == "team_skill":
            return True
        if _issue_targets_generated_team_contract(issue, source_stage=source_stage):
            return True
    return False


def _analysis_has_member_issue(analysis_ref_path: str) -> bool:
    """Return whether analysis explicitly targets Member Harness optimization."""
    analysis_ref = read_yaml_mapping(analysis_ref_path)
    issues = analysis_ref.get("issues", [])
    if not isinstance(issues, list):
        return False
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if _issue_optimization_target(issue) == "member_harness":
            return True
    return False


def _analysis_has_actionable_member_issue(
    analysis_ref_path: str,
    *,
    source_stage: str = "",
) -> bool:
    """Return whether analysis has member issues not owned by Team Skill contract."""
    analysis_ref = read_yaml_mapping(analysis_ref_path)
    issues = analysis_ref.get("issues", [])
    if not isinstance(issues, list):
        return False
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if _issue_optimization_target(issue) != "member_harness":
            continue
        if _issue_targets_generated_team_contract(issue, source_stage=source_stage):
            continue
        return True
    return False


def _issue_member_candidates(issue: dict[str, Any]) -> list[str]:
    """Return deterministic non-leader member candidates named by one issue."""
    values: list[str] = []
    for item in issue.get("target_members", []) or []:
        values.append(str(item or "").strip())
    metadata = issue.get("metadata") or {}
    if isinstance(metadata, dict):
        for item in metadata.get("affected_components", []) or []:
            values.append(str(item or "").strip())
    for evidence in issue.get("evidence", []) or []:
        if isinstance(evidence, dict):
            values.append(str(evidence.get("affected_component", "") or "").strip())
    excluded = {"", "team", "team_leader", "leader", "team_skill"}
    return sorted({value for value in values if value.lower() not in excluded})


def _preferred_issue_member_candidate(
    issue: dict[str, Any],
    candidates: list[str],
    *,
    target_ref: str = "",
) -> str:
    """Choose one deterministic owner for a restricted single-role repair."""
    if not candidates:
        return ""

    def normalized(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    normalized_ref = normalized(target_ref)
    for candidate in candidates:
        if normalized(candidate) and normalized(candidate) in normalized_ref:
            return candidate
    for evidence in issue.get("evidence", []) or []:
        if not isinstance(evidence, dict):
            continue
        affected = normalized(evidence.get("affected_component", ""))
        for candidate in candidates:
            if affected and normalized(candidate) == affected:
                return candidate
    return candidates[0]


def _issue_targets_generated_team_contract(
    issue: dict[str, Any],
    *,
    source_stage: str = "",
) -> bool:
    """Detect member-labeled defects caused by generated Team Skill contracts."""
    if source_stage != "team_skill_stage":
        return False
    if _issue_optimization_target(issue) != "member_harness":
        return False
    target_ref = str(issue.get("target_ref", "") or "").lower()
    if not (target_ref.endswith(".skill") or ".skill." in target_ref or target_ref.endswith(".prompt")):
        return False
    text_parts = []
    for key in (
        "summary",
        "recommendation",
        "root_cause",
        "critical_mistake",
        "general_mechanism",
        "category",
    ):
        text_parts.append(str(issue.get(key, "") or ""))
    text = " ".join(text_parts).lower()
    contract_markers = (
        "team skill",
        "role schema",
        "output schema",
        "artifact contract",
        "deliverable contract",
        "section",
        "workflow contract",
        "role definition",
        "role prompt",
    )
    return any(marker in text for marker in contract_markers)


def _read_harness_refs_for_context(harness_refs_path: str) -> dict[str, str]:
    """Read canonical harness refs for the run context when available."""
    if not str(harness_refs_path or "").strip():
        return {}
    path = Path(harness_refs_path).expanduser()
    if path.is_dir():
        return {child.name: str(child.resolve()) for child in sorted(path.iterdir()) if child.is_dir()}
    data = read_yaml_mapping(harness_refs_path)
    refs = data.get("harness_refs")
    if isinstance(refs, dict):
        return {str(role): str(path) for role, path in refs.items() if str(path).strip()}
    return {
        str(role): str(path)
        for role, path in data.items()
        if isinstance(path, str) and role not in {"version", "source", "description"}
    }


def _member_optimization_info(member_optimization_ref_path: str) -> dict[str, Any]:
    """Read member optimization artifact fields."""
    return read_yaml_mapping(member_optimization_ref_path)


_CAPABILITY_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "before",
    "can",
    "for",
    "from",
    "in",
    "into",
    "member",
    "of",
    "or",
    "role",
    "that",
    "the",
    "to",
    "tool",
    "when",
    "with",
}


def _member_candidate_capabilities(member_info: dict[str, Any]) -> list[dict[str, Any]]:
    """Build stable, auditable capability records from one member action plan."""
    plan = read_yaml_mapping(str(member_info.get("plan_path", "") or ""))
    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        return []
    capabilities: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        target_path = str(action.get("target_path", "") or "")
        text = " ".join(str(action.get(key, "") or "") for key in ("description", "expected_effect", "rationale"))
        tokens = sorted(
            {
                token
                for token in re.findall(r"[a-z][a-z0-9_]+", text.lower())
                if token not in _CAPABILITY_STOPWORDS and len(token) > 2
            }
        )
        capabilities.append(
            {
                "action_id": str(action.get("action_id", "") or ""),
                "role": str(action.get("role", "") or ""),
                "action_group": str(action.get("action_group", "") or ""),
                "operation": str(action.get("operation", "") or ""),
                "target_path": target_path,
                "runtime_name": Path(target_path).stem if target_path else "",
                "capability_tokens": tokens,
                "expected_effect": str(action.get("expected_effect", "") or ""),
            }
        )
    return capabilities


def _candidate_expected_tool_names(capabilities: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for capability in capabilities:
        if capability.get("action_group") != "tool":
            continue
        if capability.get("operation") not in {"add", "modify"}:
            continue
        runtime_name = str(capability.get("runtime_name", "")).strip()
        if runtime_name:
            names.add(runtime_name)
    return sorted(names)


def _runtime_tool_names_match(planned_name: str, invoked_name: str) -> bool:
    """Match a plan file stem with the concrete runtime ToolCard call name."""

    def canonical(value: str) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_")
        return normalized.removesuffix("_tool")

    return bool(canonical(planned_name)) and canonical(planned_name) == canonical(invoked_name)


def _capabilities_equivalent(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    """Compare capability intent independently from generated file names."""
    if current.get("role") != previous.get("role"):
        return False
    if current.get("action_group") != previous.get("action_group"):
        return False
    current_tokens = set(current.get("capability_tokens", []))
    previous_tokens = set(previous.get("capability_tokens", []))
    if not current_tokens or not previous_tokens:
        return False
    similarity = len(current_tokens & previous_tokens) / len(current_tokens | previous_tokens)
    return similarity >= 0.35


def _eval_invoked_tool_names(eval_ref_path: str) -> set[str]:
    """Collect structured tool-call names from candidate evaluation traces."""
    eval_ref = _load_eval_ref(eval_ref_path)
    names: set[str] = set()
    for case in _eval_cases(eval_ref):
        trace_path = str(case.get("trace_path", "") or "")
        trace = read_json_mapping(trace_path)
        _collect_structured_tool_names(trace, names)
        trajectory_dir = Path(str(trace.get("trajectory_dir", "") or ""))
        if trajectory_dir.is_dir():
            for member_trace_path in trajectory_dir.glob("*.jsonl"):
                _collect_jsonl_tool_names(member_trace_path, names)
    return names


def _eval_failed_machine_evidence(eval_ref_path: str) -> list[str]:
    """Return explicit machine-evidence failures from candidate case results.

    Score or behavior improvements cannot override a failed executable contract.
    Missing machine evidence is handled by the domain Judge Skill contract; this
    gate only acts on evidence that was collected and explicitly failed.
    """
    eval_ref = _load_eval_ref(eval_ref_path)
    failures: list[str] = []
    for case in _eval_cases(eval_ref):
        case_id = str(case.get("case_id", "") or "unknown_case")
        result = read_json_mapping(str(case.get("result_path", "") or ""))
        evaluation = result.get("evaluation") or {}
        if not isinstance(evaluation, dict):
            continue
        metadata = evaluation.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        evidence = metadata.get("artifact_runtime_evidence") or {}
        if not isinstance(evidence, dict):
            continue
        observations = evidence.get("observations") or []
        if not isinstance(observations, list):
            continue
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            status = str(observation.get("status", "") or "").strip().lower()
            if status not in {"failed", "error"}:
                continue
            evidence_type = str(observation.get("type", "") or "machine_evidence")
            failures.append(f"{case_id}:{evidence_type}:{status}")
    return sorted(set(failures))


def _collect_jsonl_tool_names(path: Path, names: set[str]) -> None:
    """Collect tool names from native Team member trajectories.

    The top-level case trace records the leader result and points at per-member
    JSONL trajectories. Candidate tools are normally invoked by members, so the
    candidate gate must inspect those structured traces as well.
    """
    collect_jsonl_successful_usage(path, tool_names=names)


def _collect_structured_tool_names(value: Any, names: set[str]) -> None:
    collect_successful_tool_names(value, names)


def _member_optimization_published_roles(member_info: dict[str, Any]) -> list[str]:
    """Return roles whose candidate package was published by MemberOptimizer."""
    published_roles = member_info.get("published_roles", [])
    if isinstance(published_roles, list):
        return [str(role) for role in published_roles if str(role).strip()]
    role = str(member_info.get("role", "") or "").strip()
    return [role] if role else []


def _issue_optimization_target(issue: dict[str, Any]) -> str:
    """Return the normalized orchestrator gate target for an analysis issue."""
    target = str(issue.get("optimization_target", "") or "").strip().lower().replace("-", "_")
    if target in {"team_skill", "member_harness"}:
        return target
    return target


def _member_gate_source_cases_and_score(eval_ref_path: str) -> tuple[list[CaseMapping], float, int]:
    """Return judge-comparable source cases for member candidate replay."""
    eval_ref = _load_eval_ref(eval_ref_path)
    source_cases: list[CaseMapping] = []
    scores: list[float] = []
    filtered_cases = 0
    for eval_case in _eval_cases(eval_ref):
        if _eval_case_is_inconclusive(eval_case):
            filtered_cases += 1
            continue
        source_cases.append(_source_case_for_eval_case(eval_case))
        score = _eval_case_score(eval_case)
        if score is not None:
            scores.append(score)
    source_score = sum(scores) / len(scores) if scores else _eval_score(eval_ref_path)
    return source_cases, source_score, filtered_cases


def _eval_case_is_inconclusive(eval_case: CaseMapping) -> bool:
    """Return whether one eval case is an execution/judge error instead of a scoreable result."""
    if str(eval_case.get("status", "") or "").lower() == "error":
        return True
    result = _read_eval_case_result(eval_case)
    if not result:
        return False
    if str(result.get("status", "") or "").lower() == "error":
        return True
    evaluation = result.get("evaluation")
    return isinstance(evaluation, dict) and str(evaluation.get("method", "") or "").lower() == "error"


def _eval_case_score(eval_case: CaseMapping) -> float | None:
    """Read the case score from result payload when present, otherwise eval ref metadata."""
    result = _read_eval_case_result(eval_case)
    if result:
        score = _numeric(result.get("score"))
        if score is not None:
            return score
    return _numeric(eval_case.get("score"))


def _fmt_score(value: Any) -> str:
    """Format scores for progress messages without affecting persisted metrics."""
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _read_eval_case_result(eval_case: CaseMapping) -> dict[str, Any]:
    """Read one case result artifact when the eval ref points to one."""
    result_path = str(eval_case.get("result_path", "") or "").strip()
    if not result_path:
        return {}
    path = Path(result_path).expanduser()
    if not path.exists():
        return {}
    return read_json_mapping(str(path))


def _eval_ref_all_cases_passed(eval_ref_path: str, *, success_score: float) -> bool:
    """Return whether every source case passed evaluation, not just execution."""
    eval_ref = _load_eval_ref(eval_ref_path)
    cases = eval_ref.get("cases", [])
    if not isinstance(cases, list) or not cases:
        return False
    for case in cases:
        if not isinstance(case, dict):
            return False
        result = read_json_mapping(str(case.get("result_path", "") or ""))
        evaluation = result.get("evaluation") if isinstance(result, dict) else None
        if isinstance(evaluation, dict) and isinstance(evaluation.get("passed"), bool):
            if not evaluation["passed"]:
                return False
            continue
        status = str(result.get("status", case.get("status", "")) or "").lower()
        if status == "error":
            return False
        score = _numeric(result.get("score", case.get("score")))
        if score is None or score < success_score:
            return False
    return True


def _eval_target_behavior_delta(
    source_eval_ref_path: str,
    candidate_eval_ref_path: str,
    *,
    target_roles: set[str] | None = None,
) -> float:
    """Return the fraction of the targeted source deficit closed by a candidate.

    Optimizer actions are normally created from accepted quality gaps. Compare
    the severity-weighted gap burden for the roles changed by the candidate so
    an unrelated behavior-score fluctuation cannot masquerade as target
    improvement. Fall back to scored behavior deficits when the source exposes
    no role-targeted quality gaps (for example, older evaluation artifacts).
    """
    roles = {str(role).strip() for role in target_roles or set() if str(role).strip()}
    if roles:
        source_gap_burden = _eval_quality_gap_burden(source_eval_ref_path, roles)
        if source_gap_burden > 0:
            candidate_gap_burden = _eval_quality_gap_burden(candidate_eval_ref_path, roles)
            return (source_gap_burden - candidate_gap_burden) / source_gap_burden

    source_behavior_scores = _eval_behavior_scores(source_eval_ref_path)
    candidate_behavior_scores = _eval_behavior_scores(candidate_eval_ref_path)
    target_ids = {behavior_id for behavior_id, score in source_behavior_scores.items() if score < 1.0}
    shared_ids = sorted(target_ids & set(candidate_behavior_scores))
    if not shared_ids:
        return 0.0
    source_deficit = sum(1.0 - source_behavior_scores[behavior_id] for behavior_id in shared_ids)
    if source_deficit <= 0:
        return 0.0
    net_improvement = sum(
        candidate_behavior_scores[behavior_id] - source_behavior_scores[behavior_id] for behavior_id in shared_ids
    )
    return net_improvement / source_deficit


def _eval_quality_gap_burden(eval_ref_path: str, target_roles: set[str]) -> float:
    """Return severity-weighted accepted quality gaps affecting target roles."""
    severity_weights = {
        "low": 1.0,
        "medium": 2.0,
        "high": 3.0,
        "critical": 4.0,
    }
    burden = 0.0
    eval_ref = _load_eval_ref(eval_ref_path)
    cases = eval_ref.get("cases", [])
    if not isinstance(cases, list):
        return burden
    for case in cases:
        if not isinstance(case, dict):
            continue
        result = read_json_mapping(str(case.get("result_path", "") or ""))
        evaluation = result.get("evaluation") if isinstance(result, dict) else None
        metadata = evaluation.get("metadata") if isinstance(evaluation, dict) else None
        parsed = metadata.get("parsed") if isinstance(metadata, dict) else None
        gaps = parsed.get("quality_gaps", []) if isinstance(parsed, dict) else []
        if not isinstance(gaps, list):
            continue
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            affected_roles = {str(role).strip() for role in gap.get("affected_roles", []) if str(role).strip()}
            if not (affected_roles & target_roles):
                continue
            burden += severity_weights.get(
                str(gap.get("severity", "low") or "low").strip().lower(),
                1.0,
            )
    return burden


def _eval_behavior_scores(eval_ref_path: str) -> dict[str, float]:
    """Read per-behavior judge scores from result artifacts."""
    eval_ref = _load_eval_ref(eval_ref_path)
    cases = eval_ref.get("cases", [])
    if not isinstance(cases, list):
        return {}
    values_by_behavior: dict[str, list[float]] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        result = read_json_mapping(str(case.get("result_path", "") or ""))
        for behavior in _iter_result_behaviors(result):
            behavior_id = str(behavior.get("id", "") or "")
            score = _numeric(behavior.get("score"))
            if not behavior_id or score is None:
                continue
            values_by_behavior.setdefault(behavior_id, []).append(score)
    return {behavior_id: sum(scores) / len(scores) for behavior_id, scores in values_by_behavior.items() if scores}


def _iter_result_behaviors(result: dict[str, Any]) -> Iterator[dict[str, Any]]:
    evaluation = result.get("evaluation") if isinstance(result, dict) else None
    if not isinstance(evaluation, dict):
        return iter(())
    metadata = evaluation.get("metadata")
    if not isinstance(metadata, dict):
        return iter(())
    parsed = metadata.get("parsed")
    if isinstance(parsed, dict) and isinstance(parsed.get("behaviors"), list):
        return (item for item in parsed["behaviors"] if isinstance(item, dict))
    dimensions = metadata.get("dimensions")
    if isinstance(dimensions, dict):
        diagnostics = dimensions.get("behavior_diagnostics")
        per_scores = dimensions.get("per_behavior_scores")
        if isinstance(per_scores, dict):
            return (
                {
                    "id": behavior_id,
                    "score": score,
                    **_behavior_diagnostic(diagnostics, behavior_id),
                }
                for behavior_id, score in per_scores.items()
            )
    return iter(())


def _behavior_diagnostic(diagnostics: Any, behavior_id: str) -> dict[str, Any]:
    """Return one behavior diagnostic mapping when available."""
    if not isinstance(diagnostics, dict):
        return {}
    diagnostic = diagnostics.get(behavior_id)
    return diagnostic if isinstance(diagnostic, dict) else {}


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _eval_ref_has_inconclusive_cases(eval_ref_path: str) -> bool:
    """Return whether an eval ref contains execution/judge errors, not behavior failures."""
    eval_ref = _load_eval_ref(eval_ref_path)
    cases = eval_ref.get("cases", [])
    if not isinstance(cases, list):
        return False
    for case in cases:
        if not isinstance(case, dict):
            continue
        if str(case.get("status", "") or "").lower() == "error":
            return True
        result = read_json_mapping(str(case.get("result_path", "") or ""))
        if str(result.get("status", "") or "").lower() == "error":
            return True
        evaluation = result.get("evaluation")
        if isinstance(evaluation, dict) and str(evaluation.get("method", "") or "").lower() == "error":
            return True
    return False
