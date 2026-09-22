# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Auto-dream: TTSE bank hygiene (TTL prune, cluster merge, LLM TIP quality).

Runs offline from the user turn: prune stale rules, merge near-duplicates
within each track (grouped by existing category), then LLM-batch
form/over-generic purge for unchecked tips.

Dream merge is dual-path:
* With an embedding provider: cosine ``soft_cluster`` then per-cluster LLM merge
  (cluster size capped by ``dream_merge_max_rules``).
* Without embedding: LLM Phase1 partition + Phase2 category merge,
  with incremental cluster persistence under ``dream/dream-clusters.json``.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

from .config import TTSEConfig
from .prompts import (
    DREAM_CATEGORY_MERGE_SYSTEM,
    DREAM_CLUSTER_SYSTEM,
    DREAM_MERGE_SYSTEM,
    DREAM_PURGE_SYSTEM,
    dream_category_merge_prompt,
    dream_cluster_prompt,
    dream_merge_prompt,
    dream_purge_prompt,
)
from .stores import TTSERecordStore, format_ts, parse_ts
from .tip_parse import parse_tip


SECONDS_PER_DAY = 86400.0
_CLUSTER_STORE_VERSION = 1
_DECISION_LINE_RE = re.compile(
    r"group\s*=\s*(?P<group>\d+)\s*\|"
    r"\s*ids\s*=\s*(?P<ids>[^|]*?)\s*\|"
    r"\s*VERDICT\s*:\s*(?P<verdict>\w+)\s*\|"
    r"\s*CANONICAL\s*:\s*(?P<canonical>.*?)\s*\|"
    r"(?:\s*MERGE_INDICES\s*:\s*(?P<merge>[^|]*)\s*\|)?"
    r"\s*KEEP_INDICES\s*:\s*(?P<keep>.*)\s*$",
    re.IGNORECASE,
)
_ATTACH_LINE_RE = re.compile(
    r"cluster\s*=\s*(?P<cid>\S+)\s*\|\s*ids\s*=\s*(?P<ids>.+)$",
    re.IGNORECASE,
)


