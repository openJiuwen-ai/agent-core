from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from openjiuwen.symphony.config import RetrievalBuildConfig
from openjiuwen.symphony.model_bridge import _unwrap_model
from openjiuwen.symphony.retrieval.build.models import CatalogRecord
from openjiuwen.symphony.retrieval.build.tree.root_categories import RootCategoryInput, resolve_tree_root_categories
from openjiuwen.symphony.retrieval.build.workflows.tree_text import slug_term, unique_child_cid


def resolve_build_config(*, config: RetrievalBuildConfig | None = None) -> RetrievalBuildConfig:
    """Snapshot validated policy and resolve root-category input at execution time."""
    cfg = (config or RetrievalBuildConfig()).model_copy(deep=True)
    return cfg.model_copy(update={"structure": cfg.structure.model_copy(update={
        "root_categories": resolve_tree_root_categories(cfg.structure.root_categories),
    })})


def build_catalog_records_from_nodes(
    *,
    nodes: Sequence[object],
    scanned_skills: Dict[str, dict],
    restrict_worker_ids: set[str] | None = None,
) -> List[CatalogRecord]:
    records: List[CatalogRecord] = []
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        worker_id = str(raw_node.get("worker_id") or "").strip()
        if not worker_id:
            continue
        if restrict_worker_ids is not None and worker_id not in restrict_worker_ids:
            continue
        scanned = scanned_skills.get(worker_id) or {}
        cid = str(raw_node.get("cid") or "")
        name = str(scanned.get("name") or worker_id)
        has_tree_profile = any(
            str(raw_node.get(key) or "").strip() for key in ("source_description", "select_when", "dont_select_when")
        )
        description = (
            str(raw_node.get("description") or scanned.get("description") or "").strip()
            if has_tree_profile
            else str(scanned.get("description") or raw_node.get("description") or "").strip()
        )
        content = str(scanned.get("content") or "").strip()
        source_description = str(raw_node.get("source_description") or scanned.get("description") or "").strip()
        select_when = str(raw_node.get("select_when") or "").strip()
        dont_select_when = str(raw_node.get("dont_select_when") or "").strip()
        skill_path = str(scanned.get("path") or "")
        records.append(
            CatalogRecord(
                worker_id=worker_id,
                cid=cid,
                name=name,
                description=description,
                skill_path=skill_path,
                branch_path=tuple(cid.split(".")[:-1]),
                category=".".join(cid.split(".")[:-1]),
                retrieval_text=build_retrieval_text(
                    worker_id=worker_id,
                    name=name,
                    description=description,
                    content=content,
                    cid=cid,
                ),
                metadata={
                    "content": content,
                    "source_description": source_description,
                    "select_when": select_when,
                    "dont_select_when": dont_select_when,
                },
            )
        )
    return sorted(records, key=lambda item: item.cid)


def write_catalog(records: Sequence[CatalogRecord], path: Path) -> None:
    lines = [
        json.dumps(
            {
                "worker_id": record.worker_id,
                "cid": record.cid,
                "name": record.name,
                "description": record.description,
                "skill_path": record.skill_path,
                "branch_path": list(record.branch_path),
                "category": record.category,
                "retrieval_text": record.retrieval_text,
                "metadata": record.metadata,
            },
            ensure_ascii=False,
        )
        for record in records
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_fallback_tree_nodes(*, aggregate_dir: Path) -> List[Dict[str, object]]:
    descriptions = {path.name: path.name for path in aggregate_dir.iterdir() if path.is_dir()}
    return build_fallback_tree_nodes_from_records(descriptions)


def build_fallback_tree_nodes_from_records(
    descriptions_by_worker_id: Mapping[str, str],
) -> List[Dict[str, object]]:
    """Build stable fallback leaves without treating dots in IDs as hierarchy."""

    nodes: List[Dict[str, object]] = [
        {
            "cid": "Skills",
            "type": "branch",
            "description": "Fallback skill index built without LLM tree generation.",
        }
    ]
    used_cids = {"Skills"}
    for worker_id in sorted(str(value) for value in descriptions_by_worker_id):
        segment = slug_term(worker_id, fallback="skill")
        cid = unique_child_cid(parent="Skills", segment=segment, used=used_cids)
        used_cids.add(cid)
        nodes.append(
            {
                "cid": cid,
                "type": "leaf",
                "description": str(descriptions_by_worker_id.get(worker_id) or worker_id),
                "worker_id": worker_id,
            }
        )
    return nodes


def build_retrieval_text(*, worker_id: str, name: str, description: str, content: str, cid: str) -> str:
    parts = [
        compact_text(name, limit=200),
        compact_text(description, limit=400),
        compact_text(content, limit=1200),
        compact_text(worker_id, limit=120),
        compact_text(cid, limit=200),
    ]
    return "\n".join(part for part in parts if part)


def compact_text(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "..."


def can_build_tree_with_llm(model=None) -> bool:
    return callable(getattr(_unwrap_model(model), "invoke", None))


__all__ = [
    "build_catalog_records_from_nodes",
    "build_fallback_tree_nodes",
    "build_fallback_tree_nodes_from_records",
    "build_retrieval_text",
    "can_build_tree_with_llm",
    "compact_text",
    "resolve_build_config",
    "write_catalog",
]
