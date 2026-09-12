# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Epoch-boundary stages of the ReflACT trainer.

Each training epoch ends with two optional stages that the per-step loop knows
nothing about:

``SlowUpdateStage``
    Compares the epoch's last skill against the previous epoch's, then rewrites
    the protected slow-update region of the target skill.
``MetaSkillStage``
    Distils the same comparison into optimizer-side memory.  It never touches
    the target skill.

Both stages are resumable: a marker file under their own epoch directory means
the work already happened, so a restarted run replays the saved decision rather
than paying for the rollouts again.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter
from openjiuwen.agent_evolving.skill_train.gate import GateResult, GateState, evaluate_gate
from openjiuwen.agent_evolving.skill_train.longitudinal import (
    LongitudinalSpec,
    build_longitudinal_pairs,
    pair_category_counts,
)
from openjiuwen.agent_evolving.skill_train.meta_skill import (
    load_meta_skill_content,
    run_meta_skill,
    save_meta_skill_result,
)
from openjiuwen.agent_evolving.skill_train.scoring import compute_score, skill_hash
from openjiuwen.agent_evolving.skill_train.slow_update import (
    SlowUpdateInputs,
    extract_slow_update_field,
    inject_empty_slow_update_field,
    replace_slow_update_field,
    run_slow_update,
    save_comparison_pairs,
)
from openjiuwen.agent_evolving.skill_train.state import load_json, save_json, save_runtime_state
from openjiuwen.core.common.logging import logger

SLOW_MARKER = "slow_result.json"
PAIRS_FILE = "comparison_pairs.json"
ALL_PAIRS_FILE = "comparison_pairs_all.json"

#: Which recorded slow-update decisions re-apply their guidance on resume.
_RESUME_ACCEPT_GATED = frozenset({"accept", "accept_new_best"})
_RESUME_ACCEPT_FORCED = frozenset({"accept", "accept_new_best", "force_accept"})

#: Epoch seeds are spread far apart so slow/meta batches never collide.
EPOCH_SEED_STRIDE = 2000

ScoreCache = Dict[str, Tuple[float, float]]


# ── Shared bookkeeping ───────────────────────────────────────────────────────


def display_epoch_of(epoch_index: int) -> int:
    """Map a 0-based trainer epoch onto the 1-based epoch users see."""
    return int(epoch_index) + 1


def save_skill_version(out_root: str, step: int, content: str) -> None:
    """Snapshot the skill document produced at *step*."""
    skills_dir = Path(out_root) / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / f"skill_v{step:04d}.md").write_text(content, encoding="utf-8")


def load_skill_version(out_root: str, step: int) -> str:
    """Read back the skill document snapshotted at *step*."""
    return (Path(out_root) / "skills" / f"skill_v{step:04d}.md").read_text(encoding="utf-8")


@dataclass
class SkillProgress:
    """The current/best skill bookkeeping every stage reads and updates."""

    current_skill: str
    current_score: float
    best_skill: str
    best_score: float
    best_step: int

    def gate_state(self) -> GateState:
        """Expose this progress as the immutable snapshot the gate expects."""
        return GateState(
            current_skill=self.current_skill,
            current_score=self.current_score,
            best_skill=self.best_skill,
            best_score=self.best_score,
            best_step=self.best_step,
        )

    def adopt(self, verdict: GateResult) -> str:
        """Apply a gate verdict in place and return the action it chose."""
        self.current_skill = verdict.current_skill
        self.current_score = verdict.current_score
        self.best_skill = verdict.best_skill
        self.best_score = verdict.best_score
        self.best_step = verdict.best_step
        return verdict.action

    def promote(self, skill: str, score: float, step: int) -> str:
        """Accept *skill* unconditionally, recording a new best when it wins."""
        self.current_skill = skill
        self.current_score = score
        if score <= self.best_score:
            return "accept"
        self.best_skill = skill
        self.best_score = score
        self.best_step = step
        return "accept_new_best"

    def runtime_state(self, global_step: int) -> Dict[str, Any]:
        return {
            "global_step": global_step,
            "current_skill": self.current_skill,
            "best_skill": self.best_skill,
            "current_score": self.current_score,
            "best_score": self.best_score,
            "best_step": self.best_step,
        }


