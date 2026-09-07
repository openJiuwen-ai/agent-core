# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto-dream: TTSE bank hygiene (TTL prune, soft-cluster merge, TIP purge).

Runs offline from the user turn: prune stale rules, LLM-merge near-duplicates
within each track, then deterministically retire low-quality TIPs.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

from .config import TTSEConfig
from .prompts import DREAM_MERGE_SYSTEM, dream_merge_prompt
from .stores import TTSERecordStore
from .tip_parse import is_valid_tip_shape, tip_purge_reason


SECONDS_PER_DAY = 86400.0


@dataclass
class DreamState:
    """Persisted Auto-dream counters / timestamps."""

    last_dream_at: float = 0.0
    last_pruned: int = 0
    last_merged_clusters: int = 0
    last_purged_tips: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dream_at": self.last_dream_at,
            "last_pruned": self.last_pruned,
            "last_merged_clusters": self.last_merged_clusters,
            "last_purged_tips": self.last_purged_tips,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DreamState":
        data = data or {}
        return cls(
            last_dream_at=float(data.get("last_dream_at") or 0.0),
            last_pruned=int(data.get("last_pruned") or 0),
            last_merged_clusters=int(data.get("last_merged_clusters") or 0),
            last_purged_tips=int(data.get("last_purged_tips") or 0),
        )


@dataclass
class DreamResult:
    """Summary of one dream pass."""

    skipped: bool = False
    skip_reason: str = ""
    pruned_facts: int = 0
    pruned_tips: int = 0
    merged_clusters: int = 0
    kept_clusters: int = 0
    purged_tips: int = 0
    elapsed_secs: float = 0.0


@dataclass
class MergeVerdict:
    verdict: str  # MERGE | KEEP_DISTINCT | REWRITE
    canonical: str = ""
    keep_indices: List[int] = field(default_factory=list)
    reason: str = ""


def load_dream_state(path: str) -> DreamState:
    if not path or not os.path.exists(path):
        return DreamState()
    try:
        with open(path, encoding="utf-8") as f:
            return DreamState.from_dict(json.load(f))
    except (OSError, ValueError) as exc:
        logger.warning("[TTSERail] dream-state load failed at %s: %s", path, exc)
        return DreamState()


def save_dream_state(path: str, state: DreamState) -> None:
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("[TTSERail] dream-state save failed at %s: %s", path, exc)


def should_run_dream(
    config: TTSEConfig,
    state: DreamState,
    *,
    now: Optional[float] = None,
) -> Tuple[bool, str]:
    """Gate Auto-dream (enabled + min_hours). ``min_rules`` only gates merge."""
    if not config.dream_enabled:
        return False, "dream_enabled=False"
    ts = now if now is not None else time.time()
    if state.last_dream_at > 0:
        hours = (ts - state.last_dream_at) / 3600.0
        if hours < config.dream_min_hours:
            return False, f"min_hours not met ({hours:.2f}<{config.dream_min_hours})"
    return True, ""


def _display_ts(record: Dict[str, Any]) -> float:
    value = record.get("last_injected_at")
    if value is None:
        value = record.get("created_at")
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def prune_stale(
    store: TTSERecordStore,
    config: TTSEConfig,
    *,
    now: Optional[float] = None,
) -> Tuple[int, int]:
    """Retire/delete rules not injected within ``dream_ttl_days``."""
    if not config.dream_prune_enabled:
        return 0, 0
    ts = now if now is not None else time.time()
    ttl = float(config.dream_ttl_days) * SECONDS_PER_DAY
    mode = (config.dream_prune_mode or "retire").lower()
    pruned_facts = 0
    pruned_tips = 0

    async def _prune_track(rtype: str, records: Sequence[Dict[str, Any]]) -> int:
        removed = 0
        # Snapshot texts first — mutate while iterating is unsafe.
        stale = [r for r in list(records) if (ts - _display_ts(r)) > ttl]
        for record in stale:
            text = record.get("text", "")
            if not text:
                continue
            if mode == "delete":
                n = await store.delete_record(text, rtype, save=False)
            else:
                n = await store.retire(text, rtype, "ttl_90d_no_inject", save=False)
            removed += n
        return removed

    pruned_facts = await _prune_track("fact", store.facts)
    pruned_tips = await _prune_track("tip", store.tips)
    return pruned_facts, pruned_tips


