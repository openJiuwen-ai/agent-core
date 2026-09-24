from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence

from openjiuwen.symphony.retrieval.build.models import CatalogRecord
from openjiuwen.symphony.retrieval.build.workflows.branch_descriptions import (
    build_branch_description as _build_branch_description,
)
from openjiuwen.symphony.retrieval.build.workflows.branch_descriptions import (
    collect_branch_keywords as _collect_branch_keywords,
)
from openjiuwen.symphony.retrieval.build.workflows.branch_descriptions import (
    enrich_branch_descriptions as _enrich_branch_descriptions,
)
from openjiuwen.symphony.retrieval.build.workflows.branch_descriptions import (
    format_catalog_record_snippet as _format_catalog_record_snippet,
)
from openjiuwen.symphony.retrieval.build.workflows.branch_descriptions import (
    sample_catalog_records as _sample_catalog_records,
)
from openjiuwen.symphony.retrieval.build.workflows.branch_descriptions import (
    strip_branch_exposure as _strip_branch_exposure,
)
from openjiuwen.symphony.retrieval.build.workflows.tree_text import join_cid as _join_cid
from openjiuwen.symphony.retrieval.build.workflows.tree_text import parent_cid, slug_term, text_tokens, unique_child_cid

build_branch_description = _build_branch_description
collect_branch_keywords = _collect_branch_keywords
enrich_branch_descriptions = _enrich_branch_descriptions
format_catalog_record_snippet = _format_catalog_record_snippet
sample_catalog_records = _sample_catalog_records
strip_branch_exposure = _strip_branch_exposure
join_cid = _join_cid


@dataclass(frozen=True)
class ParentBranchDecision:
    worker_id: str
    parent_cid: str
    confidence: float
    margin: float
    overlap: int
    reason: str


@dataclass(frozen=True)
class BranchHealthIssue:
    branch_cid: str
    kind: str
    severity: float
    detail: str


@dataclass(frozen=True)
class IncrementalTreePatchReport:
    placement_decisions: tuple[ParentBranchDecision, ...] = ()
    low_confidence_worker_ids: tuple[str, ...] = ()
    health_issues: tuple[BranchHealthIssue, ...] = ()
    rebuilt_branch_cids: tuple[str, ...] = ()


def scan_skill_paths(item_paths: Sequence[Path]) -> Dict[str, dict]:
    from openjiuwen.symphony.retrieval.build.scanners import SkillScanner

    skill_map: Dict[str, dict] = {}
    for path in item_paths:
        scanner = SkillScanner(path.parent if path.name == "skills" else path.parent)
        for item in scanner.to_dict_list():
            if str(item.get("id") or "") == path.name:
                skill_map[path.name] = item
    return skill_map


def merge_added_skills_into_tree_with_report(
    *,
    nodes: Sequence[object],
    added_skills: Dict[str, dict],
    min_confidence: float,
    min_margin: float,
) -> tuple[List[Dict[str, object]], IncrementalTreePatchReport]:
    normalized = [dict(node) for node in nodes if isinstance(node, dict)]
    used_cids = {str(node.get("cid") or "") for node in normalized}
    branch_nodes = [node for node in normalized if str(node.get("type") or "") == "branch"]
    branch_token_cache = {
        str(node.get("cid") or ""): text_tokens(str(node.get("cid") or ""), str(node.get("description") or ""))
        for node in branch_nodes
    }
    decisions: list[ParentBranchDecision] = []
    low_confidence_worker_ids: list[str] = []
    for worker_id, skill in sorted(added_skills.items()):
        decision = score_parent_branch_for_skill(
            worker_id=worker_id,
            skill=skill,
            branch_nodes=branch_nodes,
            branch_token_cache=branch_token_cache,
        )
        decisions.append(decision)
        if decision.confidence < min_confidence or decision.margin < min_margin:
            low_confidence_worker_ids.append(worker_id)
        selected_parent_cid = decision.parent_cid
        cid = unique_child_cid(
            parent=selected_parent_cid,
            segment=slug_term(worker_id, fallback="skill"),
            used=used_cids,
        )
        node = {
            "cid": cid,
            "type": "leaf",
            "description": str(skill.get("description") or "").strip(),
            "worker_id": worker_id,
        }
        normalized.append(node)
        used_cids.add(cid)
    return normalized, IncrementalTreePatchReport(
        placement_decisions=tuple(decisions),
        low_confidence_worker_ids=tuple(low_confidence_worker_ids),
    )


