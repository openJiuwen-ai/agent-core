# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReflACT training orchestrator for offline skill optimization."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjiuwen.agent_evolving.skill_train.aggregate import merge_patches
from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig
from openjiuwen.agent_evolving.skill_train.edit_budget_scheduler import build_scheduler
from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter
from openjiuwen.agent_evolving.skill_train.gate import evaluate_gate, select_gate_score
from openjiuwen.agent_evolving.skill_train.llm_client import (
    ChatLLMClient,
    make_llm_invoke_policy,
    set_optimizer_client,
    set_target_client,
)
from openjiuwen.agent_evolving.skill_train.longitudinal import (
    build_longitudinal_pairs,
    normalise_longitudinal_pair_policy,
    pair_category_counts,
)
from openjiuwen.agent_evolving.skill_train.meta_skill import (
    load_meta_skill_content,
    run_meta_skill,
    save_meta_skill_result,
)
from openjiuwen.agent_evolving.skill_train.model_compat import set_reasoning_effort
from openjiuwen.agent_evolving.skill_train.registry import get_env_adapter
from openjiuwen.agent_evolving.skill_train.scoring import compute_score, skill_hash
from openjiuwen.agent_evolving.skill_train.select import rank_and_select
from openjiuwen.agent_evolving.skill_train.skill_patch import apply_patch_with_report
from openjiuwen.agent_evolving.skill_train.slow_update import (
    extract_slow_update_field,
    has_slow_update_field,
    inject_empty_slow_update_field,
    replace_slow_update_field,
    run_slow_update,
    save_comparison_pairs,
)
from openjiuwen.agent_evolving.skill_train.state import (
    append_history,
    load_json,
    save_json,
    save_runtime_state,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model


@dataclass
class SkillTrainResult:
    """Outcome of a ReflACT training run."""

    best_skill: str
    best_score: float
    current_skill: str
    current_score: float
    history: List[Dict[str, Any]] = field(default_factory=list)
    output_dir: str = ""


def _display_epoch(epoch_idx: int) -> int:
    """Map 0-based trainer epoch index to SkillOpt 1-based display epoch."""
    return int(epoch_idx) + 1


def _save_skill_version(out_root: str, step: int, content: str) -> None:
    skills_dir = Path(out_root) / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / f"skill_v{step:04d}.md").write_text(content, encoding="utf-8")


def _load_skill_version(out_root: str, step: int) -> str:
    path = Path(out_root) / "skills" / f"skill_v{step:04d}.md"
    return path.read_text(encoding="utf-8")


