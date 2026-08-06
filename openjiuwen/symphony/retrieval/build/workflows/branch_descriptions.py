from __future__ import annotations

from collections import Counter
from typing import Dict, List, Sequence

from openjiuwen.symphony.retrieval.build.models import CatalogRecord
from openjiuwen.symphony.retrieval.build.workflows.tree_text import text_tokens


def enrich_branch_descriptions(
    nodes: Sequence[object],
    *,
    catalog_records: Sequence[CatalogRecord],
) -> List[Dict[str, object]]:
    catalog_by_branch: Dict[str, list[CatalogRecord]] = {}
    for record in catalog_records:
        parts = record.cid.split(".")
        for depth in range(1, len(parts)):
            branch_cid = ".".join(parts[:depth])
            catalog_by_branch.setdefault(branch_cid, []).append(record)

    enriched: List[Dict[str, object]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        if str(node.get("type") or "") == "branch":
            cid = str(node.get("cid") or "")
            node["description"] = build_branch_description(
                cid=cid,
                existing_description=str(node.get("description") or ""),
                descendants=catalog_by_branch.get(cid, ()),
            )
        enriched.append(node)
    return enriched


def build_branch_description(*, cid: str, existing_description: str, descendants: Sequence[CatalogRecord]) -> str:
    base = strip_branch_exposure(existing_description)
    if not descendants:
        return base
    samples = sample_catalog_records(descendants, limit=3)
    parts: List[str] = []
    if base:
        parts.append(base)
    parts.append(f"Covers {len(descendants)} descendant skill{'s' if len(descendants) != 1 else ''}.")
    keywords = collect_branch_keywords(descendants, limit=8)
    if keywords:
        parts.append("Representative keywords: " + ", ".join(keywords))
    parts.append(
        "Representative descendants: " + "; ".join(format_catalog_record_snippet(record) for record in samples)
    )
    return "\n\n".join(part for part in parts if part).strip()


def strip_branch_exposure(description: str) -> str:
    marker = "Representative descendants:"
    head, _sep, _tail = str(description or "").partition(marker)
    return head.strip()


def sample_catalog_records(records: Sequence[CatalogRecord], *, limit: int) -> List[CatalogRecord]:
    target = max(0, limit)
    if target <= 0:
        return []
    ordered = sorted(records, key=lambda item: (item.name.lower(), item.worker_id.lower(), item.cid))
    selected: List[CatalogRecord] = []
    seen_worker_ids: set[str] = set()
    seen_tokens: set[str] = set()
    while len(selected) < min(target, len(ordered)):
        best_record: CatalogRecord | None = None
        best_score = -1
        for record in ordered:
            if record.worker_id in seen_worker_ids:
                continue
            tokens = _record_text_tokens(record)
            novelty = len(tokens - seen_tokens)
            coverage = len(tokens)
            score = novelty * 10 + coverage
            if best_record is None or score > best_score:
                best_record = record
                best_score = score
        if best_record is None:
            break
        selected.append(best_record)
        seen_worker_ids.add(best_record.worker_id)
        seen_tokens.update(_record_text_tokens(best_record))
    return selected


def format_catalog_record_snippet(record: CatalogRecord) -> str:
    name = str(record.name or record.worker_id).strip() or record.worker_id
    summary = _compact_summary(record.description, limit=96)
    if summary:
        return f"{name}: {summary}"
    return name


def collect_branch_keywords(records: Sequence[CatalogRecord], *, limit: int) -> List[str]:
    counter: Counter[str] = Counter()
    for record in records:
        counter.update(_record_text_tokens(record))
    if not counter:
        return []
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _count in ranked[: max(0, limit)]]


def _record_text_tokens(record: CatalogRecord) -> set[str]:
    return text_tokens(record.name, record.description, record.worker_id, record.cid)


def _compact_summary(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."
