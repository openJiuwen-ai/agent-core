# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Selection policy for longitudinal comparison pairs.

A longitudinal pair records how one task fared under two consecutive skill
versions.  The slow update and the meta skill both consume those pairs, but
they do not always want all of them: a run can ask for only the tasks whose
outcome *changed* between versions, or only the ones that stayed put.

When the "changed" policy does not find enough pairs in the sampled batch,
extra train items are rolled out one at a time until the target count is met.
"""

from __future__ import annotations

import os
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, NamedTuple

from openjiuwen.agent_evolving.skill_train.datasets.base import BatchSpec
from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter
from openjiuwen.agent_evolving.skill_train.slow_update import build_comparison_pairs
from openjiuwen.agent_evolving.skill_train.utils import safe_fs_id
from openjiuwen.core.common.logging import logger

#: Cap top-up rollouts as a multiple of ``target_n`` so sparse ``changed``
#: policies cannot exhaust the whole train split.
_TOPUP_ATTEMPT_MULTIPLIER = 5

MIXED = "mixed"
CHANGED = "changed"
UNCHANGED = "unchanged"

_POLICY_ALIASES = {
    MIXED: (MIXED, "default", "random", "all"),
    CHANGED: (CHANGED, "change", "delta", "10_01", "01_10"),
    UNCHANGED: (UNCHANGED, "stable", "same", "00_11"),
}

_CANONICAL_POLICY = {alias: policy for policy, aliases in _POLICY_ALIASES.items() for alias in aliases}

#: Categories each policy keeps; ``mixed`` keeps everything and is not listed.
_KEPT_CATEGORIES = {
    CHANGED: frozenset({"improved", "regressed"}),
    UNCHANGED: frozenset({"persistent_fail", "stable_success"}),
}

_ALL_CATEGORIES = ("improved", "regressed", "persistent_fail", "stable_success")


class LongitudinalPairs(NamedTuple):
    """Pairs surviving the policy filter, alongside every pair that was built."""

    selected: List[dict]
    everything: List[dict]


def normalise_longitudinal_pair_policy(policy: str | None) -> str:
    """Map a user-supplied policy token onto its canonical spelling."""
    token = str(policy or MIXED).strip().lower()
    canonical = _CANONICAL_POLICY.get(token)
    if canonical is None:
        raise ValueError("longitudinal_pair_policy must be one of mixed, changed, unchanged")
    return canonical


def filter_longitudinal_pairs(pairs: List[dict], policy: str) -> List[dict]:
    """Keep only the pairs whose outcome category the policy asks for."""
    if policy == MIXED:
        return pairs
    wanted = _KEPT_CATEGORIES.get(policy)
    if wanted is None:
        raise ValueError(f"Unknown longitudinal pair policy: {policy}")
    return [pair for pair in pairs if pair.get("category") in wanted]


def pair_category_counts(pairs: List[dict]) -> Dict[str, int]:
    """Tally pairs per outcome category, with all known categories present."""
    counts = dict.fromkeys(_ALL_CATEGORIES, 0)
    for pair in pairs:
        category = str(pair.get("category", ""))
        counts[category] = counts.get(category, 0) + 1
    return counts


@dataclass
class LongitudinalSpec:
    """Inputs for building one epoch's longitudinal comparison pairs."""

    adapter: EnvAdapter
    dataloader: Any
    prev_skill: str
    curr_skill: str
    initial_items: List[dict]
    initial_prev_results: List[dict]
    initial_curr_results: List[dict]
    prev_rollout_dir: str
    curr_rollout_dir: str
    policy: str
    target_n: int
    seed: int
    out_root: str

    def wants_topup(self, have: int) -> bool:
        """Top-up only makes sense for ``changed`` runs that fell short."""
        return self.policy == CHANGED and have < self.target_n and self.dataloader is not None


def _unseen_train_items(spec: LongitudinalSpec, seen_ids: set[str]) -> List[dict]:
    """Shuffle the train split and drop anything already compared."""
    train_items = list(getattr(spec.dataloader, "train_items", []) or [])
    shuffled = list(train_items)
    random.Random(spec.seed).shuffle(shuffled)
    return [item for item in shuffled if str(item.get("id", "")) not in seen_ids]


