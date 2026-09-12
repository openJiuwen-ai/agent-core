# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT training orchestrator for offline skill optimization.

The run is organised as three nested scopes:

``TrainPlan``
    Immutable facts derived from the config before any rollout happens --
    adapter, dataloader, schedule sizes and stage toggles.
``TrainRuntime``
    Everything that mutates as the run proceeds -- the current/best skill pair,
    the selection-score cache, the edit-budget scheduler and the step history.
``StepId``
    The coordinates of one optimization step.

Each step runs rollout → reflect → aggregate → select → apply → gate.  The two
epoch-boundary stages live in :mod:`openjiuwen.agent_evolving.skill_train.epoch_stages`.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, cast

from openjiuwen.agent_evolving.skill_train.aggregate import MergeSettings, merge_patches
from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig
from openjiuwen.agent_evolving.skill_train.edit_budget_scheduler import build_scheduler
from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter
from openjiuwen.agent_evolving.skill_train.epoch_stages import (
    MetaSkillRequest,
    MetaSkillStage,
    ScoreCache,
    SkillProgress,
    SlowUpdateOutcome,
    SlowUpdateRequest,
    SlowUpdateStage,
    display_epoch_of,
    persist_runtime,
    save_skill_version,
)
from openjiuwen.agent_evolving.skill_train.gate import GateMetric, evaluate_gate, select_gate_score
from openjiuwen.agent_evolving.skill_train.llm_client import (
    ChatLLMClient,
    make_llm_invoke_policy,
    set_optimizer_client,
    set_target_client,
)
from openjiuwen.agent_evolving.skill_train.longitudinal import normalise_longitudinal_pair_policy
from openjiuwen.agent_evolving.skill_train.meta_skill import load_meta_skill_content
from openjiuwen.agent_evolving.skill_train.model_compat import set_reasoning_effort
from openjiuwen.agent_evolving.skill_train.registry import get_env_adapter
from openjiuwen.agent_evolving.skill_train.scoring import compute_score, skill_hash
from openjiuwen.agent_evolving.skill_train.select import rank_and_select
from openjiuwen.agent_evolving.skill_train.skill_patch import apply_patch_with_report
from openjiuwen.agent_evolving.skill_train.slow_update import (
    extract_slow_update_field,
    has_slow_update_field,
)
from openjiuwen.agent_evolving.skill_train.state import append_history, save_json
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

_FORCED_SLOW_UPDATE_WARNING = (
    "final current_skill carries force-injected slow_update content "
    "that was not applied to best_skill (skill_train force_accept semantics)"
)


@dataclass
class SkillTrainResult:
    """Outcome of a ReflACT training run."""

    best_skill: str
    best_score: float
    current_skill: str
    current_score: float
    history: List[Dict[str, Any]] = field(default_factory=list)
    output_dir: str = ""


@dataclass(frozen=True)
class TrainPlan:
    """Immutable schedule and toggles resolved from the training config."""

    cfg: Dict[str, Any]
    out_root: str
    env_name: str
    adapter: EnvAdapter
    dataloader: Any
    initial_skill: str
    batch_size: int
    num_epochs: int
    steps_per_epoch: int
    accumulation: int
    seed: int
    update_mode: str
    gate_metric: str
    gate_mixed_weight: float
    use_gate: bool
    use_slow: bool
    use_meta: bool
    slow_gate_with_selection: bool
    slow_n: int
    pair_policy: str

    @property
    def total_steps(self) -> int:
        return self.num_epochs * self.steps_per_epoch

    def merge_settings(self, meta_skill: str) -> MergeSettings:
        return MergeSettings(
            batch_size=int(self.cfg.get("merge_batch_size", 8)),
            workers=int(self.cfg.get("analyst_workers", 16)),
            update_mode=self.update_mode,
            meta_skill_context=meta_skill,
        )

    def gate_score(self, hard: float, soft: float, skill: str) -> float:
        return select_gate_score(
            hard,
            soft,
            cast(GateMetric, self.gate_metric),
            self.gate_mixed_weight,
            skill_content=skill,
        )