def score_parent_branch_for_skill(
    *,
    worker_id: str,
    skill: dict,
    branch_nodes: Sequence[Dict[str, object]],
    branch_token_cache: Dict[str, set[str]],
) -> ParentBranchDecision:
    if not branch_nodes:
        return ParentBranchDecision(
            worker_id=worker_id, parent_cid="", confidence=0.0, margin=0.0, overlap=0, reason="no_branch"
        )
    skill_tokens = text_tokens(
        worker_id,
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        str(skill.get("content") or ""),
    )
    best_cid = ""
    best_score = -1
    best_confidence = 0.0
    best_overlap = 0
    second_confidence = 0.0
    for node in branch_nodes:
        cid = str(node.get("cid") or "")
        branch_tokens = branch_token_cache.get(cid, set())
        overlap = len(skill_tokens & branch_tokens)
        confidence = _placement_confidence(skill_tokens=skill_tokens, branch_tokens=branch_tokens, overlap=overlap)
        depth_bonus = len(cid.split(".")) if cid else 0
        score = overlap * 100 + depth_bonus
        if score > best_score:
            second_confidence = best_confidence
            best_score = score
            best_cid = cid
            best_confidence = confidence
            best_overlap = overlap
        elif confidence > second_confidence:
            second_confidence = confidence
    margin = max(0.0, best_confidence - second_confidence)
    reason = "token_overlap" if best_overlap > 0 else "depth_fallback"
    return ParentBranchDecision(
        worker_id=worker_id,
        parent_cid=best_cid,
        confidence=best_confidence,
        margin=margin,
        overlap=best_overlap,
        reason=reason,
    )


def _placement_confidence(*, skill_tokens: set[str], branch_tokens: set[str], overlap: int) -> float:
    if not skill_tokens or not branch_tokens or overlap <= 0:
        return 0.0
    skill_ratio = overlap / max(1, len(skill_tokens))
    branch_ratio = overlap / max(1, len(branch_tokens))
    if skill_ratio + branch_ratio <= 0:
        return 0.0
    return min(1.0, 2 * skill_ratio * branch_ratio / (skill_ratio + branch_ratio))


def prune_deleted_skills_from_tree(nodes: Sequence[object], *, removed_worker_ids: set[str]) -> List[Dict[str, object]]:
    normalized = [dict(node) for node in nodes if isinstance(node, dict)]
    normalized = [node for node in normalized if str(node.get("worker_id") or "") not in removed_worker_ids]
    while True:
        child_counts: Dict[str, int] = {}
        for node in normalized:
            cid = str(node.get("cid") or "")
            if not cid:
                continue
            parent = parent_cid(cid)
            if parent:
                child_counts[parent] = child_counts.get(parent, 0) + 1
        pruned = [
            node
            for node in normalized
            if str(node.get("type") or "") != "branch" or child_counts.get(str(node.get("cid") or ""), 0) > 0
        ]
        if len(pruned) == len(normalized):
            return pruned
        normalized = pruned