def parse_merge_verdict(text: str, cluster_size: int) -> Optional[MergeVerdict]:
    """Parse VERDICT/CANONICAL/KEEP_INDICES/REASON from LLM merge output."""
    if not text:
        return None
    verdict = ""
    canonical = ""
    keep_indices: List[int] = []
    reason = ""
    for line in str(text).splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("VERDICT"):
            payload = s.split(":", 1)[-1].strip() if ":" in s else s
            token = payload.split()[0].upper() if payload else ""
            if token in ("MERGE", "KEEP_DISTINCT", "REWRITE"):
                verdict = token
        elif up.startswith("CANONICAL"):
            canonical = s.split(":", 1)[-1].strip() if ":" in s else ""
        elif up.startswith("KEEP_INDICES"):
            payload = s.split(":", 1)[-1].strip() if ":" in s else ""
            for tok in payload.replace(",", " ").split():
                if tok.isdigit():
                    idx = int(tok)
                    if 0 <= idx < cluster_size:
                        keep_indices.append(idx)
        elif up.startswith("REASON"):
            reason = s.split(":", 1)[-1].strip() if ":" in s else s
    if not verdict:
        return None
    return MergeVerdict(verdict=verdict, canonical=canonical, keep_indices=keep_indices, reason=reason)


def _best_count_text(cluster: Sequence[Dict[str, Any]]) -> str:
    return max(cluster, key=lambda r: int(r.get("count", 0))).get("text", "")


async def _llm_merge_cluster(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    track: str,
    cluster: Sequence[Dict[str, Any]],
    sims: Sequence[Tuple[int, int, float]],
    capabilities: str,
) -> Optional[MergeVerdict]:
    rules_block = "\n".join(
        f"{i}. count={int(r.get('count', 0))} | {r.get('text', '')}" for i, r in enumerate(cluster)
    )
    sim_table = "\n".join(f"{i},{j},{sim:.3f}" for i, j, sim in sims) if sims else "(none)"
    prompt = f"{DREAM_MERGE_SYSTEM}\n\n{dream_merge_prompt(track, rules_block, sim_table, capabilities=capabilities)}"
    try:
        out = await invoke_text_with_retry(llm, model, prompt, policy=policy, temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTSERail] dream merge LLM failed: %s", exc)
        return None
    return parse_merge_verdict(out, len(cluster))


async def _apply_merge_verdict(
    store: TTSERecordStore,
    track: str,
    cluster: Sequence[Dict[str, Any]],
    verdict: MergeVerdict,
    *,
    capability_names: Optional[Set[str]] = None,
) -> str:
    """Apply MERGE/REWRITE/KEEP. Returns action label used for logging."""
    if verdict.verdict == "KEEP_DISTINCT":
        return "keep"

    canonical = (verdict.canonical or "").strip()
    if not canonical:
        canonical = _best_count_text(cluster)

    if track == "tip":
        if not is_valid_tip_shape(canonical, capability_names):
            logger.info(
                "[TTSERail] dream merge TIP canonical invalid (%s); keeping distinct",
                verdict.verdict,
            )
            return "keep_invalid_tip"

    total_count = sum(int(r.get("count", 0)) for r in cluster)
    reason = "dream_merge" if verdict.verdict == "MERGE" else "dream_rewrite"
    for record in cluster:
        await store.retire(record["text"], track, reason, save=False)
    await store.add_record_direct(track, canonical, count=max(total_count, 1), save=False)
    return verdict.verdict.lower()


async def dream_merge(
    store: TTSERecordStore,
    track: str,
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    config: TTSEConfig,
    capabilities: str = "",
    capability_names: Optional[Set[str]] = None,
) -> Tuple[int, int]:
    """Soft-cluster + LLM merge one track. Returns (merged_or_rewritten, kept)."""
    records = store.facts if track == "fact" else store.tips
    if len(records) < config.dream_cluster_min_size:
        return 0, 0
    if not store.has_embedding_provider():
        logger.info("[TTSERail] dream merge skipped for %s: no embedding provider", track)
        return 0, 0

    clusters = await store.soft_cluster(
        list(records),
        soft_lo=config.dream_soft_lo,
        min_size=config.dream_cluster_min_size,
    )
    if not clusters:
        return 0, 0

    merged = 0
    kept = 0
    budget = max(0, int(config.dream_max_llm_merges))
    for cluster in clusters:
        if budget <= 0:
            break
        budget -= 1
        sims = await store.pairwise_sims(cluster)
        verdict = await _llm_merge_cluster(
            llm=llm,
            model=model,
            policy=policy,
            track=track,
            cluster=cluster,
            sims=sims,
            capabilities=capabilities,
        )
        if verdict is None:
            logger.info("[TTSERail] dream merge cluster skipped (LLM/parse failure) track=%s size=%s", track, len(cluster))
            continue

        # TIP: one rewrite retry when MERGE/REWRITE yields invalid shape.
        action = await _apply_merge_verdict(
            store, track, cluster, verdict, capability_names=capability_names
        )
        if action == "keep_invalid_tip" and verdict.verdict in ("MERGE", "REWRITE"):
            retry = await _llm_merge_cluster(
                llm=llm,
                model=model,
                policy=policy,
                track=track,
                cluster=cluster,
                sims=sims,
                capabilities=capabilities + "\n\nPrevious CANONICAL was invalid; rewrite as a valid TIP or KEEP_DISTINCT.",
            )
            if retry is not None:
                action = await _apply_merge_verdict(
                    store, track, cluster, retry, capability_names=capability_names
                )

        if action in ("merge", "rewrite"):
            merged += 1
            logger.info(
                "[TTSERail] dream %s track=%s size=%s reason=%s",
                action,
                track,
                len(cluster),
                (verdict.reason or "")[:80],
            )
        else:
            kept += 1
            logger.info(
                "[TTSERail] dream keep_distinct track=%s size=%s reason=%s",
                track,
                len(cluster),
                (verdict.reason or "")[:80],
            )
    return merged, kept