@dataclass
class TrainRuntime:
    """State that changes as the run proceeds."""

    progress: SkillProgress
    sel_env: Any
    scheduler: Any
    sel_cache: ScoreCache = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)
    meta_skill: str = ""
    global_step: int = 0


class StepId(NamedTuple):
    """Coordinates of a single optimization step."""

    epoch: int
    display_epoch: int
    step_in_epoch: int
    global_step: int
    seed: int


def _epoch_step_seeds(plan: TrainPlan, epoch: int) -> List[int]:
    """Per-step seeds for one epoch, delegated to the dataloader when present."""
    loader = plan.dataloader
    if loader is None:
        return [plan.seed + epoch]
    base = loader.make_base_seeds(plan.steps_per_epoch, plan.accumulation, plan.seed)
    return list(loader.shuffle_epoch_seeds(base, epoch, plan.seed))


def _split_by_source(patches: List[dict | None], source: str) -> List[dict]:
    return [p for p in patches if p and p.get("source_type") == source]


def _applied_count(report: List[dict]) -> int:
    return sum(1 for entry in report if str(entry.get("status", "")).startswith("applied"))


class SkillReflACTTrainer:
    """Orchestrates the 6-stage ReflACT pipeline for benchmark env adapters."""

    def __init__(
        self,
        *,
        optimizer_llm: Model,
        target_llm: Model,
        optimizer_model: str,
        target_model: str,
        llm_attempt_timeout_secs: float = 120.0,
        llm_total_budget_secs: float = 600.0,
        llm_max_attempts: int = 3,
    ) -> None:
        policy = make_llm_invoke_policy(
            attempt_timeout_secs=llm_attempt_timeout_secs,
            total_budget_secs=llm_total_budget_secs,
            max_attempts=llm_max_attempts,
        )
        self._optimizer_client = ChatLLMClient(llm=optimizer_llm, model=optimizer_model, policy=policy)
        self._target_client = ChatLLMClient(llm=target_llm, model=target_model, policy=policy)

    # ── Setup ────────────────────────────────────────────────────────────────

    def _prepare(self, config: SkillTrainConfig, adapter: EnvAdapter | None) -> TrainPlan:
        """Wire the clients, resolve the adapter and size the schedule."""
        cfg = config.to_trainer_cfg()
        out_root = os.path.abspath(config.output_dir)
        os.makedirs(out_root, exist_ok=True)

        set_optimizer_client(self._optimizer_client)
        set_target_client(self._target_client)
        set_reasoning_effort(cfg.get("reasoning_effort"))

        env_adapter = adapter or get_env_adapter(config.env_name, **config.env_kwargs)
        env_adapter.setup(cfg)

        skill_init_path = cfg.get("skill_init") or config.skill_init
        if not skill_init_path or not os.path.isfile(skill_init_path):
            raise FileNotFoundError(f"skill_init not found: {skill_init_path}")

        batch_size = int(cfg.get("batch_size", 40))
        accumulation = max(1, int(cfg.get("accumulation", 1)))
        dataloader = env_adapter.get_dataloader()
        train_size = int(cfg.get("train_size") or 0)
        if train_size <= 0 and dataloader is not None:
            train_size = int(dataloader.get_train_size() or 0)
        if train_size <= 0:
            raise ValueError("train_size must be > 0 (set explicitly or via dataloader)")

        return TrainPlan(
            cfg=cfg,
            out_root=out_root,
            env_name=config.env_name,
            adapter=env_adapter,
            dataloader=dataloader,
            initial_skill=Path(skill_init_path).read_text(encoding="utf-8"),
            batch_size=batch_size,
            num_epochs=int(cfg.get("num_epochs", 4)),
            steps_per_epoch=math.ceil(train_size / (batch_size * accumulation)),
            accumulation=accumulation,
            seed=int(cfg.get("seed", 42)),
            update_mode=str(cfg.get("skill_update_mode", "patch")),
            gate_metric=str(cfg.get("gate_metric", "hard")),
            gate_mixed_weight=float(cfg.get("gate_mixed_weight", 0.5)),
            use_gate=cfg.get("use_gate", True) is not False,
            use_slow=cfg.get("use_slow_update", True) is not False,
            use_meta=cfg.get("use_meta_skill", True) is not False,
            slow_gate_with_selection=cfg.get("slow_update_gate_with_selection", False) is True,
            slow_n=int(cfg.get("slow_update_samples", 20)),
            pair_policy=normalise_longitudinal_pair_policy(cfg.get("longitudinal_pair_policy", "mixed")),
        )

    def _start_runtime(self, plan: TrainPlan) -> TrainRuntime:
        """Score the initial skill so later candidates have something to beat."""
        sel_env = plan.adapter.build_eval_env(env_num=0, split="valid_seen", seed=plan.seed)
        baseline_dir = os.path.join(plan.out_root, "selection_eval_baseline")
        baseline = compute_score(plan.adapter.rollout(sel_env, plan.initial_skill, baseline_dir))
        baseline_hard, baseline_soft = baseline
        score = plan.gate_score(baseline_hard, baseline_soft, plan.initial_skill)

        return TrainRuntime(
            progress=SkillProgress(
                current_skill=plan.initial_skill,
                current_score=score,
                best_skill=plan.initial_skill,
                best_score=score,
                best_step=0,
            ),
            sel_env=sel_env,
            scheduler=build_scheduler(
                mode=str(plan.cfg.get("lr_scheduler", "cosine")),
                max_lr=int(plan.cfg.get("edit_budget", 4)),
                min_lr=int(plan.cfg.get("min_edit_budget", 2)),
                total_steps=max(plan.total_steps, 1),
            ),
            sel_cache={skill_hash(plan.initial_skill): baseline},
        )

    # ── Main loop ────────────────────────────────────────────────────────────

    def train(
        self,
        *,
        config: SkillTrainConfig,
        adapter: EnvAdapter | None = None,
    ) -> SkillTrainResult:
        """Run the full ReflACT schedule and return the best skill found."""
        plan = self._prepare(config, adapter)
        runtime = self._start_runtime(plan)

        logger.info(
            "[SkillReflACTTrainer] epochs=%s steps/epoch=%s baseline gate[%s]=%.4f slow=%s meta=%s effort=%s",
            plan.num_epochs,
            plan.steps_per_epoch,
            plan.gate_metric,
            runtime.progress.current_score,
            plan.use_slow,
            plan.use_meta,
            plan.cfg.get("reasoning_effort"),
        )

        for epoch in range(plan.num_epochs):
            self._run_epoch(plan, runtime, epoch)

        return self._finalize(plan, runtime)

    def _run_epoch(self, plan: TrainPlan, runtime: TrainRuntime, epoch: int) -> None:
        """Run every step of one epoch, then its two boundary stages."""
        display_epoch = display_epoch_of(epoch)
        if plan.use_meta:
            # The memory written at the end of the previous 1-based epoch is
            # indexed by this epoch's 0-based number.
            runtime.meta_skill = load_meta_skill_content(plan.out_root, epoch)
            if runtime.meta_skill:
                logger.info(
                    "[epoch %s] loaded meta skill (%s chars)",
                    display_epoch,
                    len(runtime.meta_skill),
                )

        seeds = _epoch_step_seeds(plan, epoch)
        for step_in_epoch in range(plan.steps_per_epoch):
            runtime.global_step += 1
            fallback_seed = plan.seed + runtime.global_step
            step = StepId(
                epoch=epoch,
                display_epoch=display_epoch,
                step_in_epoch=step_in_epoch,
                global_step=runtime.global_step,
                seed=seeds[step_in_epoch] if step_in_epoch < len(seeds) else fallback_seed,
            )
            record = self._run_step(plan, runtime, step)
            runtime.history.append(record)
            append_history(plan.out_root, record)
            persist_runtime(plan.out_root, runtime.global_step, runtime.progress)
            logger.info(
                "[step %s] action=%s cand=%.4f current=%.4f best=%.4f",
                step.global_step,
                record.get("action"),
                record.get("cand_gate_score", 0.0),
                runtime.progress.current_score,
                runtime.progress.best_score,
            )

        self._close_epoch(plan, runtime, epoch, display_epoch)

    def _run_step(self, plan: TrainPlan, runtime: TrainRuntime, step: StepId) -> Dict[str, Any]:
        """Rollout → reflect → aggregate → select → apply → gate for one step."""
        progress = runtime.progress
        epoch_dir = os.path.join(plan.out_root, f"epoch_{step.epoch:03d}")
        step_dir = os.path.join(epoch_dir, f"step_{step.step_in_epoch:03d}")
        os.makedirs(step_dir, exist_ok=True)

        rollout_dir = os.path.join(step_dir, "rollout")
        train_env = plan.adapter.build_train_env(batch_size=plan.batch_size, seed=step.seed)
        rollout_results = plan.adapter.rollout(train_env, progress.current_skill, rollout_dir)

        # Conversations live under rollout/predictions/<id>/
        raw_patches = plan.adapter.reflect(
            rollout_results,
            progress.current_skill,
            step_dir,
            prediction_dir=os.path.join(rollout_dir, "predictions"),
            patches_dir=os.path.join(step_dir, "patches"),
            random_seed=step.seed,
            meta_skill_context=runtime.meta_skill,
        )
        merged_patch = merge_patches(
            progress.current_skill,
            _split_by_source(raw_patches, "failure"),
            _split_by_source(raw_patches, "success"),
            plan.merge_settings(runtime.meta_skill),
        )

        edit_budget = runtime.scheduler.step()
        ranked_patch = rank_and_select(
            progress.current_skill,
            merged_patch,
            max_edits=edit_budget,
            update_mode=plan.update_mode,
            meta_skill_context=runtime.meta_skill,
        )
        candidate_skill, apply_report = apply_patch_with_report(progress.current_skill, ranked_patch)
        Path(step_dir, "candidate_skill.md").write_text(candidate_skill, encoding="utf-8")

        gate_dir = os.path.join(step_dir, "gate_eval")
        cand_hard, cand_soft = compute_score(plan.adapter.rollout(runtime.sel_env, candidate_skill, gate_dir))
        cand_score = plan.gate_score(cand_hard, cand_soft, candidate_skill)

        if plan.use_gate:
            action = progress.adopt(
                evaluate_gate(
                    candidate_skill,
                    cand_hard,
                    progress.gate_state(),
                    step.global_step,
                    cand_soft=cand_soft,
                    metric=plan.gate_metric,
                    mixed_weight=plan.gate_mixed_weight,
                )
            )
        else:
            action = progress.promote(candidate_skill, cand_score, step.global_step)

        runtime.sel_cache[skill_hash(candidate_skill)] = (cand_hard, cand_soft)
        save_skill_version(plan.out_root, step.global_step, progress.current_skill)

        rollout_hard, rollout_soft = compute_score(rollout_results)
        return {
            "epoch": step.epoch,
            "display_epoch": step.display_epoch,
            "step_in_epoch": step.step_in_epoch,
            "global_step": step.global_step,
            "action": action,
            "edit_budget": edit_budget,
            "rollout_hard": rollout_hard,
            "rollout_soft": rollout_soft,
            "cand_hard": cand_hard,
            "cand_soft": cand_soft,
            "cand_gate_score": cand_score,
            "current_score": progress.current_score,
            "best_score": progress.best_score,
            "best_step": progress.best_step,
            "n_edits_applied": _applied_count(apply_report),
            "skill_hash": skill_hash(progress.current_skill),
        }

    def _close_epoch(self, plan: TrainPlan, runtime: TrainRuntime, epoch: int, display_epoch: int) -> None:
        """Run the slow update and meta skill stages, in that order."""
        last_step_skill = runtime.progress.current_skill
        pairs: Optional[List[dict]] = None

        if plan.use_slow:
            outcome = self._run_epoch_slow_update(
                adapter=plan.adapter,
                dataloader=plan.dataloader,
                cfg=plan.cfg,
                out_root=plan.out_root,
                epoch=epoch,
                display_epoch=display_epoch,
                history=runtime.history,
                current_skill=runtime.progress.current_skill,
                current_score=runtime.progress.current_score,
                best_skill=runtime.progress.best_skill,
                best_score=runtime.progress.best_score,
                best_step=runtime.progress.best_step,
                global_step=runtime.global_step,
                seed=plan.seed,
                slow_n=plan.slow_n,
                longitudinal_pair_policy=plan.pair_policy,
                slow_gate_with_selection=plan.slow_gate_with_selection,
                gate_metric=plan.gate_metric,
                gate_mixed_weight=plan.gate_mixed_weight,
                sel_env=runtime.sel_env,
                sel_cache=runtime.sel_cache,
            )
            runtime.progress = SkillProgress(
                current_skill=outcome.current_skill,
                current_score=outcome.current_score,
                best_skill=outcome.best_skill,
                best_score=outcome.best_score,
                best_step=outcome.best_step,
            )
            pairs = outcome.comparison_pairs

        if plan.use_meta:
            self._run_epoch_meta_skill(
                adapter=plan.adapter,
                dataloader=plan.dataloader,
                out_root=plan.out_root,
                epoch=epoch,
                display_epoch=display_epoch,
                history=runtime.history,
                epoch_last_step_skill=last_step_skill,
                epoch_comparison_pairs=pairs,
                seed=plan.seed,
                slow_n=plan.slow_n,
                longitudinal_pair_policy=plan.pair_policy,
            )

    def _finalize(self, plan: TrainPlan, runtime: TrainRuntime) -> SkillTrainResult:
        """Persist the best skill and the run summary, then report the result."""
        progress = runtime.progress
        Path(plan.out_root, "best_skill.md").write_text(progress.best_skill, encoding="utf-8")
        persist_runtime(plan.out_root, runtime.global_step, progress)

        warnings: List[str] = []
        carries_guidance = has_slow_update_field(progress.current_skill) and extract_slow_update_field(
            progress.current_skill
        )
        if carries_guidance and progress.current_skill != progress.best_skill:
            warnings.append(_FORCED_SLOW_UPDATE_WARNING)

        save_json(
            os.path.join(plan.out_root, "summary.json"),
            {
                "best_score": progress.best_score,
                "best_step": progress.best_step,
                "total_steps": runtime.global_step,
                "gate_metric": plan.gate_metric,
                "env": plan.env_name,
                "current_score": progress.current_score,
                "use_slow_update": plan.use_slow,
                "use_meta_skill": plan.use_meta,
                "reasoning_effort": plan.cfg.get("reasoning_effort"),
                "warnings": warnings,
                "elapsed_s": time.time(),
            },
        )

        return SkillTrainResult(
            best_skill=progress.best_skill,
            best_score=progress.best_score,
            current_skill=progress.current_skill,
            current_score=progress.current_score,
            history=runtime.history,
            output_dir=plan.out_root,
        )

    # ── Epoch-boundary stages ────────────────────────────────────────────────

    @staticmethod
    def _run_epoch_slow_update(**fields: Any) -> SlowUpdateOutcome:
        """Run the epoch-boundary slow update.

        Keyword arguments are the fields of
        :class:`~openjiuwen.agent_evolving.skill_train.epoch_stages.SlowUpdateRequest`.
        """
        return SlowUpdateStage(SlowUpdateRequest(**fields)).run()

    @staticmethod
    def _run_epoch_meta_skill(**fields: Any) -> Optional[List[dict]]:
        """Run the epoch-boundary meta skill stage.

        Keyword arguments are the fields of
        :class:`~openjiuwen.agent_evolving.skill_train.epoch_stages.MetaSkillRequest`.
        """
        return MetaSkillStage(MetaSkillRequest(**fields)).run()