class ParallelRolloutRequest(NamedTuple):
    """Inputs for rolling out two skill versions against the same env."""

    adapter: EnvAdapter
    env: Any
    prev_skill: str
    curr_skill: str
    prev_dir: str
    curr_dir: str


def parallel_skill_rollouts(request: ParallelRolloutRequest) -> tuple[List[dict], List[dict]]:
    """Run prev/curr skill rollouts in parallel (independent out dirs)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        prev_fut = pool.submit(request.adapter.rollout, request.env, request.prev_skill, request.prev_dir)
        curr_fut = pool.submit(request.adapter.rollout, request.env, request.curr_skill, request.curr_dir)
        return prev_fut.result(), curr_fut.result()


def _topup_pairs(spec: LongitudinalSpec, candidates: List[dict]) -> Iterator[List[dict]]:
    """Roll out one extra train item at a time, yielding its comparison pair."""
    for offset, item in enumerate(candidates):
        item_seed = spec.seed + offset + 1
        item_id = safe_fs_id(str(item.get("id", f"item_{offset}")))
        batch = BatchSpec(phase="train", split="train", seed=item_seed, batch_size=1, payload=[item])
        env = spec.adapter.build_env_from_batch(batch, out_root=spec.out_root)
        prev_dir = os.path.join(spec.prev_rollout_dir, "topup", item_id)
        curr_dir = os.path.join(spec.curr_rollout_dir, "topup", item_id)
        prev_results, curr_results = parallel_skill_rollouts(
            ParallelRolloutRequest(
                adapter=spec.adapter,
                env=env,
                prev_skill=spec.prev_skill,
                curr_skill=spec.curr_skill,
                prev_dir=prev_dir,
                curr_dir=curr_dir,
            )
        )
        yield build_comparison_pairs(
            prev_results,
            curr_results,
            [item],
            prev_rollout_dir=prev_dir,
            curr_rollout_dir=curr_dir,
        )


def build_longitudinal_pairs(spec: LongitudinalSpec) -> LongitudinalPairs:
    """Build comparison pairs for one epoch boundary under *spec*'s policy.

    ``mixed`` keeps every pair, ``unchanged`` filters without topping up, and
    ``changed`` keeps improved/regressed pairs and scans the train split once
    to reach ``target_n`` when the sampled batch is not enough.
    """
    everything = build_comparison_pairs(
        spec.initial_prev_results,
        spec.initial_curr_results,
        spec.initial_items,
        prev_rollout_dir=spec.prev_rollout_dir,
        curr_rollout_dir=spec.curr_rollout_dir,
    )
    selected = filter_longitudinal_pairs(everything, spec.policy)
    if not spec.wants_topup(len(selected)):
        return LongitudinalPairs(selected, everything)

    seen_ids = {str(pair.get("id", "")) for pair in everything}
    candidates = _unseen_train_items(spec, seen_ids)
    if not candidates:
        return LongitudinalPairs(selected, everything)

    # Pull lazily so no rollout is paid for once the target count is reached.
    # Also cap attempts: sparse ``changed`` rates must not exhaust the train set.
    max_attempts = max(spec.target_n * _TOPUP_ATTEMPT_MULTIPLIER, spec.target_n)
    stream = _topup_pairs(spec, candidates)
    attempts = 0
    while len(selected) < spec.target_n and attempts < max_attempts:
        extra = next(stream, None)
        if extra is None:
            break
        attempts += 1
        everything.extend(extra)
        selected.extend(filter_longitudinal_pairs(extra, spec.policy))

    if len(selected) < spec.target_n:
        logger.warning(
            "[longitudinal] topup stopped with %s/%s pairs after %s attempts (cap=%s)",
            len(selected),
            spec.target_n,
            attempts,
            max_attempts,
        )

    limit = spec.target_n
    return LongitudinalPairs(selected[:limit], everything)