@dataclass
class DreamState:
    """Persisted Auto-dream counters / timestamps."""

    last_dream_at: float = 0.0
    last_pruned: int = 0
    last_merged_clusters: int = 0
    last_purged_tips: int = 0
    # Non-follow-up task iterations since the last scheduled dream. Lives on
    # disk so a remounted TTSERail does not restart the interval at 0.
    non_followup_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dream_at": (
                format_ts(self.last_dream_at) if self.last_dream_at > 0 else 0
            ),
            "last_pruned": self.last_pruned,
            "last_merged_clusters": self.last_merged_clusters,
            "last_purged_tips": self.last_purged_tips,
            "non_followup_count": self.non_followup_count,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DreamState":
        data = data or {}
        raw_last = data.get("last_dream_at")
        parsed = parse_ts(raw_last)
        if parsed is None:
            # Legacy empty / missing / unparsable → never dreamed.
            last_dream_at = 0.0
        else:
            last_dream_at = float(parsed)
        return cls(
            last_dream_at=last_dream_at,
            last_pruned=int(data.get("last_pruned") or 0),
            last_merged_clusters=int(data.get("last_merged_clusters") or 0),
            last_purged_tips=int(data.get("last_purged_tips") or 0),
            non_followup_count=int(data.get("non_followup_count") or 0),
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
    added_items: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class MergeVerdict:
    verdict: str  # MERGE | KEEP_DISTINCT | REWRITE
    canonical: str = ""
    # Local 0-based indices within the cluster being decided.
    # MERGE/REWRITE: members folded into CANONICAL (empty ⇒ all members).
    merge_indices: List[int] = field(default_factory=list)
    # KEEP_DISTINCT: audit-only. MERGE/REWRITE: members left unchanged
    # (empty with non-empty merge_indices ⇒ complement of merge_indices).
    keep_indices: List[int] = field(default_factory=list)
    reason: str = ""
    thinking: str = ""


@dataclass
class ClusterPartition:
    """Phase 1 result for one category×track."""

    thinking: str
    reason: str
    groups: List[List[int]] = field(default_factory=list)
    # New-rule indices attached to an existing persisted cluster id.
    attaches: List[Tuple[str, List[int]]] = field(default_factory=list)


@dataclass
class CategoryMergeDecision:
    """Phase 2 decision for one proposed group."""

    group_indices: List[int]
    verdict: str
    canonical: str = ""
    # Global indices into the Phase2 rule list (same space as group_indices).
    merge_indices: List[int] = field(default_factory=list)
    keep_indices: List[int] = field(default_factory=list)
    reason: str = ""
    group_id: int = -1


@dataclass
class CategoryMergeResult:
    thinking: str
    reason: str
    decisions: List[CategoryMergeDecision] = field(default_factory=list)


@dataclass
class PersistedCluster:
    """One persisted LLM-path cluster (members + description)."""

    id: str
    track: str
    category: str
    description: str = ""
    member_texts: List[str] = field(default_factory=list)
    last_verdict: str = ""
    dirty: bool = False
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "track": self.track,
            "category": self.category,
            "description": self.description,
            "member_texts": list(self.member_texts),
            "last_verdict": self.last_verdict,
            "dirty": bool(self.dirty),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["PersistedCluster"]:
        if not isinstance(data, dict):
            return None
        cid = str(data.get("id") or "").strip()
        track = str(data.get("track") or "").strip().lower()
        if not cid or track not in ("fact", "tip"):
            return None
        members = [str(t) for t in (data.get("member_texts") or []) if str(t).strip()]
        return cls(
            id=cid,
            track=track,
            category=str(data.get("category") or "other"),
            description=str(data.get("description") or ""),
            member_texts=members,
            last_verdict=str(data.get("last_verdict") or ""),
            dirty=bool(data.get("dirty")),
            updated_at=str(data.get("updated_at") or ""),
        )


_MERGE_FIELD_HEADERS = (
    "THINKING",
    "REASON",
    "VERDICT",
    "CANONICAL",
    "MERGE_INDICES",
    "KEEP_INDICES",
    "GROUPS",
    "ATTACH",
    "DECISIONS",
)
_THINKING_LOG_MAX = 300


def _truncate_thinking(text: str, limit: int = _THINKING_LOG_MAX) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _is_merge_field_header(line: str) -> bool:
    up = line.strip().upper()
    for header in _MERGE_FIELD_HEADERS:
        if up == header or up.startswith(header + ":") or up.startswith(header + " "):
            return True
    return False


def _new_cluster_id() -> str:
    return "c_" + uuid.uuid4().hex[:12]


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


def load_dream_clusters(path: str) -> List[PersistedCluster]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("[TTSERail] dream-clusters load failed at %s: %s", path, exc)
        return []
    if not isinstance(raw, dict):
        return []
    out: List[PersistedCluster] = []
    for item in raw.get("clusters") or []:
        cluster = PersistedCluster.from_dict(item)
        if cluster is not None and cluster.member_texts:
            out.append(cluster)
    return out


def save_dream_clusters(path: str, clusters: Sequence[PersistedCluster]) -> None:
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {
        "version": _CLUSTER_STORE_VERSION,
        "clusters": [c.to_dict() for c in clusters if c.member_texts],
    }
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("[TTSERail] dream-clusters save failed at %s: %s", path, exc)


def reconcile_dream_clusters(
    clusters: Sequence[PersistedCluster],
    bank_texts: Set[str],
) -> List[PersistedCluster]:
    """Drop members missing from the bank; drop empty clusters."""
    out: List[PersistedCluster] = []
    for cluster in clusters:
        members = [t for t in cluster.member_texts if t in bank_texts]
        if not members:
            continue
        if len(members) != len(cluster.member_texts):
            cluster = PersistedCluster(
                id=cluster.id,
                track=cluster.track,
                category=cluster.category,
                description=cluster.description,
                member_texts=members,
                last_verdict=cluster.last_verdict,
                dirty=True if len(members) >= 2 else cluster.dirty,
                updated_at=cluster.updated_at,
            )
        out.append(cluster)
    return out


def bump_dream_session_count(path: str, interval: int) -> Tuple[int, bool]:
    """Persist +1 non-follow-up iteration toward ``dream_interval``.

    Returns ``(count_after_increment, interval_reached)``. When the interval
    is reached the stored count is reset to 0 so the next rail instance does
    not immediately fire again.
    """
    interval = max(1, int(interval))
    state = load_dream_state(path)
    state.non_followup_count = max(0, int(state.non_followup_count or 0)) + 1
    count = state.non_followup_count
    reached = count >= interval
    if reached:
        state.non_followup_count = 0
    save_dream_state(path, state)
    return count, reached


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
    parsed = parse_ts(value)
    return parsed if parsed is not None else 0.0


async def prune_stale(
    store: TTSERecordStore,
    config: TTSEConfig,
    *,
    now: Optional[float] = None,
) -> Tuple[int, int]:
    """Delete rules not injected within ``dream_ttl_days``."""
    if not config.dream_prune_enabled:
        return 0, 0
    ts = now if now is not None else time.time()
    ttl = float(config.dream_ttl_days) * SECONDS_PER_DAY
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
            logger.info(
                "[TTSERail] dream prune before_delete rtype=%s text=%s",
                rtype,
                text,
            )
            n = await store.delete_record(text, rtype, save=False)
            removed += n
        return removed

    pruned_facts = await _prune_track("fact", store.facts)
    pruned_tips = await _prune_track("tip", store.tips)
    if pruned_facts or pruned_tips:
        logger.info(
            "[TTSERail] dream prune done ttl_days=%s pruned_facts=%s pruned_tips=%s",
            config.dream_ttl_days,
            pruned_facts,
            pruned_tips,
        )
    else:
        logger.debug(
            "[TTSERail] dream prune idle ttl_days=%s",
            config.dream_ttl_days,
        )
    return pruned_facts, pruned_tips


def _parse_thinking_reason(text: str) -> Tuple[str, str, List[str]]:
    """Return (thinking, reason, remaining_lines_after_headers).

    Both THINKING and REASON accept either inline (``HEADER: body``) or
    block form (``HEADER:`` then following lines until the next field header).
    """
    thinking_lines: List[str] = []
    reason_lines: List[str] = []
    in_thinking = False
    in_reason = False
    remaining: List[str] = []
    for line in str(text).splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("THINKING"):
            in_thinking = True
            in_reason = False
            if ":" in s:
                inline = s.split(":", 1)[-1].strip()
                if inline:
                    thinking_lines.append(inline)
            continue
        if in_thinking:
            if _is_merge_field_header(s) and not up.startswith("THINKING"):
                in_thinking = False
            else:
                thinking_lines.append(line.rstrip())
                continue
        if up.startswith("REASON"):
            in_reason = True
            in_thinking = False
            if ":" in s:
                inline = s.split(":", 1)[-1].strip()
                if inline:
                    reason_lines.append(inline)
            continue
        if in_reason:
            if _is_merge_field_header(s) and not up.startswith("REASON"):
                in_reason = False
            else:
                reason_lines.append(line.rstrip())
                continue
        remaining.append(line)
    return (
        "\n".join(thinking_lines).strip(),
        "\n".join(reason_lines).strip(),
        remaining,
    )


def _parse_index_list(payload: str, *, n: int) -> List[int]:
    out: List[int] = []
    seen: Set[int] = set()
    for tok in (payload or "").replace(",", " ").split():
        if not tok.isdigit():
            continue
        idx = int(tok)
        if 0 <= idx < n and idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out


def parse_merge_verdict(text: str, cluster_size: int) -> Optional[MergeVerdict]:
    """Parse THINKING/REASON/VERDICT/CANONICAL/MERGE_INDICES/KEEP_INDICES.

    THINKING and REASON must be non-empty; otherwise returns ``None``.
    """
    if not text:
        return None
    verdict = ""
    canonical = ""
    merge_indices: List[int] = []
    keep_indices: List[int] = []
    reason = ""
    thinking_lines: List[str] = []
    in_thinking = False
    for line in str(text).splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("THINKING"):
            in_thinking = True
            # Inline body on the same line: "THINKING: ..."
            if ":" in s:
                inline = s.split(":", 1)[-1].strip()
                if inline:
                    thinking_lines.append(inline)
            continue
        if in_thinking:
            if _is_merge_field_header(s) and not up.startswith("THINKING"):
                in_thinking = False
            else:
                thinking_lines.append(line.rstrip())
                continue
        if up.startswith("VERDICT"):
            payload = s.split(":", 1)[-1].strip() if ":" in s else s
            token = payload.split()[0].upper() if payload else ""
            if token in ("MERGE", "KEEP_DISTINCT", "REWRITE"):
                verdict = token
        elif up.startswith("CANONICAL"):
            canonical = s.split(":", 1)[-1].strip() if ":" in s else ""
        elif up.startswith("MERGE_INDICES"):
            payload = s.split(":", 1)[-1].strip() if ":" in s else ""
            for tok in payload.replace(",", " ").split():
                if tok.isdigit():
                    idx = int(tok)
                    if 0 <= idx < cluster_size:
                        merge_indices.append(idx)
        elif up.startswith("KEEP_INDICES"):
            payload = s.split(":", 1)[-1].strip() if ":" in s else ""
            for tok in payload.replace(",", " ").split():
                if tok.isdigit():
                    idx = int(tok)
                    if 0 <= idx < cluster_size:
                        keep_indices.append(idx)
        elif up.startswith("REASON"):
            reason = s.split(":", 1)[-1].strip() if ":" in s else s
    thinking = "\n".join(thinking_lines).strip()
    if not verdict or not thinking or not reason:
        return None
    return MergeVerdict(
        verdict=verdict,
        canonical=canonical,
        merge_indices=merge_indices,
        keep_indices=keep_indices,
        reason=reason,
        thinking=thinking,
    )


def _dedupe_indices(indices: Sequence[int]) -> List[int]:
    seen: Set[int] = set()
    out: List[int] = []
    for i in indices:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def normalize_merge_subset(
    cluster_size: int,
    verdict: str,
    merge_indices: Sequence[int],
    keep_indices: Sequence[int],
) -> Tuple[List[int], List[int]]:
    """Resolve MERGE/REWRITE subset vs KEEP_DISTINCT keep-all.

    Returns ``(merge_local, keep_local)``. For KEEP_DISTINCT, merge is empty and
    keep is all members (KEEP_INDICES is audit-only). For MERGE/REWRITE:
    empty ``merge_indices`` means full-cluster merge; otherwise keep defaults to
    the complement of merge. If fewer than 2 merge members remain, degrade to
    keep-all (no apply).
    """
    all_idx = list(range(cluster_size))
    if verdict == "KEEP_DISTINCT":
        return [], all_idx

    merge = _dedupe_indices([i for i in merge_indices if 0 <= i < cluster_size])
    keep = _dedupe_indices([i for i in keep_indices if 0 <= i < cluster_size])
    if not merge:
        # Backward compatible: full-cluster merge.
        return all_idx, []

    merge_set = set(merge)
    keep = [i for i in keep if i not in merge_set]
    if not keep_indices:
        keep = [i for i in all_idx if i not in merge_set]
    else:
        # Unlisted members are kept (safer than silent drop).
        listed = merge_set | set(keep)
        for i in all_idx:
            if i not in listed:
                keep.append(i)
        keep = _dedupe_indices(keep)

    if len(merge) < 2:
        return [], all_idx
    return merge, keep


def parse_cluster_groups(
    text: str,
    *,
    n: int,
    min_size: int,
    known_cluster_ids: Optional[Set[str]] = None,
) -> Optional[ClusterPartition]:
    """Parse Phase 1 THINKING/REASON/GROUPS[/ATTACH] output."""
    if not text:
        return None
    thinking, reason, remaining = _parse_thinking_reason(text)
    if not thinking or not reason:
        return None

    section = ""
    groups_lines: List[str] = []
    attach_lines: List[str] = []
    for line in remaining:
        s = line.strip()
        up = s.upper()
        if up.startswith("GROUPS"):
            section = "groups"
            inline = s.split(":", 1)[-1].strip() if ":" in s else ""
            if inline:
                groups_lines.append(inline)
            continue
        if up.startswith("ATTACH"):
            section = "attach"
            inline = s.split(":", 1)[-1].strip() if ":" in s else ""
            if inline:
                attach_lines.append(inline)
            continue
        if section == "groups":
            if _is_merge_field_header(s) and not up.startswith("GROUPS"):
                section = ""
                if up.startswith("ATTACH"):
                    section = "attach"
                    inline = s.split(":", 1)[-1].strip() if ":" in s else ""
                    if inline:
                        attach_lines.append(inline)
                continue
            groups_lines.append(s)
        elif section == "attach":
            if _is_merge_field_header(s) and not up.startswith("ATTACH"):
                section = ""
                continue
            attach_lines.append(s)

    used: Set[int] = set()
    groups: List[List[int]] = []
    body = "\n".join(groups_lines).strip()
    if body.upper() != "NONE":
        for raw in groups_lines:
            s = raw.strip()
            if not s or s.upper() == "NONE":
                continue
            if s.startswith("-") or s.startswith("*"):
                s = s[1:].strip()
            idxs = _parse_index_list(s, n=n)
            if not idxs:
                continue
            if any(i in used for i in idxs):
                logger.info(
                    "[TTSERail] dream llm_cluster drop overlapping group idxs=%s",
                    idxs,
                )
                continue
            for i in idxs:
                used.add(i)
            if len(idxs) >= min_size:
                groups.append(idxs)

    known = known_cluster_ids or set()
    attaches: List[Tuple[str, List[int]]] = []
    attach_body = "\n".join(attach_lines).strip()
    if attach_body.upper() != "NONE":
        for raw in attach_lines:
            s = raw.strip()
            if not s or s.upper() == "NONE":
                continue
            if s.startswith("-") or s.startswith("*"):
                s = s[1:].strip()
            match = _ATTACH_LINE_RE.search(s)
            if not match:
                continue
            cid = match.group("cid").strip()
            if known and cid not in known:
                logger.info("[TTSERail] dream llm_cluster ignore unknown attach id=%s", cid)
                continue
            idxs = _parse_index_list(match.group("ids"), n=n)
            idxs = [i for i in idxs if i not in used]
            if not idxs:
                continue
            for i in idxs:
                used.add(i)
            attaches.append((cid, idxs))

    groups.sort(key=lambda g: (-len(g), min(g) if g else 0))
    return ClusterPartition(
        thinking=thinking,
        reason=reason,
        groups=groups,
        attaches=attaches,
    )


def parse_category_merge_decisions(
    text: str,
    *,
    clusters: Sequence[Sequence[int]],
    n: int,
) -> Optional[CategoryMergeResult]:
    """Parse Phase 2 THINKING/REASON/DECISIONS for a category."""
    if not text:
        return None
    thinking, reason, remaining = _parse_thinking_reason(text)
    if not thinking or not reason:
        return None

    in_decisions = False
    decision_lines: List[str] = []
    for line in remaining:
        s = line.strip()
        up = s.upper()
        if up.startswith("DECISIONS"):
            in_decisions = True
            inline = s.split(":", 1)[-1].strip() if ":" in s else ""
            if inline:
                decision_lines.append(inline)
            continue
        if in_decisions:
            if _is_merge_field_header(s) and not up.startswith("DECISIONS"):
                break
            if s:
                decision_lines.append(s)

    cluster_sets = [frozenset(c) for c in clusters]
    decisions: List[CategoryMergeDecision] = []
    seen_groups: Set[int] = set()

    for raw in decision_lines:
        s = raw.strip()
        if s.startswith("-") or s.startswith("*"):
            s = s[1:].strip()
        match = _DECISION_LINE_RE.search(s)
        if not match:
            continue
        group_id = int(match.group("group"))
        ids = _parse_index_list(match.group("ids"), n=n)
        verdict = match.group("verdict").strip().upper()
        canonical = (match.group("canonical") or "").strip()
        keep = _parse_index_list(match.group("keep"), n=n)
        merge_raw = match.groupdict().get("merge")
        merge = _parse_index_list(merge_raw or "", n=n) if merge_raw is not None else []
        if verdict not in ("MERGE", "KEEP_DISTINCT", "REWRITE"):
            continue
        id_set = frozenset(ids)
        resolved_gid = group_id
        if not (0 <= group_id < len(cluster_sets)) or id_set != cluster_sets[group_id]:
            resolved_gid = -1
            for gi, cset in enumerate(cluster_sets):
                if id_set == cset:
                    resolved_gid = gi
                    break
            if resolved_gid < 0:
                logger.info(
                    "[TTSERail] dream category_merge drop unmatched decision ids=%s",
                    ids,
                )
                continue
        if resolved_gid in seen_groups:
            continue
        keep = [i for i in keep if i in id_set]
        merge = [i for i in merge if i in id_set]
        if verdict == "KEEP_DISTINCT" and not keep:
            keep = list(clusters[resolved_gid])
        seen_groups.add(resolved_gid)
        decisions.append(
            CategoryMergeDecision(
                group_indices=list(clusters[resolved_gid]),
                verdict=verdict,
                canonical=canonical,
                merge_indices=merge,
                keep_indices=keep,
                reason=reason,
                group_id=resolved_gid,
            )
        )

    if clusters and not decisions:
        return None

    for gi, group in enumerate(clusters):
        if gi in seen_groups:
            continue
        logger.info(
            "[TTSERail] dream category_merge missing decision group=%s -> KEEP_DISTINCT",
            gi,
        )
        decisions.append(
            CategoryMergeDecision(
                group_indices=list(group),
                verdict="KEEP_DISTINCT",
                keep_indices=list(group),
                reason=reason or "missing_decision",
                group_id=gi,
            )
        )

    decisions.sort(key=lambda d: d.group_id if d.group_id >= 0 else 10**9)
    return CategoryMergeResult(thinking=thinking, reason=reason, decisions=decisions)


def _best_count_text(cluster: Sequence[Dict[str, Any]]) -> str:
    return max(cluster, key=lambda r: int(r.get("count", 0))).get("text", "")


def _format_cluster_members(cluster: Sequence[Dict[str, Any]]) -> str:
    """Human-readable cluster members for dream logs (full text, no truncation)."""
    parts: List[str] = []
    for i, record in enumerate(cluster):
        parts.append(f"[{i}] count={int(record.get('count', 0))} text={record.get('text', '')}")
    return " || ".join(parts)


def _format_rules_block(records: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{i}. count={int(r.get('count', 0))} | {r.get('text', '')}" for i, r in enumerate(records)
    )


def _sample_rules(
    records: Sequence[Dict[str, Any]],
    max_rules: int,
) -> Tuple[List[Dict[str, Any]], List[int]]:
    """Return (kept_records, omitted_original_indices) after random shuffle cap."""
    max_rules = max(0, int(max_rules))
    indexed = list(enumerate(records))
    if max_rules <= 0 or len(indexed) <= max_rules:
        return list(records), []
    shuffled = list(indexed)
    random.shuffle(shuffled)
    kept_pairs = shuffled[:max_rules]
    kept_idx = {i for i, _ in kept_pairs}
    # Preserve original relative order for stable LLM indices.
    kept = [r for i, r in indexed if i in kept_idx]
    omitted = [i for i, _ in indexed if i not in kept_idx]
    return kept, omitted


def _cap_cluster_by_count(
    records: Sequence[Dict[str, Any]],
    max_rules: int,
) -> Tuple[List[Dict[str, Any]], List[int]]:
    """Keep up to ``max_rules`` with highest count; preserve original order.

    Used on the embedding soft-cluster merge path so oversized clusters do not
    explode the pairwise similarity table in the LLM prompt. Omitted members
    stay in the bank for a later dream pass.
    """
    max_rules = max(0, int(max_rules))
    indexed = list(enumerate(records))
    if max_rules <= 0 or len(indexed) <= max_rules:
        return list(records), []
    ranked = sorted(
        indexed,
        key=lambda pair: (-int(pair[1].get("count", 0)), pair[0]),
    )
    kept_idx = {i for i, _ in ranked[:max_rules]}
    kept = [r for i, r in indexed if i in kept_idx]
    omitted = [i for i, _ in indexed if i not in kept_idx]
    return kept, omitted


def materialize_clusters(
    group: Sequence[Dict[str, Any]],
    partition: ClusterPartition,
) -> List[List[Dict[str, Any]]]:
    """Map Phase1 index groups to record lists."""
    clusters: List[List[Dict[str, Any]]] = []
    for idxs in partition.groups:
        clusters.append([group[i] for i in idxs if 0 <= i < len(group)])
    return clusters


def _tip_canonical_valid(
    canonical: str,
    *,
    capability_names: Optional[Set[str]],
) -> bool:
    parsed = parse_tip(canonical)
    if parsed is None:
        return False
    if capability_names:
        return parsed[1] in capability_names
    return True


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
    rules_block = _format_rules_block(cluster)
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
) -> Tuple[str, Optional[Tuple[str, str]]]:
    """Apply MERGE/REWRITE/KEEP. Returns (action, new rule or None).

    MERGE/REWRITE may fold a subset (``merge_indices``) into one CANONICAL while
    leaving ``keep_indices`` untouched. Empty ``merge_indices`` means full merge.
    """
    if verdict.verdict == "KEEP_DISTINCT":
        return "keep", None

    merge_idxs, keep_idxs = normalize_merge_subset(
        len(cluster),
        verdict.verdict,
        verdict.merge_indices,
        verdict.keep_indices,
    )
    if not merge_idxs:
        logger.info(
            "[TTSERail] dream keep_subset_too_small track=%s size=%s merge=%s keep=%s",
            track,
            len(cluster),
            list(verdict.merge_indices),
            list(verdict.keep_indices),
        )
        return "keep", None

    merge_records = [cluster[i] for i in merge_idxs]
    keep_records = [cluster[i] for i in keep_idxs]

    canonical = (verdict.canonical or "").strip()
    if not canonical:
        canonical = _best_count_text(merge_records)

    if track == "tip" and not _tip_canonical_valid(canonical, capability_names=capability_names):
        logger.info(
            "[TTSERail] dream keep_invalid_tip track=%s canonical=%s",
            track,
            canonical,
        )
        return "keep_invalid_tip", None

    total_count = sum(int(r.get("count", 0)) for r in merge_records)
    category = store.record_category(cluster[0])
    reason = "dream_merge" if verdict.verdict == "MERGE" else "dream_rewrite"
    logger.info(
        "[TTSERail] dream before_%s track=%s category=%s merge_members=%s keep_members=%s "
        "canonical=%s llm_reason=%s thinking=%s",
        verdict.verdict.lower(),
        track,
        category,
        _format_cluster_members(merge_records),
        _format_cluster_members(keep_records) if keep_records else "(none)",
        canonical,
        verdict.reason or "",
        _truncate_thinking(verdict.thinking),
    )
    if verdict.thinking:
        logger.debug(
            "[TTSERail] dream before_%s full_thinking=%s",
            verdict.verdict.lower(),
            verdict.thinking,
        )
    for record in merge_records:
        logger.info(
            "[TTSERail] dream before_delete track=%s action=%s text=%s",
            track,
            reason,
            record.get("text", ""),
        )
        await store.delete_record(record["text"], track, save=False)
    merged_count = max(total_count, 1)
    await store.add_record_direct(track, canonical, count=merged_count, category=category, save=False)
    logger.info(
        "[TTSERail] dream after_%s track=%s category=%s merge_members=%s keep_members=%s "
        "canonical=%s count=%s llm_reason=%s thinking=%s",
        verdict.verdict.lower(),
        track,
        category,
        _format_cluster_members(merge_records),
        _format_cluster_members(keep_records) if keep_records else "(none)",
        canonical,
        merged_count,
        verdict.reason or "",
        _truncate_thinking(verdict.thinking),
    )
    return verdict.verdict.lower(), (canonical, track)


