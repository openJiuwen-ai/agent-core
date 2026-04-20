# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RLMetricsTracker & TrainingDiagnostics
---------------------------------------

Wraps verl's Tracking to provide structured logging for RL-specific
metrics (rollout stats, reward distributions, conversation turns, etc.),
and consolidates all training diagnostics (DIAG_S0–S4, DIAG_DATA) into
a single module.

Supported backends (via verl Tracking):
tensorboard, wandb, mlflow, swanlab, clearml, file
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from openjiuwen.core.common.logging import logger


@dataclass
class TrainingStepMetrics:
    """Encapsulates all metrics for a single training step log entry."""

    step: int
    epoch: int
    verl_metrics: Dict[str, Any]
    avg_turns: float
    reward_mean: float
    consecutive_zero_reward_steps: int


class RLMetricsTracker:
    """Structured metrics tracker for RL training.

    Uses verl's Tracking as the unified logging backend.
    """

    def __init__(
        self,
        project_name: str,
        experiment_name: str,
        backends: List[str],
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._backends = backends
        self._tracking = None
        self._initialized = False
        self._init_kwargs = {
            "project_name": project_name,
            "experiment_name": experiment_name,
            "default_backend": backends,
            "config": config,
        }

    def _ensure_initialized(self) -> None:
        """Lazily initialize the underlying verl Tracking backend."""
        if self._initialized:
            return
        self._initialized = True
        try:
            from verl.utils.tracking import Tracking
            self._tracking = Tracking(**self._init_kwargs)
            logger.info(
                "RLMetricsTracker initialized with backends: %s", self._backends
            )
        except ImportError:
            logger.warning(
                "verl.utils.tracking not available, metrics logging disabled"
            )

    # -- core log method ------------------------------------------------------

    def log_step(self, step: int, metrics: Dict[str, Any]) -> None:
        """Log a dict of scalar metrics at the given step."""
        self._ensure_initialized()
        if self._tracking is not None:
            self._tracking.log(data=metrics, step=step)

    # -- structured logging helpers -------------------------------------------

    def log_training_step(self, data: TrainingStepMetrics) -> None:
        """Log a complete training step with RL-augmented metrics."""
        metrics = dict(data.verl_metrics)
        metrics.update({
            "training/global_step": data.step,
            "training/epoch": data.epoch,
            "training/avg_conversation_turns": data.avg_turns,
            "training/rollout_reward_mean": data.reward_mean,
            "training/consecutive_zero_reward_steps": data.consecutive_zero_reward_steps,
        })
        self.log_step(data.step, metrics)

    def log_rollout_stats(
        self,
        step: int,
        rewards_by_uid: Dict[str, List[Dict[str, Any]]],
        total_positive: int = 0,
        total_negative: int = 0,
        total_training_samples: Optional[int] = None,
    ) -> None:
        """Log structured rollout statistics.

        total_training_samples: Number of training samples used in the current step
            (granularity after splitting, e.g. per-turn count when using per-turn).
            If provided, used for rollout/total_rollouts; otherwise uses trajectory
            count len(all_rewards).
        """
        all_rewards = []
        for entries in rewards_by_uid.values():
            for entry in entries:
                val = entry.get("global")
                if val is not None:
                    all_rewards.append(val)

        if not all_rewards:
            return

        total = total_positive + total_negative
        n_rollouts = (
            total_training_samples
            if total_training_samples is not None
            else len(all_rewards)
        )
        rollout_metrics = {
            "rollout/reward_mean": float(np.mean(all_rewards)),
            "rollout/reward_std": float(np.std(all_rewards)),
            "rollout/reward_max": float(max(all_rewards)),
            "rollout/reward_min": float(min(all_rewards)),
            "rollout/positive_ratio": total_positive / max(total, 1),
            "rollout/total_rollouts": n_rollouts,
            "rollout/unique_prompts": len(rewards_by_uid),
        }
        self.log_step(step, rollout_metrics)

    def log_reward_distribution(self, step: int, rewards: List[float]) -> None:
        """Log reward distribution histogram (WandB only)."""
        self._ensure_initialized()
        if self._tracking is None or not rewards:
            return
        tracking_loggers = getattr(self._tracking, "logger", {})
        if "wandb" in tracking_loggers:
            try:
                import wandb
                wandb.log(
                    {"rollout/reward_hist": wandb.Histogram(rewards)},
                    step=step,
                )
            except Exception as e:
                logger.debug("Failed to log WandB reward histogram: %s", e)

    def log_validation(self, step: int, val_metrics: Dict[str, Any]) -> None:
        """Log validation metrics."""
        self.log_step(step, val_metrics)

    def finish(self) -> None:
        """Clean up tracking resources."""
        if self._tracking is not None and hasattr(self._tracking, "finish"):
            self._tracking.finish()


class TrainingDiagnostics:
    """Consolidated training diagnostics for the RL training pipeline.

    Stages:
    - DIAG_DATA: rollout encoding diagnostics
    - DIAG_S0:   batch assembly EOS position & reward placement
    - DIAG_S1:   post-reward batch diagnostics
    - DIAG_S2:   old log prob diagnostics
    - DIAG_S3:   advantage diagnostics
    - DIAG_S4:   actor update diagnostics
    """

    def __init__(self, tokenizer=None) -> None:
        self.tokenizer = tokenizer

    # -- DIAG_DATA: encoding --------------------------------------------------

    @staticmethod
    def diag_encoding(rolloutmsg, total_turns: int, global_reward: float) -> None:
        """Log rollout encoding diagnostics (DIAG_DATA)."""
        logger.info(
            "[DIAG_DATA] build: rollout_id=%s  total_turns=%d  "
            "global_reward=%.4f  reward_list=%s  "
            "origin_task_id=%s",
            rolloutmsg.rollout_id,
            total_turns,
            global_reward,
            [round(r, 4) for r in (rolloutmsg.reward_list or [])],
            rolloutmsg.origin_task_id,
        )

    # -- DIAG_S0: batch assembly ----------------------------------------------

    @dataclass
    class BatchAssemblyDiag:
        """Inputs for DIAG_S0 batch assembly diagnostics."""

        input_ids: Any
        response_ids: Any
        attention_mask: Any
        position_ids: Any
        token_scores: Any
        scores: Any
        n_transition: int

    @staticmethod
    def diag_batch_assembly(
        diag: "TrainingDiagnostics.BatchAssemblyDiag",
    ) -> None:
        """Log batch assembly EOS position & reward placement diagnostics (DIAG_S0)."""
        import torch

        input_ids = diag.input_ids
        response_ids = diag.response_ids
        attention_mask = diag.attention_mask
        position_ids = diag.position_ids
        token_scores = diag.token_scores
        scores = diag.scores
        n_transition = diag.n_transition

        prompt_len = input_ids.size(-1)
        resp_len = response_ids.size(-1)
        eos_pos = torch.argmax(position_ids * attention_mask, dim=-1)
        resp_attn_sums = attention_mask[:, prompt_len:].sum(dim=-1)
        reward_in_resp = token_scores.sum(dim=-1)
        problems = []
        for i in range(min(n_transition, 4)):
            eos_in_resp = eos_pos[i].item() >= prompt_len
            resp_active = resp_attn_sums[i].item()
            reward_sum = reward_in_resp[i].item()
            original_reward = scores[i].item()
            reward_ok = abs(reward_sum - original_reward) < 1e-3
            logger.info(
                "[DIAG_S0] sample %d: eos_abs_pos=%d  prompt_len=%d  "
                "eos_in_response=%s  resp_active_tokens=%d/%d  "
                "reward_in_scores=%.4f  original_reward=%.4f  match=%s",
                i, eos_pos[i].item(), prompt_len,
                eos_in_resp, resp_active, resp_len,
                reward_sum, original_reward, reward_ok,
            )
            if not eos_in_resp:
                problems.append(
                    f"sample {i}: eos_pos={eos_pos[i].item()} < prompt_len={prompt_len}, "
                    "reward in PROMPT part"
                )
            if not reward_ok:
                problems.append(
                    f"sample {i}: reward mismatch {reward_sum:.4f} != {original_reward:.4f}"
                )
        if problems:
            logger.warning("[DIAG_S0] PROBLEMS FOUND:\n  %s", "\n  ".join(problems))
        else:
            logger.info(
                "[DIAG_S0] OK: all %d checked samples have correct EOS & reward placement",
                min(n_transition, 4),
            )

    # -- DIAG_S1: post-reward -------------------------------------------------

    @staticmethod
    def diag_after_reward(batch) -> None:
        """Log post-reward batch diagnostics (DIAG_S1)."""
        response_mask = batch.batch.get("response_mask")
        token_scores = batch.batch.get("token_level_scores")
        attn_mask = batch.batch.get("attention_mask")
        n = len(batch)

        resp_sums = response_mask.sum(dim=-1).float()
        attn_sums = attn_mask.sum(dim=-1).float()
        score_sums = token_scores.sum(dim=-1).float()
        logger.info(
            "[DIAG_S1] n_samples=%d  resp_mask: mean=%.1f min=%d max=%d  "
            "attn_mask: mean=%.1f  score_sums: min=%.4f max=%.4f  "
            "has_actor_loss_mask=%s",
            n,
            resp_sums.mean().item(), int(resp_sums.min()), int(resp_sums.max()),
            attn_sums.mean().item(),
            score_sums.min().item(), score_sums.max().item(),
            "actor_loss_mask" in batch.batch,
        )

        uids = batch.non_tensor_batch.get("uid")
        if uids is None:
            return
        uid_to_indices: dict = {}
        for idx, uid in enumerate(uids):
            uid_to_indices.setdefault(uid, []).append(idx)

        group_sizes = [len(v) for v in uid_to_indices.values()]
        n_groups = len(uid_to_indices)
        no_var_count = 0
        for uid, indices in uid_to_indices.items():
            rewards = [score_sums[i].item() for i in indices]
            if len(set(round(r, 4) for r in rewards)) <= 1:
                no_var_count += 1

        logger.info(
            "[DIAG_S1] groups=%d  group_sizes=%s  no_reward_variance=%d/%d",
            n_groups,
            sorted(set(group_sizes)),
            no_var_count, n_groups,
        )

        for uid in list(uid_to_indices.keys())[:2]:
            indices = uid_to_indices[uid]
            rewards = [round(score_sums[i].item(), 4) for i in indices]
            resp_masks = [int(resp_sums[i]) for i in indices]
            logger.info(
                "[DIAG_S1] group=%s size=%d  rewards=%s  resp_masks=%s",
                uid[:8], len(indices), rewards, resp_masks,
            )

    # -- DIAG_S2: old log prob ------------------------------------------------

    @staticmethod
    def diag_after_old_log_prob(batch) -> None:
        """Log old log prob diagnostics (DIAG_S2)."""
        import torch
        old_lp = batch.batch.get("old_log_probs")
        response_mask = batch.batch.get("response_mask")
        if old_lp is None:
            logger.warning("[DIAG_S2] old_log_probs not found in batch!")
            return

        masked = old_lp[response_mask.bool()] if response_mask is not None else old_lp.flatten()
        nan_cnt = torch.isnan(masked).sum().item()
        inf_cnt = torch.isinf(masked).sum().item()
        neginf_cnt = (masked == float("-inf")).sum().item()
        finite = masked[torch.isfinite(masked)]

        logger.info(
            "[DIAG_S2] old_log_probs (on resp tokens): "
            "n=%d  nan=%d  inf=%d  neg_inf=%d  "
            "mean=%.4f  std=%.4f  min=%.4f  max=%.4f  "
            "pct_below_m10=%.2f%%",
            masked.numel(), nan_cnt, inf_cnt, neginf_cnt,
            finite.mean().item() if finite.numel() > 0 else 0,
            finite.std().item() if finite.numel() > 1 else 0,
            finite.min().item() if finite.numel() > 0 else 0,
            finite.max().item() if finite.numel() > 0 else 0,
            (finite < -10).float().mean().item() * 100 if finite.numel() > 0 else 0,
        )

        if nan_cnt > 0 or neginf_cnt > 0:
            logger.warning(
                "[DIAG_S2] PROBLEM: old_log_probs has %d NaN and %d -inf on response tokens! "
                "This will cause NaN in PPO ratio.",
                nan_cnt, neginf_cnt,
            )

        for i in range(min(len(batch), 4)):
            mask_i = response_mask[i].bool()
            lp_i = old_lp[i][mask_i]
            logger.info(
                "[DIAG_S2] sample %d: resp_tokens=%d  lp_mean=%.4f  lp_min=%.4f  lp_max=%.4f",
                i, mask_i.sum().item(),
                lp_i.mean().item(), lp_i.min().item(), lp_i.max().item(),
            )

    # -- DIAG_S3: advantages --------------------------------------------------

    @staticmethod
    def diag_after_advantages(batch) -> None:
        """Log advantage diagnostics (DIAG_S3)."""
        import torch
        adv = batch.batch.get("advantages")
        token_rewards = batch.batch.get("token_level_rewards")
        response_mask = batch.batch.get("response_mask")

        if adv is None:
            logger.warning("[DIAG_S3] no 'advantages' in batch!")
            return

        adv_flat = adv[response_mask.bool()] if response_mask is not None else adv.flatten()
        nan_count = torch.isnan(adv_flat).sum().item()
        inf_count = torch.isinf(adv_flat).sum().item()
        finite = adv_flat[torch.isfinite(adv_flat)]

        logger.info(
            "[DIAG_S3] advantages: n=%d  nan=%d  inf=%d  "
            "mean=%.6f  std=%.6f  min=%.4f  max=%.4f",
            adv_flat.numel(), nan_count, inf_count,
            finite.mean().item() if finite.numel() > 0 else 0,
            finite.std().item() if finite.numel() > 1 else 0,
            finite.min().item() if finite.numel() > 0 else 0,
            finite.max().item() if finite.numel() > 0 else 0,
        )

        if nan_count > 0 or inf_count > 0:
            logger.warning(
                "[DIAG_S3] PROBLEM: advantages has %d NaN and %d Inf!",
                nan_count, inf_count,
            )

        if token_rewards is not None:
            tr_flat = token_rewards[response_mask.bool()] if response_mask is not None else token_rewards.flatten()
            logger.info(
                "[DIAG_S3] token_level_rewards: nonzero=%d/%d  mean=%.6f  min=%.4f  max=%.4f",
                int((tr_flat != 0).sum()), tr_flat.numel(),
                tr_flat.mean().item(), tr_flat.min().item(), tr_flat.max().item(),
            )

        per_sample = []
        for i in range(min(len(batch), 8)):
            mask_i = response_mask[i].bool() if response_mask is not None \
                else torch.ones(adv.size(-1), dtype=torch.bool)
            a = adv[i][mask_i]
            r = token_rewards[i][mask_i].sum().item() if token_rewards is not None else 0
            per_sample.append(f"s{i}(r={r:.2f},adv={a.mean().item():.4f})")
        logger.info("[DIAG_S3] per_sample: %s", "  ".join(per_sample))

    # -- DIAG_S4: actor update ------------------------------------------------

    @staticmethod
    def diag_after_actor_update(metrics) -> None:
        """Log actor update diagnostics (DIAG_S4)."""
        keys_of_interest = [
            "actor/entropy_loss",
            "actor/pg_loss",
            "actor/actor_loss",
            "actor/pg_clipfrac",
            "actor/approx_kl",
            "actor/grad_norm",
        ]
        parts = []
        for k in keys_of_interest:
            v = metrics.get(k)
            if v is not None:
                parts.append(f"{k.split('/')[-1]}={v:.6f}")
        logger.info("[DIAG_S4] actor_update: %s", "  ".join(parts) if parts else "(no actor metrics found)")

        pg_loss = metrics.get("actor/pg_loss")
        clip_frac = metrics.get("actor/pg_clipfrac")
        approx_kl = metrics.get("actor/approx_kl")

        if pg_loss is not None and (abs(pg_loss) > 10.0):
            logger.warning("[DIAG_S4] PROBLEM: pg_loss=%.4f is very large (>10), possible divergence!", pg_loss)
        if clip_frac is not None and clip_frac > 0.5:
            logger.warning("[DIAG_S4] WARNING: clip_frac=%.4f > 0.5, policy changed too much", clip_frac)
        if approx_kl is not None and approx_kl > 0.1:
            logger.warning("[DIAG_S4] WARNING: approx_kl=%.4f > 0.1, large policy shift", approx_kl)

    # -- full batch diagnose --------------------------------------------------

    def diagnose_batch(self, batch, global_steps: int) -> None:
        """Full batch diagnose with token-level text output."""
        import torch
        uids = batch.non_tensor_batch.get("uid")
        if uids is None:
            logger.warning("[diagnose] 'uid' not in non_tensor_batch, skipping")
            return

        input_ids = batch.batch["input_ids"]
        attention_mask = batch.batch["attention_mask"]
        scores = batch.batch["token_level_scores"]
        response_mask = batch.batch.get("response_mask")
        loss_mask = batch.batch.get("actor_loss_mask")
        n_turns_arr = batch.non_tensor_batch.get("n_turns_list")

        prompt_len = batch.batch["prompts"].shape[-1]

        uid_to_indices: dict = {}
        for idx, uid in enumerate(uids):
            uid_to_indices.setdefault(uid, []).append(idx)

        n_groups = len(uid_to_indices)
        n_samples = len(batch)
        n_zero_resp_mask = 0
        n_zero_loss_mask = 0
        n_no_variance = 0
        reward_list_all = []

        for uid, indices in uid_to_indices.items():
            rewards = []
            for i in indices:
                r = (scores[i] * (response_mask[i] if response_mask is not None
                                  else 1)).sum().item()
                rewards.append(r)
                if response_mask is not None and response_mask[i].sum().item() == 0:
                    n_zero_resp_mask += 1
                if loss_mask is not None and loss_mask[i].sum().item() == 0:
                    n_zero_loss_mask += 1
            reward_list_all.append(rewards)
            if len(set(round(r, 6) for r in rewards)) <= 1:
                n_no_variance += 1

        logger.info(
            "[diagnose] step=%d  groups=%d  samples=%d  "
            "zero_resp_mask=%d  zero_loss_mask=%d  no_reward_variance_groups=%d",
            global_steps, n_groups, n_samples,
            n_zero_resp_mask, n_zero_loss_mask, n_no_variance,
        )

        sampled_uids = list(uid_to_indices.keys())[:2]
        for uid in sampled_uids:
            indices = uid_to_indices[uid]
            lines = [f"  [group uid={uid[:8]}  size={len(indices)}]"]

            for i in indices:
                ids = input_ids[i]
                attn = attention_mask[i]

                prompt_part = ids[:prompt_len]
                prompt_attn = attn[:prompt_len]
                resp_part = ids[prompt_len:]
                resp_attn = attn[prompt_len:]

                prompt_active = prompt_part[prompt_attn.bool()].tolist()
                resp_active = resp_part[resp_attn.bool()].tolist()

                prompt_text = self.tokenizer.decode(prompt_active, skip_special_tokens=False)
                resp_text = self.tokenizer.decode(resp_active, skip_special_tokens=False)

                r = (scores[i] * (response_mask[i] if response_mask is not None
                                  else 1)).sum().item()
                resp_mask_sum = int(response_mask[i].sum()) if response_mask is not None else -1
                loss_mask_sum = int(loss_mask[i].sum()) if loss_mask is not None else -1
                n_turns = int(n_turns_arr[i]) if n_turns_arr is not None else -1

                lines.append(
                    f"  [sample {i}] reward={r:.4f}  n_turns={n_turns}  "
                    f"prompt_tokens={len(prompt_active)}  resp_tokens={len(resp_active)}  "
                    f"resp_mask={resp_mask_sum}  loss_mask={loss_mask_sum}"
                )
                lines.append(f"    prompt: {prompt_text}")
                lines.append(f"    response: {resp_text}")

            logger.info("[diagnose]\n%s", "\n".join(lines))
