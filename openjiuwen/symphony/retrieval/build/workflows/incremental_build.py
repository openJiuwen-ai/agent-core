from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Dict, Sequence

from openjiuwen.symphony.retrieval.build.io import load_catalog_records, load_tree_preset
from openjiuwen.symphony.retrieval.build.models import CATALOG_FILENAME, TREE_INDEX_FILENAME, CatalogRecord
from openjiuwen.symphony.retrieval.build.scanners import create_scanner
from openjiuwen.symphony.retrieval.build.workflows.full_build import _IndexBuildWorkflow
from openjiuwen.symphony.retrieval.build.workflows.item_sources import (
    normalize_manifest_item_path,
    resolve_materialized_item_paths,
)
from openjiuwen.symphony.retrieval.build.workflows.output_writer import write_index_outputs
from openjiuwen.symphony.retrieval.build.workflows.subtree_rebuild import IncrementalSubtreeRebuilder, is_descendant_cid
from openjiuwen.symphony.retrieval.build.workflows.tree_ops import (
    align_leaf_nodes_with_catalog,
    apply_incremental_tree_maintenance,
    build_catalog_records_from_existing,
    merge_added_skills_into_tree_with_report,
    prune_deleted_skills_from_tree,
)
from openjiuwen.symphony.shared.storage import is_s3_uri

from .artifacts import build_catalog_records_from_nodes, can_build_tree_with_llm

LOGGER = logging.getLogger("index_builder")
MAX_LOGGED_HEALTH_ISSUES = 5