async def dream_purge_tips(
    store: TTSERecordStore,
    capability_names: Set[str],
) -> int:
    """Deterministically retire malformed / unknown / over-generic TIPs."""
    purged = 0
    for record in list(store.tips):
        text = record.get("text", "")
        reason = tip_purge_reason(text, capability_names)
        if reason is None:
            continue
        removed = await store.retire(text, "tip", reason, save=False)
        if removed:
            purged += 1
            logger.info("[TTSERail] dream purged tip (%s): %s", reason, text[:80])
    return purged


async def run_dream_pass(
    store: TTSERecordStore,
    config: TTSEConfig,
    *,
    llm: Model,
    model: str,
    capabilities: str = "",
    capability_names: Optional[Set[str]] = None,
    state: Optional[DreamState] = None,
    now: Optional[float] = None,
) -> Tuple[DreamResult, DreamState]:
    """Full dream pipeline: prune → merge → purge. Caller holds evolution lock."""
    started = time.time()
    ts = now if now is not None else started
    path = config.resolved_dream_state_path()
    dream_state = state if state is not None else load_dream_state(path)

    ok, reason = should_run_dream(config, dream_state, now=ts)
    if not ok:
        logger.info("[TTSERail] dream skipped: %s", reason)
        return DreamResult(skipped=True, skip_reason=reason), dream_state

    result = DreamResult()
    names = capability_names or set()

    pruned_facts, pruned_tips = await prune_stale(store, config, now=ts)
    result.pruned_facts = pruned_facts
    result.pruned_tips = pruned_tips

    n_rules = len(store.facts) + len(store.tips)
    if n_rules >= config.dream_min_rules:
        mf, kf = await dream_merge(
            store,
            "fact",
            llm=llm,
            model=model,
            policy=config.induce_llm_policy,
            config=config,
            capabilities=capabilities,
            capability_names=names,
        )
        mt, kt = await dream_merge(
            store,
            "tip",
            llm=llm,
            model=model,
            policy=config.induce_llm_policy,
            config=config,
            capabilities=capabilities,
            capability_names=names,
        )
        result.merged_clusters = mf + mt
        result.kept_clusters = kf + kt
    else:
        logger.info(
            "[TTSERail] dream merge skipped: rules=%s < min_rules=%s",
            n_rules,
            config.dream_min_rules,
        )

    if config.dream_purge_tips_enabled:
        result.purged_tips = await dream_purge_tips(store, names)

    await store.save()

    dream_state.last_dream_at = ts
    dream_state.last_pruned = result.pruned_facts + result.pruned_tips
    dream_state.last_merged_clusters = result.merged_clusters
    dream_state.last_purged_tips = result.purged_tips
    save_dream_state(path, dream_state)

    result.elapsed_secs = time.time() - started
    logger.info(
        "[TTSERail] dream done pruned_facts=%s pruned_tips=%s merged=%s kept=%s purged_tips=%s elapsed=%.2fs bank=%s",
        result.pruned_facts,
        result.pruned_tips,
        result.merged_clusters,
        result.kept_clusters,
        result.purged_tips,
        result.elapsed_secs,
        store.stats(),
    )
    return result, dream_state


__all__ = [
    "DreamState",
    "DreamResult",
    "MergeVerdict",
    "load_dream_state",
    "save_dream_state",
    "should_run_dream",
    "prune_stale",
    "parse_merge_verdict",
    "dream_merge",
    "dream_purge_tips",
    "run_dream_pass",
]
