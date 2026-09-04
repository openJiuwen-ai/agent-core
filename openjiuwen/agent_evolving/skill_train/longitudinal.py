# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Longitudinal comparison pair helpers shared by slow_update and meta_skill."""

from __future__ import annotations

import os
import random
import re
from typing import Any, Dict, List, Tuple

from openjiuwen.agent_evolving.skill_train.datasets.base import BatchSpec
from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter
from openjiuwen.agent_evolving.skill_train.slow_update import build_comparison_pairs


def normalise_longitudinal_pair_policy(policy: str | None) -> str:
    raw = str(policy or "mixed").strip().lower()
    aliases = {
        "mixed": "mixed",
        "default": "mixed",
        "random": "mixed",
        "all": "mixed",
        "changed": "changed",
        "change": "changed",
        "delta": "changed",
        "10_01": "changed",
        "01_10": "changed",
        "unchanged": "unchanged",
        "stable": "unchanged",
        "same": "unchanged",
        "00_11": "unchanged",
    }
    if raw not in aliases:
        raise ValueError(
            "longitudinal_pair_policy must be one of mixed, changed, unchanged"
        )
    return aliases[raw]


def filter_longitudinal_pairs(pairs: List[dict], policy: str) -> List[dict]:
    if policy == "mixed":
        return pairs
    if policy == "changed":
        keep = {"improved", "regressed"}
    elif policy == "unchanged":
        keep = {"persistent_fail", "stable_success"}
    else:
        raise ValueError(f"Unknown longitudinal pair policy: {policy}")
    return [p for p in pairs if p.get("category") in keep]


def pair_category_counts(pairs: List[dict]) -> Dict[str, int]:
    counts = {
        "improved": 0,
        "regressed": 0,
        "persistent_fail": 0,
        "stable_success": 0,
    }
    for pair in pairs:
        cat = str(pair.get("category", ""))
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def _safe_pair_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return safe[:80] or "item"


def build_longitudinal_pairs(
    *,
    adapter: EnvAdapter,
    dataloader: Any,
    prev_skill: str,
    curr_skill: str,
    initial_items: List[dict],
    initial_prev_results: List[dict],
    initial_curr_results: List[dict],
    prev_rollout_dir: str,
    curr_rollout_dir: str,
    policy: str,
    target_n: int,
    seed: int,
    out_root: str,
) -> Tuple[List[dict], List[dict]]:
    """Build longitudinal pairs, optionally filtering by change category.

    ``mixed`` preserves all pairs. ``changed`` keeps only improved/regressed and
    tops up to ``target_n`` by scanning the train split once. ``unchanged``
    keeps persistent_fail/stable_success and does not top up.
    """
    all_pairs = build_comparison_pairs(
        initial_prev_results,
        initial_curr_results,
        initial_items,
        prev_rollout_dir=prev_rollout_dir,
        curr_rollout_dir=curr_rollout_dir,
    )
    selected_pairs = filter_longitudinal_pairs(all_pairs, policy)
    if policy != "changed" or len(selected_pairs) >= target_n or dataloader is None:
        return selected_pairs, all_pairs

    train_items = list(getattr(dataloader, "train_items", []) or [])
    if not train_items:
        return selected_pairs, all_pairs

    seen_ids = {str(p.get("id", "")) for p in all_pairs}
    rng = random.Random(seed)
    candidates = list(train_items)
    rng.shuffle(candidates)
    candidates = [item for item in candidates if str(item.get("id", "")) not in seen_ids]

    for idx, item in enumerate(candidates):
        if len(selected_pairs) >= target_n:
            break
        item_id = _safe_pair_id(str(item.get("id", f"item_{idx}")))
        batch = BatchSpec(
            phase="train",
            split="train",
            seed=seed + idx + 1,
            batch_size=1,
            payload=[item],
        )
        env = adapter.build_env_from_batch(batch, out_root=out_root)
        prev_dir = os.path.join(prev_rollout_dir, "topup", item_id)
        curr_dir = os.path.join(curr_rollout_dir, "topup", item_id)
        prev_results = adapter.rollout(env, prev_skill, prev_dir)
        curr_results = adapter.rollout(env, curr_skill, curr_dir)
        pair = build_comparison_pairs(
            prev_results,
            curr_results,
            [item],
            prev_rollout_dir=prev_dir,
            curr_rollout_dir=curr_dir,
        )
        all_pairs.extend(pair)
        selected_pairs.extend(filter_longitudinal_pairs(pair, policy))

    return selected_pairs[:target_n], all_pairs