def apply_incremental_tree_maintenance(
    *,
    nodes: Sequence[object],
    affected_branch_cids: set[str],
    records_by_worker: Dict[str, CatalogRecord],
    max_direct_leaf_children: int,
    branch_imbalance_ratio: float,
    force_rebuild_branch_cids: set[str] | None = None,
    subtree_rebuilder: Callable[[Sequence[object], str, Dict[str, CatalogRecord], int], List[Dict[str, object]] | None]
    | None = None,
) -> tuple[List[Dict[str, object]], IncrementalTreePatchReport]:
    normalized = [dict(node) for node in nodes if isinstance(node, dict)]
    affected_branch_cids = {str(cid) for cid in affected_branch_cids if str(cid)}
    force_rebuild_branch_cids = {str(cid) for cid in (force_rebuild_branch_cids or set()) if str(cid)}
    max_direct_leaf_children = max(2, int(max_direct_leaf_children))
    issues = _analyze_affected_branch_health(
        nodes=normalized,
        branch_cids=affected_branch_cids,
        max_direct_leaf_children=max_direct_leaf_children,
        branch_imbalance_ratio=branch_imbalance_ratio,
    )
    rebuild_candidates = {
        issue.branch_cid for issue in issues if issue.kind in {"too_many_direct_leaves", "imbalanced_children"}
    }
    rebuild_candidates.update(force_rebuild_branch_cids)
    rebuilt: list[str] = []
    for branch_cid in _select_outermost_branches(rebuild_candidates):
        if not branch_cid:
            continue
        subtree_leaf_count = _count_descendant_leaves(normalized, branch_cid)
        if subtree_leaf_count < 2:
            continue
        if subtree_rebuilder is None:
            continue
        rebuilt_nodes = subtree_rebuilder(normalized, branch_cid, records_by_worker, max_direct_leaf_children)
        if rebuilt_nodes is None:
            continue
        if rebuilt_nodes != normalized:
            normalized = rebuilt_nodes
            rebuilt.append(branch_cid)
    final_issues = _analyze_affected_branch_health(
        nodes=normalized,
        branch_cids=affected_branch_cids | force_rebuild_branch_cids | set(rebuilt),
        max_direct_leaf_children=max_direct_leaf_children,
        branch_imbalance_ratio=branch_imbalance_ratio,
    )
    return normalized, IncrementalTreePatchReport(
        health_issues=tuple(final_issues),
        rebuilt_branch_cids=tuple(rebuilt),
    )


def _analyze_affected_branch_health(
    nodes: Sequence[object],
    *,
    branch_cids: set[str],
    max_direct_leaf_children: int,
    branch_imbalance_ratio: float,
) -> tuple[BranchHealthIssue, ...]:
    normalized = [dict(node) for node in nodes if isinstance(node, dict)]
    by_cid = {str(node.get("cid") or ""): node for node in normalized}
    children = _children_by_parent(normalized)
    issues: list[BranchHealthIssue] = []
    for cid in sorted(str(item) for item in branch_cids if str(item)):
        node = by_cid.get(cid)
        if node is None:
            continue
        if str(node.get("type") or "") != "branch":
            continue
        direct_children = children.get(cid, [])
        if not direct_children:
            issues.append(
                BranchHealthIssue(branch_cid=cid, kind="empty_branch", severity=1.0, detail="branch has no children")
            )
            continue
        direct_leaf_count = sum(1 for child in direct_children if str(child.get("type") or "") != "branch")
        if direct_leaf_count > max_direct_leaf_children:
            severity = direct_leaf_count / max(1, max_direct_leaf_children)
            issues.append(
                BranchHealthIssue(
                    branch_cid=cid,
                    kind="too_many_direct_leaves",
                    severity=severity,
                    detail=f"direct_leaf_count={direct_leaf_count}, max={max_direct_leaf_children}",
                )
            )
        child_branch_leaf_counts = [
            _count_descendant_leaves(normalized, str(child.get("cid") or ""))
            for child in direct_children
            if str(child.get("type") or "") == "branch"
        ]
        positive_counts = [count for count in child_branch_leaf_counts if count > 0]
        if len(positive_counts) >= 2:
            ratio = max(positive_counts) / max(1, min(positive_counts))
            if ratio > branch_imbalance_ratio:
                issues.append(
                    BranchHealthIssue(
                        branch_cid=cid,
                        kind="imbalanced_children",
                        severity=ratio / max(1.0, branch_imbalance_ratio),
                        detail=f"child_leaf_count_ratio={ratio:.2f}, max={branch_imbalance_ratio:.2f}",
                    )
                )
    return tuple(sorted(issues, key=lambda item: (-item.severity, item.branch_cid, item.kind)))