def persist_runtime(out_root: str, global_step: int, progress: SkillProgress) -> None:
    """Checkpoint the trainer's mutable state plus the current best skill."""
    save_runtime_state(out_root, progress.runtime_state(global_step))
    Path(out_root, "best_skill.md").write_text(progress.best_skill, encoding="utf-8")


class SampledBatch(NamedTuple):
    """A built environment together with the items it was built from."""

    env: Any
    items: List[dict]


def sample_train_batch(
    adapter: EnvAdapter,
    dataloader: Any,
    *,
    batch_size: int,
    seed: int,
    out_root: str,
) -> SampledBatch:
    """Draw one train batch, preferring the dataloader when the env has one."""
    if dataloader is None:
        env = adapter.build_train_env(batch_size=batch_size, seed=seed, out_root=out_root)
    else:
        batch = dataloader.build_train_batch(batch_size=batch_size, seed=seed, out_root=out_root)
        env = adapter.build_env_from_batch(batch, out_root=out_root)
    items = list(env) if hasattr(env, "__iter__") else env
    return SampledBatch(env, list(items))


def last_step_of_epoch(history: List[Dict[str, Any]], epoch: int) -> Optional[int]:
    """Global step of the final recorded step in *epoch*, if it ran at all."""
    steps = [record for record in history if record.get("epoch") == epoch]
    if not steps:
        return None
    return int(steps[-1].get("global_step", 0))


# ── Slow update ──────────────────────────────────────────────────────────────


@dataclass
class SlowUpdateRequest:
    """Everything the slow update stage needs for one epoch boundary."""

    adapter: Any
    dataloader: Any
    cfg: Dict[str, Any]
    out_root: str
    epoch: int
    display_epoch: int
    history: List[Dict[str, Any]]
    current_skill: str
    current_score: float
    best_skill: str
    best_score: float
    best_step: int
    global_step: int
    seed: int
    slow_n: int
    longitudinal_pair_policy: str
    slow_gate_with_selection: bool
    gate_metric: str
    gate_mixed_weight: float
    sel_env: Any
    sel_cache: ScoreCache

    def progress(self) -> SkillProgress:
        return SkillProgress(
            current_skill=self.current_skill,
            current_score=self.current_score,
            best_skill=self.best_skill,
            best_score=self.best_score,
            best_step=self.best_step,
        )

    def epoch_seed(self) -> int:
        return self.seed + self.display_epoch * EPOCH_SEED_STRIDE


class SlowUpdateOutcome(NamedTuple):
    """Post-stage progress plus the comparison pairs the meta stage can reuse."""

    current_skill: str
    current_score: float
    best_skill: str
    best_score: float
    best_step: int
    comparison_pairs: Optional[List[dict]]