async def _apply_category_merge_result(
    store: TTSERecordStore,
    track: str,
    group_records: Sequence[Dict[str, Any]],
    result: CategoryMergeResult,
    *,
    capability_names: Optional[Set[str]] = None,
) -> Tuple[int, int, List[Tuple[str, str]], List[CategoryMergeDecision]]:
    """Apply Phase2 decisions. Returns (merged, kept, added, tip_retries)."""
    merged = 0
    kept = 0
    added_items: List[Tuple[str, str]] = []
    tip_retries: List[CategoryMergeDecision] = []
    decisions = sorted(result.decisions, key=lambda d: -len(d.group_indices))
    applied_texts: Set[str] = set()

    for decision in decisions:
        cluster = [group_records[i] for i in decision.group_indices if 0 <= i < len(group_records)]
        if len(cluster) < 2:
            continue
        texts = [str(r.get("text") or "") for r in cluster]
        if any(t in applied_texts for t in texts):
            logger.info(
                "[TTSERail] dream category_merge skip overlapping decision ids=%s",
                decision.group_indices,
            )
            continue
        global_to_local = {g: loc for loc, g in enumerate(decision.group_indices)}
        local_merge = [global_to_local[g] for g in decision.merge_indices if g in global_to_local]
        local_keep = [global_to_local[g] for g in decision.keep_indices if g in global_to_local]
        verdict = MergeVerdict(
            verdict=decision.verdict,
            canonical=decision.canonical,
            merge_indices=local_merge,
            keep_indices=local_keep,
            reason=decision.reason or result.reason,
            thinking=result.thinking,
        )
        action, added = await _apply_merge_verdict(
            store,
            track,
            cluster,
            verdict,
            capability_names=capability_names,
        )
        if action == "keep_invalid_tip" and decision.verdict in ("MERGE", "REWRITE"):
            tip_retries.append(decision)
            kept += 1
            continue
        for t in texts:
            applied_texts.add(t)
        if added is not None:
            added_items.append(added)
            applied_texts.add(added[0])
        if action in ("merge", "rewrite"):
            merged += 1
        else:
            kept += 1
    return merged, kept, added_items, tip_retries