def analyze_branch_health(
    nodes: Sequence[object],
    *,
    max_direct_leaf_children: int,
    branch_imbalance_ratio: float,
) -> tuple[BranchHealthIssue, ...]:
    normalized = [dict(node) for node in nodes if isinstance(node, dict)]
    branch_cids = {str(node.get("cid") or "") for node in normalized if str(node.get("type") or "") == "branch"}
    return _analyze_affected_branch_health(
        nodes=normalized,
        branch_cids=branch_cids,
        max_direct_leaf_children=max_direct_leaf_children,
        branch_imbalance_ratio=branch_imbalance_ratio,
    )


def _children_by_parent(nodes: Sequence[Dict[str, object]]) -> Dict[str, list[Dict[str, object]]]:
    children: Dict[str, list[Dict[str, object]]] = {}
    for node in nodes:
        cid = str(node.get("cid") or "")
        if not cid:
            continue
        children.setdefault(parent_cid(cid), []).append(node)
    return children


def _count_descendant_leaves(nodes: Sequence[Dict[str, object]], root_cid: str) -> int:
    return sum(
        1
        for node in nodes
        if str(node.get("type") or "") != "branch" and _is_descendant(str(node.get("cid") or ""), root_cid)
    )


def _is_descendant(cid: str, root_cid: str) -> bool:
    return bool(cid and root_cid and cid.startswith(f"{root_cid}."))


def _select_outermost_branches(branch_cids: set[str]) -> list[str]:
    selected: list[str] = []
    for cid in sorted(branch_cids, key=lambda item: (item.count("."), item)):
        if not cid:
            continue
        if any(cid == existing or cid.startswith(f"{existing}.") for existing in selected):
            continue
        selected.append(cid)
    return selected


def align_leaf_nodes_with_catalog(
    nodes: Sequence[object],
    records_by_worker: Dict[str, CatalogRecord],
    *,
    preserve_cids: bool = False,
) -> List[Dict[str, object]]:
    aligned: List[Dict[str, object]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        worker_id = str(node.get("worker_id") or "").strip()
        if worker_id and worker_id in records_by_worker:
            record = records_by_worker[worker_id]
            if not preserve_cids:
                node["cid"] = record.cid
            node["description"] = record.description
        aligned.append(node)
    return aligned


def build_catalog_records_from_existing(
    *, nodes: Sequence[object], records_by_worker: Dict[str, CatalogRecord]
) -> List[CatalogRecord]:
    records: List[CatalogRecord] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        worker_id = str(raw_node.get("worker_id") or "").strip()
        if not worker_id:
            continue
        record = records_by_worker.get(worker_id)
        if record is None:
            continue
        records.append(
            CatalogRecord(
                worker_id=record.worker_id,
                cid=str(raw_node.get("cid") or record.cid),
                name=record.name,
                description=str(raw_node.get("description") or record.description),
                skill_path=record.skill_path,
                branch_path=tuple(str(raw_node.get("cid") or record.cid).split(".")[:-1]),
                category=record.category,
                retrieval_text=record.retrieval_text,
                metadata=dict(record.metadata),
            )
        )
    return sorted(records, key=lambda item: item.cid)


def tree_nodes_to_tree_dict(nodes: Sequence[object], records: Sequence[CatalogRecord]) -> dict:
    nodes_by_cid = {str(node.get("cid") or ""): dict(node) for node in nodes if isinstance(node, dict)}
    children: Dict[str, list[str]] = {}
    for cid in nodes_by_cid:
        parent = parent_cid(cid)
        children.setdefault(parent, []).append(cid)
    records_by_cid = {record.cid: record for record in records}

    def build(cid: str) -> dict:
        node = nodes_by_cid.get(cid, {})
        label = cid.split(".")[-1] if cid else "ROOT"
        payload = {
            "name": str(node.get("worker_id") or label),
            "cid": cid,
            "type": str(node.get("type") or "branch"),
            "description": str(node.get("description") or ""),
            "children": [build(child) for child in sorted(children.get(cid, []))],
        }
        record = records_by_cid.get(cid)
        if record is not None:
            payload["skill_path"] = record.skill_path
            payload["worker_id"] = record.worker_id
        return payload

    root_children = [build(cid) for cid in sorted(children.get("", []))]
    return {"name": "ROOT", "cid": "ROOT", "type": "root", "children": root_children}