class SlowUpdateStage:
    """Runs (or replays) the slow update for a single epoch boundary."""

    def __init__(self, request: SlowUpdateRequest) -> None:
        self.req = request
        self.progress = request.progress()
        self.dir = os.path.join(request.out_root, "slow_update", f"epoch_{request.display_epoch:02d}")
        self.marker = os.path.join(self.dir, SLOW_MARKER)

    # -- entry point ---------------------------------------------------------

    def run(self) -> SlowUpdateOutcome:
        """Replay a finished stage, seed epoch 1, or do the real comparison."""
        if os.path.exists(self.marker):
            return self._replay()
        os.makedirs(self.dir, exist_ok=True)
        if self.req.display_epoch == 1:
            return self._seed_placeholder()
        return self._refine()

    def _finish(self, pairs: Optional[List[dict]]) -> SlowUpdateOutcome:
        return SlowUpdateOutcome(
            self.progress.current_skill,
            self.progress.current_score,
            self.progress.best_skill,
            self.progress.best_score,
            self.progress.best_step,
            pairs,
        )

    def _checkpoint(self) -> None:
        save_skill_version(self.req.out_root, self.req.global_step, self.progress.current_skill)
        persist_runtime(self.req.out_root, self.req.global_step, self.progress)

    # -- resume path ---------------------------------------------------------

    def _saved_pairs(self) -> Optional[List[dict]]:
        path = os.path.join(self.dir, PAIRS_FILE)
        if not os.path.exists(path):
            return None
        try:
            return load_json(path, default=None)
        except Exception:  # noqa: BLE001 - a broken cache just means "recompute"
            return None

    def _replay(self) -> SlowUpdateOutcome:
        display = self.req.display_epoch
        logger.info("[SLOW UPDATE epoch %s] resumed — already done", display)
        saved = load_json(self.marker, default={}) or {}
        guidance = saved.get("slow_update_content")
        accepted = _RESUME_ACCEPT_GATED if self.req.slow_gate_with_selection else _RESUME_ACCEPT_FORCED
        if guidance and display >= 2 and saved.get("action") in accepted:
            self.progress.current_skill = replace_slow_update_field(self.progress.current_skill, guidance)
        return self._finish(self._saved_pairs())

    # -- epoch 1 -------------------------------------------------------------

    def _seed_placeholder(self) -> SlowUpdateOutcome:
        """Epoch 1 only reserves the protected region; there is nothing to compare."""
        self.progress.current_skill = inject_empty_slow_update_field(self.progress.current_skill)
        save_skill_version(self.req.out_root, self.req.global_step, self.progress.current_skill)
        save_json(self.marker, {"action": "inject_placeholder", "epoch": self.req.display_epoch})
        persist_runtime(self.req.out_root, self.req.global_step, self.progress)
        logger.info("[SLOW UPDATE epoch %s] injected empty placeholder", self.req.display_epoch)
        return self._finish(None)

    # -- full path -----------------------------------------------------------

    def _refine(self) -> SlowUpdateOutcome:
        req = self.req
        display = req.display_epoch
        logger.info("[SLOW UPDATE] Epoch %s (comparing epoch %s vs %s)", display, display - 1, display)

        prev_step = last_step_of_epoch(req.history, req.epoch - 1)
        if prev_step is None:
            logger.warning("[SLOW UPDATE] no previous epoch history; skipping")
            save_json(self.marker, {"action": "skip_no_prev_history", "epoch": display})
            return self._finish(None)

        prev_skill = load_skill_version(req.out_root, prev_step)
        slow_seed = req.epoch_seed()
        batch = sample_train_batch(
            req.adapter,
            req.dataloader,
            batch_size=req.slow_n,
            seed=slow_seed,
            out_root=req.out_root,
        )
        logger.info("[slow update] sampled %s train items (seed=%s)", len(batch.items), slow_seed)

        started = time.time()
        prev_dir = os.path.join(self.dir, "rollout_prev")
        curr_dir = os.path.join(self.dir, "rollout_curr")
        results_prev = req.adapter.rollout(batch.env, prev_skill, prev_dir)
        results_curr = req.adapter.rollout(batch.env, self.progress.current_skill, curr_dir)
        prev_hard, _ = compute_score(results_prev)
        curr_hard, _ = compute_score(results_curr)
        logger.info("[slow update] prev hard=%.4f curr hard=%.4f", prev_hard, curr_hard)

        pairs = build_longitudinal_pairs(
            LongitudinalSpec(
                adapter=req.adapter,
                dataloader=req.dataloader,
                prev_skill=prev_skill,
                curr_skill=self.progress.current_skill,
                initial_items=batch.items,
                initial_prev_results=results_prev,
                initial_curr_results=results_curr,
                prev_rollout_dir=prev_dir,
                curr_rollout_dir=curr_dir,
                policy=req.longitudinal_pair_policy,
                target_n=req.slow_n,
                seed=slow_seed,
                out_root=req.out_root,
            )
        )
        self._persist_pairs(pairs.selected, pairs.everything, "slow update")

        draft = run_slow_update(
            SlowUpdateInputs(
                skill_content=self.progress.current_skill,
                results_prev=results_prev,
                results_curr=results_curr,
                items=batch.items,
                prev_skill=prev_skill,
                prev_slow_update_content=extract_slow_update_field(self.progress.current_skill),
                prev_rollout_dir=prev_dir,
                curr_rollout_dir=curr_dir,
                comparison_pairs=pairs.selected,
            )
        )
        elapsed = round(time.time() - started, 1)

        record = self._adopt_guidance(draft, elapsed, prev_hard, curr_hard)
        save_json(self.marker, record)
        self._checkpoint()
        logger.info(
            "[SLOW UPDATE epoch %s done] current=%.4f best=%.4f",
            display,
            self.progress.current_score,
            self.progress.best_score,
        )
        return self._finish(pairs.selected)

    def _persist_pairs(self, selected: List[dict], everything: List[dict], tag: str) -> None:
        """Save the kept pairs, plus the unfiltered set when it differs."""
        if everything is not selected:
            save_comparison_pairs(everything, os.path.join(self.dir, ALL_PAIRS_FILE))
        save_comparison_pairs(selected, os.path.join(self.dir, PAIRS_FILE))
        logger.info(
            "[%s] comparison: %s policy=%s kept=%s/%s",
            tag,
            pair_category_counts(selected),
            self.req.longitudinal_pair_policy,
            len(selected),
            len(everything),
        )

    def _adopt_guidance(
        self,
        draft: Optional[Dict[str, Any]],
        elapsed: float,
        prev_hard: float,
        curr_hard: float,
    ) -> Dict[str, Any]:
        """Fold new guidance into the skill and return the record to persist."""
        guidance = (draft or {}).get("slow_update_content")
        if not guidance:
            record = dict(draft or {})
            record["action"] = "no_content"
            record["time_s"] = elapsed
            logger.info("[slow update] no guidance produced, %ss", elapsed)
            return record

        record = dict(draft or {})
        candidate = replace_slow_update_field(self.progress.current_skill, guidance)
        candidate_hash = skill_hash(candidate)
        Path(self.dir, "candidate_skill.md").write_text(candidate, encoding="utf-8")
        record.update(
            {
                "time_s": elapsed,
                "prev_hard": prev_hard,
                "curr_hard": curr_hard,
                "candidate_hash": candidate_hash,
                "update_origin": "slow_update_momentum",
            }
        )

        if self.req.slow_gate_with_selection:
            self._gate_candidate(record, candidate, candidate_hash)
        else:
            self.progress.current_skill = replace_slow_update_field(self.progress.current_skill, guidance)
            self.req.sel_cache[skill_hash(self.progress.current_skill)] = (self.progress.current_score, 0.0)
            record["action"] = "force_accept"
            logger.info(
                "[slow update] force-injected into current only (%s chars), %ss",
                len(guidance),
                elapsed,
            )
        return record

    def _gate_candidate(self, record: Dict[str, Any], candidate: str, candidate_hash: str) -> None:
        """Score the slow-update candidate and let the selection gate decide."""
        req = self.req
        cached = req.sel_cache.get(candidate_hash)
        if cached is None:
            eval_dir = os.path.join(self.dir, "selection_eval")
            cached = compute_score(req.adapter.rollout(req.sel_env, candidate, eval_dir))
            req.sel_cache[candidate_hash] = cached
        sel_hard, sel_soft = cached

        verdict = evaluate_gate(
            candidate,
            sel_hard,
            self.progress.gate_state(),
            req.global_step,
            cand_soft=sel_soft,
            metric=req.gate_metric,
            mixed_weight=req.gate_mixed_weight,
        )
        record["selection_hard"] = sel_hard
        record["selection_soft"] = sel_soft
        record["action"] = self.progress.adopt(verdict)
        logger.info("[slow gate] action=%s hard=%.4f", verdict.action, sel_hard)