async def _llm_cluster_category(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    track: str,
    category_id: str,
    group: Sequence[Dict[str, Any]],
    config: TTSEConfig,
    existing_clusters: Sequence[PersistedCluster],
) -> Optional[ClusterPartition]:
    rules_block = _format_rules_block(group)
    existing_block = "\n".join(
        f"- id={c.id} | description={c.description or '(none)'}" for c in existing_clusters
    )
    cluster_body = dream_cluster_prompt(
        track,
        category_id,
        rules_block,
        min_size=config.dream_cluster_min_size,
        existing_clusters_block=existing_block,
    )
    prompt = f"{DREAM_CLUSTER_SYSTEM}\n\n{cluster_body}"
    try:
        out = await invoke_text_with_retry(llm, model, prompt, policy=policy, temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTSERail] dream llm_cluster failed: %s", exc)
        return None
    return parse_cluster_groups(
        out,
        n=len(group),
        min_size=config.dream_cluster_min_size,
        known_cluster_ids={c.id for c in existing_clusters},
    )


async def _llm_category_merge(
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    track: str,
    category_id: str,
    group: Sequence[Dict[str, Any]],
    cluster_index_groups: Sequence[Sequence[int]],
    capabilities: str,
    invalid_tip_hint: bool = False,
) -> Optional[CategoryMergeResult]:
    rules_block = _format_rules_block(group)
    clusters_block = "\n".join(
        f"{i}: {','.join(str(x) for x in idxs)}" for i, idxs in enumerate(cluster_index_groups)
    )
    merge_body = dream_category_merge_prompt(
        track,
        category_id,
        rules_block,
        clusters_block,
        capabilities=capabilities,
        invalid_tip_hint=invalid_tip_hint,
    )
    prompt = f"{DREAM_CATEGORY_MERGE_SYSTEM}\n\n{merge_body}"
    try:
        out = await invoke_text_with_retry(llm, model, prompt, policy=policy, temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTSERail] dream category_merge LLM failed: %s", exc)
        return None
    return parse_category_merge_decisions(out, clusters=cluster_index_groups, n=len(group))