class _IncrementalIndexBuildWorkflow(_IndexBuildWorkflow):
    def __init__(
        self,
        *,
        item_paths: Sequence[str],
        output_dir: Path,
        resolved_config,
        base_index_dir: Path,
        added_paths: Sequence[str],
        removed_paths: Sequence[str],
        item_type: str,
        added_scanned_skills: Dict[str, dict] | None = None,
        manifest_item_paths: Sequence[str] | None = None,
    ) -> None:
        super().__init__(
            item_paths=item_paths,
            output_dir=output_dir,
            resolved_config=resolved_config,
            item_type=item_type,
            manifest_item_paths=manifest_item_paths,
        )
        self._base_index_dir = base_index_dir.resolve()
        self._added_paths = _non_empty_strings(added_paths)
        self._removed_paths = _non_empty_strings(removed_paths)
        self._added_scanned_skills = (
            {str(key): dict(value) for key, value in (added_scanned_skills or {}).items()}
            if added_scanned_skills is not None
            else None
        )

    def build(self) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        if not (self._base_index_dir / TREE_INDEX_FILENAME).exists():
            return super().build()

        existing_catalog = load_catalog_records(self._base_index_dir / CATALOG_FILENAME)
        tree_payload = load_tree_preset(self._base_index_dir / TREE_INDEX_FILENAME)
        raw_existing_nodes = tree_payload.get("nodes")
        existing_nodes = list(raw_existing_nodes) if isinstance(raw_existing_nodes, list) else []
        if self._should_full_rebuild(existing_catalog):
            return super().build()

        nodes, catalog_records, affected_branch_cids, force_rebuild_branch_cids = self._apply_incremental_patch(
            existing_nodes=existing_nodes,
            existing_catalog=existing_catalog,
        )
        worker_to_record = {record.worker_id: record for record in catalog_records}
        nodes = self._maintain_incremental_tree(
            nodes=nodes,
            affected_branch_cids=affected_branch_cids,
            force_rebuild_branch_cids=force_rebuild_branch_cids,
            records_by_worker=worker_to_record,
        )
        catalog_records = build_catalog_records_from_existing(nodes=nodes, records_by_worker=worker_to_record)
        self._write_incremental_outputs(nodes=nodes, catalog_records=catalog_records)
        return self._output_dir

    def _should_full_rebuild(self, existing_catalog: Sequence[CatalogRecord]) -> bool:
        change_count = len(self._added_paths) + len(self._removed_paths)
        change_ratio = change_count / max(1, len(existing_catalog))
        if change_count <= 0 or change_ratio <= self._config.incremental_max_change_ratio:
            return False
        LOGGER.info(
            "incremental build escalated to full rebuild | "
            "change_count=%s | base_count=%s | ratio=%.4f | threshold=%.4f",
            change_count,
            len(existing_catalog),
            change_ratio,
            self._config.incremental_max_change_ratio,
        )
        return True

    def _apply_incremental_patch(
        self,
        *,
        existing_nodes: Sequence[object],
        existing_catalog: Sequence[CatalogRecord],
    ) -> tuple[list[dict[str, object]], list[CatalogRecord], set[str], set[str]]:
        remaining_paths = set(self._item_paths)
        removed_path_set = set(self._removed_paths)
        kept_catalog: list[CatalogRecord] = []
        for record in existing_catalog:
            if not _record_matches_manifest_paths(record, remaining_paths):
                continue
            if _record_matches_manifest_paths(record, removed_path_set):
                continue
            kept_catalog.append(record)

        if self._added_paths:
            return self._apply_add_patch(existing_nodes=existing_nodes, kept_catalog=kept_catalog)
        return self._apply_delete_patch(
            existing_nodes=existing_nodes,
            existing_catalog=existing_catalog,
            kept_catalog=kept_catalog,
            removed_path_set=removed_path_set,
        )

    def _apply_add_patch(
        self,
        *,
        existing_nodes: Sequence[object],
        kept_catalog: Sequence[CatalogRecord],
    ) -> tuple[list[dict[str, object]], list[CatalogRecord], set[str], set[str]]:
        scanned_added = (
            self._added_scanned_skills if self._added_scanned_skills is not None else self._scan_added_skills()
        )
        nodes, placement_report = merge_added_skills_into_tree_with_report(
            nodes=existing_nodes,
            added_skills=scanned_added,
            min_confidence=self._config.incremental_min_add_confidence,
            min_margin=self._config.incremental_min_add_confidence_margin,
        )
        added_catalog = build_catalog_records_from_nodes(
            nodes=nodes,
            scanned_skills=scanned_added,
            restrict_worker_ids=set(scanned_added),
        )
        merged = {record.worker_id: record for record in kept_catalog}
        for record in added_catalog:
            merged[record.worker_id] = record
        catalog_records = sorted(merged.values(), key=lambda item: item.cid)
        affected_branch_cids: set[str] = set()
        for decision in placement_report.placement_decisions:
            affected_branch_cids.update(branch_and_ancestors(decision.parent_cid))
        low_confidence_worker_ids = set(placement_report.low_confidence_worker_ids)
        force_rebuild_branch_cids: set[str] = set()
        for decision in placement_report.placement_decisions:
            if decision.worker_id in low_confidence_worker_ids:
                force_rebuild_branch_cids.add(decision.parent_cid)
        return nodes, catalog_records, affected_branch_cids, force_rebuild_branch_cids

    def _apply_delete_patch(
        self,
        *,
        existing_nodes: Sequence[object],
        existing_catalog: Sequence[CatalogRecord],
        kept_catalog: Sequence[CatalogRecord],
        removed_path_set: set[str],
    ) -> tuple[list[dict[str, object]], list[CatalogRecord], set[str], set[str]]:
        removed_worker_ids: set[str] = set()
        for record in existing_catalog:
            if _record_matches_manifest_paths(record, removed_path_set):
                removed_worker_ids.add(record.worker_id)
        affected_branch_cids = parent_branches_for_workers(existing_nodes, removed_worker_ids)
        nodes = prune_deleted_skills_from_tree(existing_nodes, removed_worker_ids=removed_worker_ids)
        catalog_records = sorted(kept_catalog, key=lambda item: item.cid)
        return nodes, catalog_records, affected_branch_cids, set()

    def _maintain_incremental_tree(
        self,
        *,
        nodes: Sequence[object],
        affected_branch_cids: set[str],
        force_rebuild_branch_cids: set[str],
        records_by_worker: Dict[str, CatalogRecord],
    ) -> list[dict[str, object]]:
        subtree_rebuilder = None
        if can_build_tree_with_llm(self._config):
            subtree_rebuilder = IncrementalSubtreeRebuilder(self._config).rebuild
        maintained_nodes, maintenance_report = apply_incremental_tree_maintenance(
            nodes=nodes,
            affected_branch_cids=affected_branch_cids,
            records_by_worker=records_by_worker,
            max_direct_leaf_children=self._config.tree_branching_factor,
            branch_imbalance_ratio=self._config.incremental_branch_imbalance_ratio,
            force_rebuild_branch_cids=force_rebuild_branch_cids,
            subtree_rebuilder=subtree_rebuilder,
        )
        preserve_tree_cids = bool(maintenance_report.rebuilt_branch_cids)
        if maintenance_report.rebuilt_branch_cids or maintenance_report.health_issues:
            LOGGER.info(
                "incremental tree maintenance | rebuilt=%s | remaining_health_issues=%s",
                list(maintenance_report.rebuilt_branch_cids),
                [issue.detail for issue in maintenance_report.health_issues[:MAX_LOGGED_HEALTH_ISSUES]],
            )
        return align_leaf_nodes_with_catalog(maintained_nodes, records_by_worker, preserve_cids=preserve_tree_cids)

    def _write_incremental_outputs(
        self,
        *,
        nodes: Sequence[dict[str, object]],
        catalog_records: Sequence[CatalogRecord],
    ) -> None:
        write_index_outputs(
            output_dir=self._output_dir,
            manifest_item_paths=self._manifest_item_paths,
            tree_nodes=nodes,
            catalog_records=catalog_records,
            mode="incremental",
            item_type=self._item_type,
            generate_tree_html=self._config.generate_tree_html,
        )

    def _scan_added_skills(self) -> dict[str, dict]:
        scanned: dict[str, dict] = {}
        with tempfile.TemporaryDirectory(prefix="retriever-index-added-items-") as tmpdir:
            resolved_item_paths = resolve_materialized_item_paths(
                self._added_paths,
                work_dir=Path(tmpdir),
                item_type=self._item_type,
            )
            for item in resolved_item_paths:
                skill_dir = item.materialized_dir
                scan_root = skill_dir.parent
                scanner = create_scanner(self._item_type, scan_root, display_items_dir=scan_root)
                for scanned_item in scanner.to_dict_list():
                    if str(scanned_item.get("id") or "") == skill_dir.name:
                        scanned[skill_dir.name] = scanned_item
                        if not is_s3_uri(item.source_path):
                            scanned[skill_dir.name]["path"] = normalize_manifest_item_path(item.source_path)
                        else:
                            scanned[skill_dir.name]["path"] = item.source_path
                        break
        return scanned