# ── Meta skill ───────────────────────────────────────────────────────────────


@dataclass
class MetaSkillRequest:
    """Everything the optimizer-memory stage needs for one epoch boundary."""

    adapter: Any
    dataloader: Any
    out_root: str
    epoch: int
    display_epoch: int
    history: List[Dict[str, Any]]
    epoch_last_step_skill: str
    epoch_comparison_pairs: Optional[List[dict]]
    seed: int
    slow_n: int
    longitudinal_pair_policy: str

    def epoch_seed(self) -> int:
        return self.seed + self.display_epoch * EPOCH_SEED_STRIDE


class MetaSkillStage:
    """Writes optimizer-side memory; never mutates the target skill."""

    def __init__(self, request: MetaSkillRequest) -> None:
        self.req = request
        self.dir = os.path.join(request.out_root, "meta_skill", f"epoch_{request.display_epoch:02d}")
        self.marker = os.path.join(self.dir, "meta_skill_result.json")

    def run(self) -> Optional[List[dict]]:
        """Return the comparison pairs, building them first when necessary."""
        req = self.req
        display = req.display_epoch
        os.makedirs(self.dir, exist_ok=True)

        if os.path.exists(self.marker):
            logger.info("[META SKILL epoch %s] resumed — already done", display)
            return req.epoch_comparison_pairs

        if display == 1:
            save_meta_skill_result(req.out_root, display, {"action": "skip_first_epoch", "epoch": display})
            logger.info("[META SKILL epoch %s] skipped — first epoch", display)
            return req.epoch_comparison_pairs

        logger.info(
            "[META SKILL] Epoch %s (optimizer memory from epoch %s vs %s)",
            display,
            display - 1,
            display,
        )

        prev_step = last_step_of_epoch(req.history, req.epoch - 1)
        if prev_step is None:
            save_meta_skill_result(
                req.out_root,
                display,
                {"action": "skip_no_prev_history", "epoch": display},
            )
            return req.epoch_comparison_pairs

        prev_skill = load_skill_version(req.out_root, prev_step)
        pairs = req.epoch_comparison_pairs
        if pairs is None:
            pairs = self._build_pairs(prev_skill)

        started = time.time()
        memory = run_meta_skill(
            prev_skill=prev_skill,
            curr_skill=req.epoch_last_step_skill,
            comparison_pairs=pairs or [],
            prev_meta_skill_content=load_meta_skill_content(req.out_root, display - 1),
        )
        save_meta_skill_result(req.out_root, display, self._record(memory, round(time.time() - started, 1)))
        return pairs

    @staticmethod
    def _record(memory: Optional[Dict[str, Any]], elapsed: float) -> Dict[str, Any]:
        """Normalise the optimizer answer into the JSON record to persist."""
        record = dict(memory or {})
        record["time_s"] = elapsed
        content = record.get("meta_skill_content")
        if content:
            record["action"] = "write_meta_skill"
            logger.info("[meta skill] memory written (%s chars), %ss", len(content), elapsed)
        else:
            record["action"] = "no_content"
            logger.info("[meta skill] no memory produced, %ss", elapsed)
        return record

    def _build_pairs(self, prev_skill: str) -> List[dict]:
        """Roll out both skill versions when the slow stage did not do it."""
        req = self.req
        meta_seed = req.epoch_seed()
        batch = sample_train_batch(
            req.adapter,
            req.dataloader,
            batch_size=req.slow_n,
            seed=meta_seed,
            out_root=req.out_root,
        )
        prev_dir = os.path.join(self.dir, "rollout_prev")
        curr_dir = os.path.join(self.dir, "rollout_curr")
        pairs = build_longitudinal_pairs(
            LongitudinalSpec(
                adapter=req.adapter,
                dataloader=req.dataloader,
                prev_skill=prev_skill,
                curr_skill=req.epoch_last_step_skill,
                initial_items=batch.items,
                initial_prev_results=req.adapter.rollout(batch.env, prev_skill, prev_dir),
                initial_curr_results=req.adapter.rollout(batch.env, req.epoch_last_step_skill, curr_dir),
                prev_rollout_dir=prev_dir,
                curr_rollout_dir=curr_dir,
                policy=req.longitudinal_pair_policy,
                target_n=req.slow_n,
                seed=meta_seed,
                out_root=req.out_root,
            )
        )
        if pairs.everything is not pairs.selected:
            save_comparison_pairs(pairs.everything, os.path.join(self.dir, ALL_PAIRS_FILE))
        save_comparison_pairs(pairs.selected, os.path.join(self.dir, PAIRS_FILE))
        logger.info(
            "[meta skill] comparison: %s policy=%s kept=%s/%s",
            pair_category_counts(pairs.selected),
            req.longitudinal_pair_policy,
            len(pairs.selected),
            len(pairs.everything),
        )
        return pairs.selected