async def _dream_merge_embedding(
    store: TTSERecordStore,
    track: str,
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    config: TTSEConfig,
    capabilities: str = "",
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """Cosine soft-cluster + per-cluster LLM merge (embedding path)."""
    records = store.facts if track == "fact" else store.tips
    if len(records) < config.dream_cluster_min_size:
        return 0, 0, []

    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_category[store.record_category(record)].append(record)

    clusters: List[List[Dict[str, Any]]] = []
    min_size = config.dream_cluster_min_size
    for cid, group in by_category.items():
        if len(group) < min_size:
            continue
        cat_clusters = await store.soft_cluster(
            group,
            soft_lo=config.dream_soft_lo,
            min_size=min_size,
        )
        logger.info(
            "[TTSERail] dream merge category bucket track=%s category=%s rules=%s "
            "clusters=%s similarity=cosine threshold=%.3f",
            track,
            cid,
            len(group),
            len(cat_clusters),
            float(config.dream_soft_lo),
        )
        clusters.extend(cat_clusters)
    clusters.sort(key=lambda c: -len(c))

    if not clusters:
        logger.info(
            "[TTSERail] dream merge no clusters track=%s rules=%s categories=%s "
            "similarity=cosine threshold=%.3f",
            track,
            len(records),
            len(by_category),
            float(config.dream_soft_lo),
        )
        return 0, 0, []

    merged = 0
    kept = 0
    added_items: List[Tuple[str, str]] = []
    logger.info(
        "[TTSERail] dream merge start track=%s rules=%s categories=%s clusters=%s "
        "similarity=cosine threshold=%.3f",
        track,
        len(records),
        len(by_category),
        len(clusters),
        float(config.dream_soft_lo),
    )
    max_rules = max(1, int(config.dream_merge_max_rules))
    for idx, cluster in enumerate(clusters):
        logger.info(
            "[TTSERail] dream cluster track=%s idx=%s/%s size=%s members=%s",
            track,
            idx + 1,
            len(clusters),
            len(cluster),
            _format_cluster_members(cluster),
        )
    for cluster in clusters:
        work, omitted = _cap_cluster_by_count(cluster, max_rules)
        if omitted:
            logger.info(
                "[TTSERail] dream merge truncate track=%s size=%s kept=%s omitted=%s",
                track,
                len(cluster),
                len(work),
                omitted,
            )
        if len(work) < config.dream_cluster_min_size:
            continue
        sims = await store.pairwise_sims(work)
        verdict = await _llm_merge_cluster(
            llm=llm,
            model=model,
            policy=policy,
            track=track,
            cluster=work,
            sims=sims,
            capabilities=capabilities,
        )
        if verdict is None:
            logger.info(
                "[TTSERail] dream merge cluster skipped (LLM/parse failure) track=%s size=%s",
                track,
                len(work),
            )
            continue

        action, added = await _apply_merge_verdict(store, track, work, verdict)
        if added is not None:
            added_items.append(added)
        if action in ("merge", "rewrite"):
            merged += 1
            logger.info(
                "[TTSERail] dream %s track=%s size=%s reason=%s thinking=%s",
                action,
                track,
                len(work),
                verdict.reason or "",
                _truncate_thinking(verdict.thinking),
            )
        else:
            kept += 1
            logger.info(
                "[TTSERail] dream keep_distinct track=%s size=%s reason=%s thinking=%s",
                track,
                len(work),
                verdict.reason or "",
                _truncate_thinking(verdict.thinking),
            )
    return merged, kept, added_items


def _bank_text_set(store: TTSERecordStore, track: str) -> Set[str]:
    records = store.facts if track == "fact" else store.tips
    return {str(r.get("text") or "") for r in records if r.get("text")}


def _text_to_record(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(r.get("text") or ""): r for r in records if r.get("text")}


async def _dream_merge_llm(
    store: TTSERecordStore,
    track: str,
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    config: TTSEConfig,
    capabilities: str = "",
    capability_names: Optional[Set[str]] = None,
    cluster_store: Optional[List[PersistedCluster]] = None,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """LLM Phase1/Phase2 merge with incremental cluster persistence."""
    records = store.facts if track == "fact" else store.tips
    if len(records) < config.dream_cluster_min_size:
        return 0, 0, []

    path = config.resolved_dream_clusters_path()
    if cluster_store is None:
        bank_texts = _bank_text_set(store, "fact") | _bank_text_set(store, "tip")
        clusters_all = reconcile_dream_clusters(load_dream_clusters(path), bank_texts)
    else:
        clusters_all = cluster_store

    track_clusters = [c for c in clusters_all if c.track == track]
    clustered_texts: Set[str] = set()
    for c in track_clusters:
        clustered_texts.update(c.member_texts)

    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_category[store.record_category(record)].append(record)

    merged = 0
    kept = 0
    added_items: List[Tuple[str, str]] = []
    now_ts = format_ts(time.time())
    max_rules = max(1, int(config.dream_category_max_rules))

    for cid, group in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        if len(group) < config.dream_cluster_min_size and not any(
            c.category == cid and c.dirty and len(c.member_texts) >= config.dream_cluster_min_size
            for c in track_clusters
        ):
            if len(group) < config.dream_cluster_min_size:
                continue

        existing = [c for c in track_clusters if c.category == cid]
        unclustered = [r for r in group if str(r.get("text") or "") not in clustered_texts]
        work, omitted = _sample_rules(unclustered, max_rules)
        if omitted:
            logger.info(
                "[TTSERail] dream llm_cluster truncate track=%s category=%s kept=%s omitted=%s",
                track,
                cid,
                len(work),
                omitted,
            )

        partition: Optional[ClusterPartition] = None
        if len(work) >= config.dream_cluster_min_size or (work and existing):
            # Phase1 when there is something new to place (even single attach candidates).
            if work and (len(work) >= config.dream_cluster_min_size or existing):
                partition = await _llm_cluster_category(
                    llm=llm,
                    model=model,
                    policy=policy,
                    track=track,
                    category_id=cid,
                    group=work,
                    config=config,
                    existing_clusters=existing,
                )
                if partition is None:
                    logger.info(
                        "[TTSERail] dream llm_cluster failed; skip category track=%s category=%s",
                        track,
                        cid,
                    )
                    continue
                logger.info(
                    "[TTSERail] dream llm_cluster track=%s category=%s rules=%s groups=%s "
                    "attaches=%s similarity=llm",
                    track,
                    cid,
                    len(work),
                    len(partition.groups),
                    len(partition.attaches),
                )
                # Persist new groups as dirty clusters.
                for idxs in partition.groups:
                    members = [str(work[i].get("text") or "") for i in idxs]
                    members = [t for t in members if t]
                    if len(members) < config.dream_cluster_min_size:
                        continue
                    new_c = PersistedCluster(
                        id=_new_cluster_id(),
                        track=track,
                        category=cid,
                        description=partition.reason,
                        member_texts=members,
                        last_verdict="",
                        dirty=True,
                        updated_at=now_ts,
                    )
                    track_clusters.append(new_c)
                    clusters_all.append(new_c)
                    clustered_texts.update(members)
                by_id = {c.id: c for c in track_clusters}
                for attach_id, idxs in partition.attaches:
                    target = by_id.get(attach_id)
                    if target is None:
                        continue
                    added = [str(work[i].get("text") or "") for i in idxs]
                    added = [t for t in added if t and t not in target.member_texts]
                    if not added:
                        continue
                    target.member_texts.extend(added)
                    target.dirty = True
                    target.description = partition.reason or target.description
                    target.updated_at = now_ts
                    clustered_texts.update(added)

        # Phase2 for dirty clusters in this category.
        dirty: List[PersistedCluster] = []
        for c in track_clusters:
            if c.category != cid or not c.dirty:
                continue
            if len(c.member_texts) < config.dream_cluster_min_size:
                continue
            dirty.append(c)
        if not dirty:
            continue

        text_map = _text_to_record(group)
        # Build a Phase2 worklist: union of dirty members present in bank, capped.
        dirty_records: List[Dict[str, Any]] = []
        seen_text: Set[str] = set()
        for c in sorted(dirty, key=lambda x: -len(x.member_texts)):
            for t in c.member_texts:
                if t in seen_text:
                    continue
                rec = text_map.get(t)
                if rec is None:
                    continue
                seen_text.add(t)
                dirty_records.append(rec)
        phase2_records, omitted2 = _sample_rules(dirty_records, max_rules)
        if omitted2:
            logger.info(
                "[TTSERail] dream category_merge truncate track=%s category=%s kept=%s omitted=%s",
                track,
                cid,
                len(phase2_records),
                omitted2,
            )
        text_to_idx = {str(r.get("text") or ""): i for i, r in enumerate(phase2_records)}
        cluster_index_groups: List[List[int]] = []
        dirty_for_prompt: List[PersistedCluster] = []
        for c in dirty:
            idxs = [text_to_idx[t] for t in c.member_texts if t in text_to_idx]
            if len(idxs) < config.dream_cluster_min_size:
                continue
            cluster_index_groups.append(idxs)
            dirty_for_prompt.append(c)
        if not cluster_index_groups:
            for c in dirty:
                c.dirty = False
            continue

        logger.info(
            "[TTSERail] dream category_merge start track=%s category=%s groups=%s",
            track,
            cid,
            len(cluster_index_groups),
        )
        merge_out = await _llm_category_merge(
            llm=llm,
            model=model,
            policy=policy,
            track=track,
            category_id=cid,
            group=phase2_records,
            cluster_index_groups=cluster_index_groups,
            capabilities=capabilities,
        )
        if merge_out is None:
            logger.info(
                "[TTSERail] dream category merge failed; skip apply track=%s category=%s",
                track,
                cid,
            )
            continue

        m, k, items, tip_retries = await _apply_category_merge_result(
            store,
            track,
            phase2_records,
            merge_out,
            capability_names=capability_names,
        )
        merged += m
        kept += k
        added_items.extend(items)

        # Refresh persisted clusters after apply.
        bank_now = _bank_text_set(store, track)
        decision_by_group = {d.group_id: d for d in merge_out.decisions}
        dirty_ids = {c.id for c in dirty_for_prompt}
        rebuilt: List[PersistedCluster] = []
        for gi, c in enumerate(dirty_for_prompt):
            decision = decision_by_group.get(gi)
            if decision is not None and decision.verdict in ("MERGE", "REWRITE"):
                canonical = (decision.canonical or "").strip()
                if not canonical:
                    for text, rtype in items:
                        if rtype == track and text in bank_now:
                            canonical = text
                            break
                members: List[str] = []
                if canonical and canonical in bank_now:
                    members.append(canonical)
                # KEEP_INDICES / complement survivors stay in the persisted cluster.
                _, keep_local = normalize_merge_subset(
                    len(decision.group_indices),
                    decision.verdict,
                    [
                        decision.group_indices.index(g)
                        for g in decision.merge_indices
                        if g in decision.group_indices
                    ],
                    [
                        decision.group_indices.index(g)
                        for g in decision.keep_indices
                        if g in decision.group_indices
                    ],
                )
                for loc in keep_local:
                    gidx = decision.group_indices[loc]
                    if 0 <= gidx < len(phase2_records):
                        t = str(phase2_records[gidx].get("text") or "")
                        if t and t in bank_now and t not in members:
                            members.append(t)
                if not members:
                    # Merge did not apply (e.g. invalid tip); keep surviving originals.
                    members = [t for t in c.member_texts if t in bank_now]
                    if not members:
                        continue
                    c.member_texts = members
                    c.description = merge_out.reason or c.description
                    c.last_verdict = decision.verdict
                    c.dirty = False
                    c.updated_at = now_ts
                    rebuilt.append(c)
                    continue
                rebuilt.append(
                    PersistedCluster(
                        id=_new_cluster_id(),
                        track=track,
                        category=cid,
                        description=merge_out.reason or c.description,
                        member_texts=members,
                        last_verdict=decision.verdict,
                        dirty=False,
                        updated_at=now_ts,
                    )
                )
                continue
            members = [t for t in c.member_texts if t in bank_now]
            if not members:
                continue
            c.member_texts = members
            c.description = merge_out.reason or c.description
            c.last_verdict = (
                decision.verdict if decision is not None else (c.last_verdict or "KEEP_DISTINCT")
            )
            c.dirty = False
            c.updated_at = now_ts
            rebuilt.append(c)

        kept_others = [
            c
            for c in clusters_all
            if c.id not in dirty_ids
        ]
        # Drop members deleted elsewhere for non-dirty clusters.
        refreshed_others: List[PersistedCluster] = []
        for c in kept_others:
            if c.track != track:
                refreshed_others.append(c)
                continue
            members = [t for t in c.member_texts if t in bank_now]
            if not members:
                continue
            if len(members) != len(c.member_texts):
                c.member_texts = members
            refreshed_others.append(c)
        clusters_all[:] = refreshed_others + rebuilt
        track_clusters = [c for c in clusters_all if c.track == track]
        clustered_texts = set()
        for c in track_clusters:
            clustered_texts.update(c.member_texts)

        if tip_retries:
            retry_groups = [d.group_indices for d in tip_retries]
            # Remap to current phase2 indices still valid.
            valid_retry: List[List[int]] = []
            for g in retry_groups:
                idxs = [i for i in g if 0 <= i < len(phase2_records)]
                # Filter to texts still in bank (invalid tip left members untouched).
                idxs = [
                    i
                    for i in idxs
                    if str(phase2_records[i].get("text") or "") in _bank_text_set(store, track)
                ]
                if len(idxs) >= config.dream_cluster_min_size:
                    valid_retry.append(idxs)
            if valid_retry:
                retry_out = await _llm_category_merge(
                    llm=llm,
                    model=model,
                    policy=policy,
                    track=track,
                    category_id=cid,
                    group=phase2_records,
                    cluster_index_groups=valid_retry,
                    capabilities=capabilities,
                    invalid_tip_hint=True,
                )
                if retry_out is not None:
                    m2, k2, items2, _ = await _apply_category_merge_result(
                        store,
                        track,
                        phase2_records,
                        retry_out,
                        capability_names=capability_names,
                    )
                    # tip_retries were counted as kept earlier; adjust if merge succeeded.
                    merged += m2
                    kept = max(0, kept - m2) + k2
                    added_items.extend(items2)

        logger.info(
            "[TTSERail] dream category_merge done track=%s category=%s merged=%s kept=%s",
            track,
            cid,
            m,
            k,
        )

    if cluster_store is None:
        save_dream_clusters(path, clusters_all)
    return merged, kept, added_items


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
    cluster_store: Optional[List[PersistedCluster]] = None,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """Category-bucket then cluster + LLM merge one track.

    With embedding: cosine soft_cluster + per-cluster merge
    (capped by ``dream_merge_max_rules``, highest-count kept).
    Without: LLM Phase1/Phase2 when ``dream_llm_cluster_enabled``.

    Returns (merged, kept, new rules). Clusters never cross category boundaries.
    """
    if store.has_embedding_provider():
        return await _dream_merge_embedding(
            store,
            track,
            llm=llm,
            model=model,
            policy=policy,
            config=config,
            capabilities=capabilities,
        )
    if not config.dream_llm_cluster_enabled:
        logger.info(
            "[TTSERail] dream merge skipped: no embedding and dream_llm_cluster_enabled=False"
        )
        return 0, 0, []
    return await _dream_merge_llm(
        store,
        track,
        llm=llm,
        model=model,
        policy=policy,
        config=config,
        capabilities=capabilities,
        capability_names=capability_names,
        cluster_store=cluster_store,
    )


_PURGE_REASONS = frozenset(
    {
        "tip_malformed",
        "tip_fact_shaped",
        "tip_too_generic_condition",
        "tip_too_generic_action",
        "ok",
    }
)


@dataclass
class PurgeVerdict:
    index: int
    verdict: str  # KEEP | PURGE
    reason: str = ""


def parse_purge_verdicts(text: str, batch_size: int) -> List[PurgeVerdict]:
    """Parse ``INDEX: i | VERDICT: KEEP|PURGE | REASON: ...`` lines from LLM output."""
    out: List[PurgeVerdict] = []
    if not text:
        return out
    for line in str(text).splitlines():
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        if "INDEX" not in up or "VERDICT" not in up:
            continue
        # Tolerate "INDEX: 0 | VERDICT: KEEP | REASON: ok" and minor spacing variants.
        parts = [p.strip() for p in s.replace("|", " | ").split("|")]
        idx_val: Optional[int] = None
        verdict = ""
        reason = ""
        for part in parts:
            pu = part.upper()
            if pu.startswith("INDEX"):
                payload = part.split(":", 1)[-1].strip() if ":" in part else ""
                tok = payload.split()[0] if payload else ""
                if tok.isdigit():
                    idx_val = int(tok)
            elif pu.startswith("VERDICT"):
                payload = part.split(":", 1)[-1].strip() if ":" in part else ""
                token = payload.split()[0].upper() if payload else ""
                if token in ("KEEP", "PURGE"):
                    verdict = token
            elif pu.startswith("REASON"):
                reason = part.split(":", 1)[-1].strip() if ":" in part else ""
        if idx_val is None or not verdict:
            continue
        if not (0 <= idx_val < batch_size):
            continue
        if reason.lower() not in _PURGE_REASONS and verdict == "KEEP":
            reason = reason or "ok"
        out.append(PurgeVerdict(index=idx_val, verdict=verdict, reason=reason or "ok"))
    return out


def _pack_unchecked_tips(
    records: Sequence[Dict[str, Any]],
    *,
    batch_size: int,
    max_chars: int,
) -> List[Dict[str, Any]]:
    """Select unchecked tips under count + char budget (one oversized tip alone)."""
    batch_size = max(0, int(batch_size))
    max_chars = max(0, int(max_chars))
    if batch_size <= 0 or max_chars <= 0:
        return []
    packed: List[Dict[str, Any]] = []
    used = 0
    for record in records:
        if record.get("form_checked") is True:
            continue
        text = str(record.get("text") or "")
        if not text:
            continue
        n = len(text)
        if not packed and n > max_chars:
            packed.append(record)
            break
        if len(packed) >= batch_size:
            break
        if used + n > max_chars:
            break
        packed.append(record)
        used += n
    return packed


async def dream_purge_tips(
    store: TTSERecordStore,
    *,
    llm: Model,
    model: str,
    policy: LLMInvokePolicy,
    config: TTSEConfig,
) -> int:
    """LLM form / over-generic purge for tips with ``form_checked`` not true.

    One invoke per dream pass. KEEP sets ``form_checked=True``; PURGE deletes.
    """
    candidates = _pack_unchecked_tips(
        store.tips,
        batch_size=config.dream_purge_batch_size,
        max_chars=config.dream_purge_max_chars,
    )
    if not candidates:
        logger.debug("[TTSERail] dream purge idle: no unchecked tips")
        return 0

    tips_block = "\n".join(f"{i}. {r.get('text', '')}" for i, r in enumerate(candidates))
    prompt = f"{DREAM_PURGE_SYSTEM}\n\n{dream_purge_prompt(tips_block)}"
    logger.info(
        "[TTSERail] dream purge start tips=%s chars=%s",
        len(candidates),
        sum(len(str(r.get("text") or "")) for r in candidates),
    )
    logger.info("[TTSE] dream purge prompt:\n%s", prompt)
    try:
        out = await invoke_text_with_retry(llm, model, prompt, policy=policy, temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTSERail] dream purge LLM failed: %s", exc)
        return 0

    verdicts = parse_purge_verdicts(out, len(candidates))
    by_index = {v.index: v for v in verdicts}
    if len(by_index) < len(candidates):
        logger.warning(
            "[TTSERail] dream purge partial parse got=%s expected=%s",
            len(by_index),
            len(candidates),
        )

    purged = 0
    for i, record in enumerate(candidates):
        verdict = by_index.get(i)
        if verdict is None:
            continue
        text = record.get("text", "")
        if verdict.verdict == "PURGE":
            logger.info(
                "[TTSERail] dream before_purge tip reason=%s text=%s",
                verdict.reason,
                text,
            )
            removed = await store.delete_record(text, "tip", save=False)
            if removed:
                purged += 1
                logger.info("[TTSERail] dream purged tip (%s): %s", verdict.reason, text)
        else:
            record["form_checked"] = True
            logger.info(
                "[TTSERail] dream form_checked tip reason=%s text=%s",
                verdict.reason or "ok",
                text,
            )
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

    logger.info(
        "[TTSERail] dream pass start bank=%s caps=%s state_path=%s last_dream_at=%s",
        store.stats(),
        len(capability_names or set()),
        path,
        format_ts(dream_state.last_dream_at) if dream_state.last_dream_at > 0 else dream_state.last_dream_at,
    )
    result = DreamResult()

    pruned_facts, pruned_tips = await prune_stale(store, config, now=ts)
    result.pruned_facts = pruned_facts
    result.pruned_tips = pruned_tips

    n_rules = len(store.facts) + len(store.tips)
    if n_rules >= config.dream_min_rules:
        use_llm_path = not store.has_embedding_provider() and config.dream_llm_cluster_enabled
        cluster_store: Optional[List[PersistedCluster]] = None
        clusters_path = config.resolved_dream_clusters_path()
        if use_llm_path:
            bank_texts = _bank_text_set(store, "fact") | _bank_text_set(store, "tip")
            cluster_store = reconcile_dream_clusters(load_dream_clusters(clusters_path), bank_texts)

        mf, kf, items_f = await dream_merge(
            store,
            "fact",
            llm=llm,
            model=model,
            policy=config.induce_llm_policy,
            config=config,
            capabilities=capabilities,
            capability_names=capability_names,
            cluster_store=cluster_store,
        )
        mt, kt, items_t = await dream_merge(
            store,
            "tip",
            llm=llm,
            model=model,
            policy=config.induce_llm_policy,
            config=config,
            capabilities=capabilities,
            capability_names=capability_names,
            cluster_store=cluster_store,
        )
        if use_llm_path and cluster_store is not None:
            save_dream_clusters(clusters_path, cluster_store)
        result.merged_clusters = mf + mt
        result.kept_clusters = kf + kt
        result.added_items = items_f + items_t
    else:
        logger.info(
            "[TTSERail] dream merge skipped: rules=%s < min_rules=%s",
            n_rules,
            config.dream_min_rules,
        )

    if config.dream_purge_tips_enabled:
        result.purged_tips = await dream_purge_tips(
            store,
            llm=llm,
            model=model,
            policy=config.induce_llm_policy,
            config=config,
        )

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
    "PurgeVerdict",
    "ClusterPartition",
    "CategoryMergeDecision",
    "CategoryMergeResult",
    "PersistedCluster",
    "load_dream_state",
    "save_dream_state",
    "load_dream_clusters",
    "save_dream_clusters",
    "reconcile_dream_clusters",
    "bump_dream_session_count",
    "should_run_dream",
    "prune_stale",
    "parse_merge_verdict",
    "normalize_merge_subset",
    "parse_cluster_groups",
    "parse_category_merge_decisions",
    "parse_purge_verdicts",
    "materialize_clusters",
    "dream_merge",
    "dream_purge_tips",
    "run_dream_pass",
]