def branch_and_ancestors(cid: str) -> set[str]:
    parts: list[str] = []
    for part in str(cid or "").split("."):
        if part:
            parts.append(part)
    ancestors: set[str] = set()
    for index in range(1, len(parts) + 1):
        ancestors.add(".".join(parts[:index]))
    return ancestors


def parent_branches_for_workers(nodes: Sequence[object], worker_ids: set[str]) -> set[str]:
    branches: set[str] = set()
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        worker_id = str(raw_node.get("worker_id") or "")
        if worker_id not in worker_ids:
            continue
        cid = str(raw_node.get("cid") or "")
        parent = cid.rsplit(".", 1)[0] if "." in cid else ""
        branches.update(branch_and_ancestors(parent))
    return branches


_branch_and_ancestors = branch_and_ancestors
_is_descendant_cid = is_descendant_cid
_parent_branches_for_workers = parent_branches_for_workers


def _non_empty_strings(values: Sequence[str]) -> list[str]:
    results: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized:
            results.append(normalized)
    return results


def _record_matches_manifest_paths(record: CatalogRecord, manifest_paths: set[str]) -> bool:
    if normalize_manifest_item_path(record.skill_path) in manifest_paths:
        return True
    worker_id = str(record.worker_id or "").strip()
    return bool(worker_id) and f"jsonl://skill/{worker_id}" in manifest_paths