def _persist_runtime(
    out_root: str,
    *,
    global_step: int,
    current_skill: str,
    best_skill: str,
    current_score: float,
    best_score: float,
    best_step: int,
) -> None:
    save_runtime_state(
        out_root,
        {
            "global_step": global_step,
            "current_skill": current_skill,
            "best_skill": best_skill,
            "current_score": current_score,
            "best_score": best_score,
            "best_step": best_step,
        },
    )
    Path(out_root, "best_skill.md").write_text(best_skill, encoding="utf-8")


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
        self._optimizer_client = ChatLLMClient(
            llm=optimizer_llm, model=optimizer_model, policy=policy
        )
        self._target_client = ChatLLMClient(
            llm=target_llm, model=target_model, policy=policy
        )

    def train(
        self,
        *,
        config: SkillTrainConfig,
        adapter: EnvAdapter | None = None,
    ) -> SkillTrainResult:
        cfg = config.to_trainer_cfg()
        out_root = os.path.abspath(config.output_dir)
        os.makedirs(out_root, exist_ok=True)

        set_optimizer_client(self._optimizer_client)
        set_target_client(self._target_client)
        set_reasoning_effort(cfg.get("reasoning_effort"))

        adapter = adapter or get_env_adapter(config.env_name, **config.env_kwargs)
        adapter.setup(cfg)

        skill_init_path = cfg.get("skill_init") or config.skill_init
        if not skill_init_path or not os.path.isfile(skill_init_path):
            raise FileNotFoundError(f"skill_init not found: {skill_init_path}")
        current_skill = Path(skill_init_path).read_text(encoding="utf-8")
        best_skill = current_skill

        batch_size = int(cfg.get("batch_size", 40))
        accumulation = max(1, int(cfg.get("accumulation", 1)))
        num_epochs = int(cfg.get("num_epochs", 4))
        seed = int(cfg.get("seed", 42))
        train_size = int(cfg.get("train_size") or 0)
        dataloader = adapter.get_dataloader()
        if train_size <= 0 and dataloader is not None:
            train_size = int(dataloader.get_train_size() or 0)
        if train_size <= 0:
            raise ValueError("train_size must be > 0 (set explicitly or via dataloader)")

        steps_per_epoch = math.ceil(train_size / (batch_size * accumulation))
        total_steps = num_epochs * steps_per_epoch
        scheduler = build_scheduler(
            mode=str(cfg.get("lr_scheduler", "cosine")),
            max_lr=int(cfg.get("edit_budget", 4)),
            min_lr=int(cfg.get("min_edit_budget", 2)),
            total_steps=max(total_steps, 1),
        )

        gate_metric = str(cfg.get("gate_metric", "hard"))
        gate_mixed_weight = float(cfg.get("gate_mixed_weight", 0.5))
        use_gate = cfg.get("use_gate", True) is not False
        update_mode = str(cfg.get("skill_update_mode", "patch"))
        use_slow = cfg.get("use_slow_update", True) is not False
        use_meta = cfg.get("use_meta_skill", True) is not False
        slow_gate_with_selection = cfg.get("slow_update_gate_with_selection", False) is True
        slow_n = int(cfg.get("slow_update_samples", 20))
        longitudinal_pair_policy = normalise_longitudinal_pair_policy(
            cfg.get("longitudinal_pair_policy", "mixed")
        )

        sel_env = adapter.build_eval_env(env_num=0, split="valid_seen", seed=seed)
        baseline_dir = os.path.join(out_root, "selection_eval_baseline")
        baseline_results = adapter.rollout(sel_env, current_skill, baseline_dir)
        baseline_hard, baseline_soft = compute_score(baseline_results)
        current_score = select_gate_score(
            baseline_hard, baseline_soft, gate_metric, gate_mixed_weight, skill_content=current_skill
        )
        best_score = current_score
        best_step = 0
        global_step = 0
        history: List[Dict[str, Any]] = []
        sel_cache: Dict[str, Tuple[float, float]] = {
            skill_hash(current_skill): (baseline_hard, baseline_soft),
        }
        active_meta_skill = ""

        logger.info(
            "[SkillReflACTTrainer] epochs=%s steps/epoch=%s baseline gate[%s]=%.4f "
            "slow=%s meta=%s effort=%s",
            num_epochs,
            steps_per_epoch,
            gate_metric,
            current_score,
            use_slow,
            use_meta,
            cfg.get("reasoning_effort"),
        )

        for epoch in range(num_epochs):
            display_epoch = _display_epoch(epoch)
            epoch_dir = os.path.join(out_root, f"epoch_{epoch:03d}")

            if use_meta:
                # SkillOpt loads meta from previous 1-based epoch (= our epoch index).
                active_meta_skill = load_meta_skill_content(out_root, epoch)
                if active_meta_skill:
                    logger.info(
                        "[epoch %s] loaded meta skill (%s chars)",
                        display_epoch,
                        len(active_meta_skill),
                    )

            base_seeds = dataloader.make_base_seeds(steps_per_epoch, accumulation, seed) if dataloader else []
            shuffled_seeds = (
                dataloader.shuffle_epoch_seeds(base_seeds, epoch, seed) if dataloader else [seed + epoch]
            )

            for step_in_epoch in range(steps_per_epoch):
                global_step += 1
                step_dir = os.path.join(epoch_dir, f"step_{step_in_epoch:03d}")
                os.makedirs(step_dir, exist_ok=True)
                step_seed = shuffled_seeds[step_in_epoch] if step_in_epoch < len(shuffled_seeds) else seed + global_step

                train_env = adapter.build_train_env(batch_size=batch_size, seed=step_seed)
                rollout_dir = os.path.join(step_dir, "rollout")
                patches_dir = os.path.join(step_dir, "patches")
                rollout_results = adapter.rollout(train_env, current_skill, rollout_dir)

                # Must match SkillOpt: conversations live under rollout/predictions/<id>/
                pred_dir = os.path.join(rollout_dir, "predictions")
                raw_patches = adapter.reflect(
                    rollout_results,
                    current_skill,
                    step_dir,
                    prediction_dir=pred_dir,
                    patches_dir=patches_dir,
                    random_seed=step_seed,
                    meta_skill_context=active_meta_skill,
                )
                failure_patches = [p for p in raw_patches if p and p.get("source_type") == "failure"]
                success_patches = [p for p in raw_patches if p and p.get("source_type") == "success"]

                merged_patch = merge_patches(
                    current_skill,
                    failure_patches,
                    success_patches,
                    batch_size=int(cfg.get("merge_batch_size", 8)),
                    workers=int(cfg.get("analyst_workers", 16)),
                    update_mode=update_mode,
                    meta_skill_context=active_meta_skill,
                )

                edit_budget = scheduler.step()
                ranked_patch = rank_and_select(
                    current_skill,
                    merged_patch,
                    max_edits=edit_budget,
                    update_mode=update_mode,
                    meta_skill_context=active_meta_skill,
                )
                candidate_skill, apply_report = apply_patch_with_report(current_skill, ranked_patch)
                candidate_path = os.path.join(step_dir, "candidate_skill.md")
                Path(candidate_path).write_text(candidate_skill, encoding="utf-8")

                gate_dir = os.path.join(step_dir, "gate_eval")
                gate_results = adapter.rollout(sel_env, candidate_skill, gate_dir)
                cand_hard, cand_soft = compute_score(gate_results)
                cand_score = select_gate_score(
                    cand_hard,
                    cand_soft,
                    gate_metric,
                    gate_mixed_weight,
                    skill_content=candidate_skill,
                )

                if use_gate:
                    gate = evaluate_gate(
                        candidate_skill,
                        cand_hard,
                        current_skill,
                        current_score,
                        best_skill,
                        best_score,
                        best_step,
                        global_step,
                        cand_soft=cand_soft,
                        metric=gate_metric,
                        mixed_weight=gate_mixed_weight,
                    )
                    action = gate.action
                    current_skill = gate.current_skill
                    current_score = gate.current_score
                    best_skill = gate.best_skill
                    best_score = gate.best_score
                    best_step = gate.best_step
                else:
                    action = "accept"
                    current_skill = candidate_skill
                    current_score = cand_score
                    if cand_score > best_score:
                        best_skill = candidate_skill
                        best_score = cand_score
                        best_step = global_step
                        action = "accept_new_best"

                sel_cache[skill_hash(candidate_skill)] = (cand_hard, cand_soft)
                _save_skill_version(out_root, global_step, current_skill)

                step_rec: Dict[str, Any] = {
                    "epoch": epoch,
                    "display_epoch": display_epoch,
                    "step_in_epoch": step_in_epoch,
                    "global_step": global_step,
                    "action": action,
                    "edit_budget": edit_budget,
                    "rollout_hard": compute_score(rollout_results)[0],
                    "rollout_soft": compute_score(rollout_results)[1],
                    "cand_hard": cand_hard,
                    "cand_soft": cand_soft,
                    "cand_gate_score": cand_score,
                    "current_score": current_score,
                    "best_score": best_score,
                    "best_step": best_step,
                    "n_edits_applied": sum(
                        1 for r in apply_report if str(r.get("status", "")).startswith("applied")
                    ),
                    "skill_hash": skill_hash(current_skill),
                }
                history.append(step_rec)
                append_history(out_root, step_rec)
                _persist_runtime(
                    out_root,
                    global_step=global_step,
                    current_skill=current_skill,
                    best_skill=best_skill,
                    current_score=current_score,
                    best_score=best_score,
                    best_step=best_step,
                )
                logger.info(
                    "[step %s] action=%s cand=%.4f current=%.4f best=%.4f",
                    global_step,
                    action,
                    cand_score,
                    current_score,
                    best_score,
                )

            epoch_last_step_skill = current_skill
            epoch_comparison_pairs: List[dict] | None = None

            # ── SLOW UPDATE (end of epoch; SkillOpt order: slow then meta) ──
            if use_slow:
                current_skill, current_score, best_skill, best_score, best_step, epoch_comparison_pairs = (
                    self._run_epoch_slow_update(
                        adapter=adapter,
                        dataloader=dataloader,
                        cfg=cfg,
                        out_root=out_root,
                        epoch=epoch,
                        display_epoch=display_epoch,
                        history=history,
                        current_skill=current_skill,
                        current_score=current_score,
                        best_skill=best_skill,
                        best_score=best_score,
                        best_step=best_step,
                        global_step=global_step,
                        seed=seed,
                        slow_n=slow_n,
                        longitudinal_pair_policy=longitudinal_pair_policy,
                        slow_gate_with_selection=slow_gate_with_selection,
                        gate_metric=gate_metric,
                        gate_mixed_weight=gate_mixed_weight,
                        sel_env=sel_env,
                        sel_cache=sel_cache,
                    )
                )

            # ── META SKILL (optimizer-side; does not mutate target skill) ──
            if use_meta:
                epoch_comparison_pairs = self._run_epoch_meta_skill(
                    adapter=adapter,
                    dataloader=dataloader,
                    out_root=out_root,
                    epoch=epoch,
                    display_epoch=display_epoch,
                    history=history,
                    epoch_last_step_skill=epoch_last_step_skill,
                    epoch_comparison_pairs=epoch_comparison_pairs,
                    seed=seed,
                    slow_n=slow_n,
                    longitudinal_pair_policy=longitudinal_pair_policy,
                )

        best_path = os.path.join(out_root, "best_skill.md")
        Path(best_path).write_text(best_skill, encoding="utf-8")
        _persist_runtime(
            out_root,
            global_step=global_step,
            current_skill=current_skill,
            best_skill=best_skill,
            current_score=current_score,
            best_score=best_score,
            best_step=best_step,
        )

        final_warnings: List[str] = []
        if has_slow_update_field(current_skill) and extract_slow_update_field(current_skill):
            if current_skill != best_skill:
                final_warnings.append(
                    "final current_skill carries force-injected slow_update content "
                    "that was not applied to best_skill (SkillOpt force_accept semantics)"
                )

        summary = {
            "best_score": best_score,
            "best_step": best_step,
            "total_steps": global_step,
            "gate_metric": gate_metric,
            "env": config.env_name,
            "current_score": current_score,
            "use_slow_update": use_slow,
            "use_meta_skill": use_meta,
            "reasoning_effort": cfg.get("reasoning_effort"),
            "warnings": final_warnings,
            "elapsed_s": time.time(),
        }
        save_json(os.path.join(out_root, "summary.json"), summary)

        return SkillTrainResult(
            best_skill=best_skill,
            best_score=best_score,
            current_skill=current_skill,
            current_score=current_score,
            history=history,
            output_dir=out_root,
        )

    def _build_slow_env(
        self,
        adapter: EnvAdapter,
        dataloader: Any,
        *,
        batch_size: int,
        seed: int,
        out_root: str,
    ) -> Tuple[Any, List[dict]]:
        if dataloader is not None:
            batch = dataloader.build_train_batch(
                batch_size=batch_size,
                seed=seed,
                out_root=out_root,
            )
            env = adapter.build_env_from_batch(batch, out_root=out_root)
        else:
            env = adapter.build_train_env(batch_size=batch_size, seed=seed, out_root=out_root)
        items = list(env) if hasattr(env, "__iter__") else env
        return env, list(items)

    def _run_epoch_slow_update(
        self,
        *,
        adapter: EnvAdapter,
        dataloader: Any,
        cfg: Dict[str, Any],
        out_root: str,
        epoch: int,
        display_epoch: int,
        history: List[Dict[str, Any]],
        current_skill: str,
        current_score: float,
        best_skill: str,
        best_score: float,
        best_step: int,
        global_step: int,
        seed: int,
        slow_n: int,
        longitudinal_pair_policy: str,
        slow_gate_with_selection: bool,
        gate_metric: str,
        gate_mixed_weight: float,
        sel_env: Any,
        sel_cache: Dict[str, Tuple[float, float]],
    ) -> Tuple[str, float, str, float, int, Optional[List[dict]]]:
        del cfg  # reserved for future env-specific overrides
        slow_dir = os.path.join(out_root, "slow_update", f"epoch_{display_epoch:02d}")
        slow_done_path = os.path.join(slow_dir, "slow_result.json")
        epoch_comparison_pairs: Optional[List[dict]] = None

        if os.path.exists(slow_done_path):
            logger.info("[SLOW UPDATE epoch %s] resumed — already done", display_epoch)
            slow_saved = load_json(slow_done_path, default={}) or {}
            comparison_path = os.path.join(slow_dir, "comparison_pairs.json")
            if os.path.exists(comparison_path):
                try:
                    epoch_comparison_pairs = load_json(comparison_path, default=None)
                except Exception:
                    epoch_comparison_pairs = None
            if slow_saved.get("slow_update_content") and display_epoch >= 2:
                action = slow_saved.get("action")
                if slow_gate_with_selection:
                    if action in {"accept", "accept_new_best"}:
                        current_skill = replace_slow_update_field(
                            current_skill, slow_saved["slow_update_content"]
                        )
                elif action in {"accept", "accept_new_best", "force_accept"}:
                    current_skill = replace_slow_update_field(
                        current_skill, slow_saved["slow_update_content"]
                    )
            return (
                current_skill,
                current_score,
                best_skill,
                best_score,
                best_step,
                epoch_comparison_pairs,
            )

        if display_epoch == 1:
            os.makedirs(slow_dir, exist_ok=True)
            current_skill = inject_empty_slow_update_field(current_skill)
            _save_skill_version(out_root, global_step, current_skill)
            save_json(slow_done_path, {"action": "inject_placeholder", "epoch": display_epoch})
            _persist_runtime(
                out_root,
                global_step=global_step,
                current_skill=current_skill,
                best_skill=best_skill,
                current_score=current_score,
                best_score=best_score,
                best_step=best_step,
            )
            logger.info("[SLOW UPDATE epoch %s] injected empty placeholder", display_epoch)
            return (
                current_skill,
                current_score,
                best_skill,
                best_score,
                best_step,
                None,
            )

        os.makedirs(slow_dir, exist_ok=True)
        logger.info(
            "[SLOW UPDATE] Epoch %s (comparing epoch %s vs %s)",
            display_epoch,
            display_epoch - 1,
            display_epoch,
        )

        prev_epoch_records = [h for h in history if h.get("epoch") == epoch - 1]
        if not prev_epoch_records:
            logger.warning("[SLOW UPDATE] no previous epoch history; skipping")
            save_json(slow_done_path, {"action": "skip_no_prev_history", "epoch": display_epoch})
            return (
                current_skill,
                current_score,
                best_skill,
                best_score,
                best_step,
                None,
            )
        prev_step = int(prev_epoch_records[-1]["global_step"])
        prev_skill = _load_skill_version(out_root, prev_step)

        slow_seed = seed + display_epoch * 2000
        slow_env, slow_items = self._build_slow_env(
            adapter, dataloader, batch_size=slow_n, seed=slow_seed, out_root=out_root
        )
        logger.info("[slow update] sampled %s train items (seed=%s)", len(slow_items), slow_seed)

        t_slow = time.time()
        prev_rollout_dir = os.path.join(slow_dir, "rollout_prev")
        curr_rollout_dir = os.path.join(slow_dir, "rollout_curr")
        results_prev = adapter.rollout(slow_env, prev_skill, prev_rollout_dir)
        results_curr = adapter.rollout(slow_env, current_skill, curr_rollout_dir)
        prev_hard, _ = compute_score(results_prev)
        curr_hard, _ = compute_score(results_curr)
        logger.info(
            "[slow update] prev hard=%.4f curr hard=%.4f",
            prev_hard,
            curr_hard,
        )

        comparison_pairs, all_comparison_pairs = build_longitudinal_pairs(
            adapter=adapter,
            dataloader=dataloader,
            prev_skill=prev_skill,
            curr_skill=current_skill,
            initial_items=slow_items,
            initial_prev_results=results_prev,
            initial_curr_results=results_curr,
            prev_rollout_dir=prev_rollout_dir,
            curr_rollout_dir=curr_rollout_dir,
            policy=longitudinal_pair_policy,
            target_n=slow_n,
            seed=slow_seed,
            out_root=out_root,
        )
        epoch_comparison_pairs = comparison_pairs
        if all_comparison_pairs is not comparison_pairs:
            save_comparison_pairs(
                all_comparison_pairs,
                os.path.join(slow_dir, "comparison_pairs_all.json"),
            )
        save_comparison_pairs(comparison_pairs, os.path.join(slow_dir, "comparison_pairs.json"))
        counts = pair_category_counts(comparison_pairs)
        logger.info(
            "[slow update] comparison: %s policy=%s kept=%s/%s",
            counts,
            longitudinal_pair_policy,
            len(comparison_pairs),
            len(all_comparison_pairs),
        )

        existing_guidance = extract_slow_update_field(current_skill)
        slow_result = run_slow_update(
            current_skill,
            results_prev,
            results_curr,
            slow_items,
            prev_skill=prev_skill,
            prev_slow_update_content=existing_guidance,
            prev_rollout_dir=prev_rollout_dir,
            curr_rollout_dir=curr_rollout_dir,
            comparison_pairs=comparison_pairs,
        )
        slow_time = round(time.time() - t_slow, 1)

        if slow_result and slow_result.get("slow_update_content"):
            slow_candidate = replace_slow_update_field(
                current_skill, slow_result["slow_update_content"]
            )
            slow_candidate_hash = skill_hash(slow_candidate)
            Path(slow_dir, "candidate_skill.md").write_text(slow_candidate, encoding="utf-8")
            slow_result["time_s"] = slow_time
            slow_result["prev_hard"] = prev_hard
            slow_result["curr_hard"] = curr_hard
            slow_result["candidate_hash"] = slow_candidate_hash
            slow_result["update_origin"] = "slow_update_momentum"

            if slow_gate_with_selection:
                if slow_candidate_hash in sel_cache:
                    slow_sel_hard, slow_sel_soft = sel_cache[slow_candidate_hash]
                else:
                    slow_eval_dir = os.path.join(slow_dir, "selection_eval")
                    slow_eval_results = adapter.rollout(sel_env, slow_candidate, slow_eval_dir)
                    slow_sel_hard, slow_sel_soft = compute_score(slow_eval_results)
                    sel_cache[slow_candidate_hash] = (slow_sel_hard, slow_sel_soft)

                slow_gate = evaluate_gate(
                    slow_candidate,
                    slow_sel_hard,
                    current_skill,
                    current_score,
                    best_skill,
                    best_score,
                    best_step,
                    global_step,
                    cand_soft=slow_sel_soft,
                    metric=gate_metric,
                    mixed_weight=gate_mixed_weight,
                )
                slow_result["selection_hard"] = slow_sel_hard
                slow_result["selection_soft"] = slow_sel_soft
                slow_result["action"] = slow_gate.action
                current_skill = slow_gate.current_skill
                current_score = slow_gate.current_score
                best_skill = slow_gate.best_skill
                best_score = slow_gate.best_score
                best_step = slow_gate.best_step
                logger.info(
                    "[slow gate] action=%s hard=%.4f",
                    slow_gate.action,
                    slow_sel_hard,
                )
            else:
                slow_content = slow_result["slow_update_content"]
                current_skill = replace_slow_update_field(current_skill, slow_content)
                sel_cache[skill_hash(current_skill)] = (current_score, 0.0)
                slow_result["action"] = "force_accept"
                logger.info(
                    "[slow update] force-injected into current only (%s chars), %ss",
                    len(slow_content),
                    slow_time,
                )
        else:
            slow_result = slow_result or {}
            slow_result["action"] = "no_content"
            slow_result["time_s"] = slow_time
            logger.info("[slow update] no guidance produced, %ss", slow_time)

        save_json(slow_done_path, slow_result)
        _save_skill_version(out_root, global_step, current_skill)
        _persist_runtime(
            out_root,
            global_step=global_step,
            current_skill=current_skill,
            best_skill=best_skill,
            current_score=current_score,
            best_score=best_score,
            best_step=best_step,
        )
        logger.info(
            "[SLOW UPDATE epoch %s done] current=%.4f best=%.4f",
            display_epoch,
            current_score,
            best_score,
        )
        return (
            current_skill,
            current_score,
            best_skill,
            best_score,
            best_step,
            epoch_comparison_pairs,
        )

    def _run_epoch_meta_skill(
        self,
        *,
        adapter: EnvAdapter,
        dataloader: Any,
        out_root: str,
        epoch: int,
        display_epoch: int,
        history: List[Dict[str, Any]],
        epoch_last_step_skill: str,
        epoch_comparison_pairs: Optional[List[dict]],
        seed: int,
        slow_n: int,
        longitudinal_pair_policy: str,
    ) -> Optional[List[dict]]:
        meta_skill_dir = os.path.join(out_root, "meta_skill", f"epoch_{display_epoch:02d}")
        meta_skill_done_path = os.path.join(meta_skill_dir, "meta_skill_result.json")
        os.makedirs(meta_skill_dir, exist_ok=True)

        if os.path.exists(meta_skill_done_path):
            logger.info("[META SKILL epoch %s] resumed — already done", display_epoch)
            return epoch_comparison_pairs

        if display_epoch == 1:
            save_meta_skill_result(
                out_root,
                display_epoch,
                {"action": "skip_first_epoch", "epoch": display_epoch},
            )
            logger.info("[META SKILL epoch %s] skipped — first epoch", display_epoch)
            return epoch_comparison_pairs

        logger.info(
            "[META SKILL] Epoch %s (optimizer memory from epoch %s vs %s)",
            display_epoch,
            display_epoch - 1,
            display_epoch,
        )

        prev_epoch_records = [h for h in history if h.get("epoch") == epoch - 1]
        if not prev_epoch_records:
            save_meta_skill_result(
                out_root,
                display_epoch,
                {"action": "skip_no_prev_history", "epoch": display_epoch},
            )
            return epoch_comparison_pairs

        prev_step = int(prev_epoch_records[-1]["global_step"])
        prev_skill = _load_skill_version(out_root, prev_step)
        prev_meta_skill = load_meta_skill_content(out_root, display_epoch - 1)

        if epoch_comparison_pairs is None:
            meta_seed = seed + display_epoch * 2000
            meta_env, meta_items = self._build_slow_env(
                adapter, dataloader, batch_size=slow_n, seed=meta_seed, out_root=out_root
            )
            prev_rollout_dir = os.path.join(meta_skill_dir, "rollout_prev")
            curr_rollout_dir = os.path.join(meta_skill_dir, "rollout_curr")
            results_prev = adapter.rollout(meta_env, prev_skill, prev_rollout_dir)
            results_curr = adapter.rollout(meta_env, epoch_last_step_skill, curr_rollout_dir)
            epoch_comparison_pairs, all_meta_pairs = build_longitudinal_pairs(
                adapter=adapter,
                dataloader=dataloader,
                prev_skill=prev_skill,
                curr_skill=epoch_last_step_skill,
                initial_items=meta_items,
                initial_prev_results=results_prev,
                initial_curr_results=results_curr,
                prev_rollout_dir=prev_rollout_dir,
                curr_rollout_dir=curr_rollout_dir,
                policy=longitudinal_pair_policy,
                target_n=slow_n,
                seed=meta_seed,
                out_root=out_root,
            )
            if all_meta_pairs is not epoch_comparison_pairs:
                save_comparison_pairs(
                    all_meta_pairs,
                    os.path.join(meta_skill_dir, "comparison_pairs_all.json"),
                )
            save_comparison_pairs(
                epoch_comparison_pairs,
                os.path.join(meta_skill_dir, "comparison_pairs.json"),
            )
            logger.info(
                "[meta skill] comparison: %s policy=%s kept=%s/%s",
                pair_category_counts(epoch_comparison_pairs),
                longitudinal_pair_policy,
                len(epoch_comparison_pairs),
                len(all_meta_pairs),
            )

        t_meta = time.time()
        meta_skill_result = run_meta_skill(
            prev_skill=prev_skill,
            curr_skill=epoch_last_step_skill,
            comparison_pairs=epoch_comparison_pairs or [],
            prev_meta_skill_content=prev_meta_skill,
        )
        meta_time = round(time.time() - t_meta, 1)

        if meta_skill_result and meta_skill_result.get("meta_skill_content"):
            meta_skill_result["time_s"] = meta_time
            meta_skill_result["action"] = "write_meta_skill"
            logger.info(
                "[meta skill] memory written (%s chars), %ss",
                len(meta_skill_result["meta_skill_content"]),
                meta_time,
            )
        else:
            meta_skill_result = meta_skill_result or {}
            meta_skill_result["time_s"] = meta_time
            meta_skill_result["action"] = "no_content"
            logger.info("[meta skill] no memory produced, %ss", meta_time)

        save_meta_skill_result(out_root, display_epoch, meta_skill_result)
        return epoch_comparison_pairs
