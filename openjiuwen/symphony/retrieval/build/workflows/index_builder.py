from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from openjiuwen.symphony.retrieval.build.io import load_manifest, normalize_item_paths
from openjiuwen.symphony.retrieval.build.tree.builder import build_tree as _build_tree
from openjiuwen.symphony.retrieval.build.workflows.full_build import _IndexBuildWorkflow
from openjiuwen.symphony.retrieval.build.workflows.incremental_build import (
    _IncrementalIndexBuildWorkflow,
    branch_and_ancestors,
    parent_branches_for_workers,
)
from openjiuwen.symphony.retrieval.build.workflows.item_sources import (
    ResolvedItemPath,
    download_remote_zip,
    extract_item_zip,
    load_pre_scanned_items,
    materialize_existing_index_dir,
    normalize_manifest_item_path,
    resolve_item_paths_or_error,
    resolve_materialized_item_paths,
    safe_extract_zip,
    validate_item_dir,
)
from openjiuwen.symphony.retrieval.build.workflows.subtree_rebuild import extract_json_object, is_descendant_cid
from openjiuwen.symphony.shared.profiling import StageTimer
from openjiuwen.symphony.shared.storage import (
    download_s3_object_to_path as _download_s3_object_to_path,
)
from openjiuwen.symphony.shared.storage import is_s3_uri, upload_local_dir_to_s3

from .artifacts import BuildConfig, resolve_build_config

build_tree = _build_tree
download_s3_object_to_path = _download_s3_object_to_path

LOGGER = logging.getLogger("index_builder")


class IndexBuilder:
    @staticmethod
    def build(
        item_paths: list[str] | None = None,
        output_dir: str | Path | None = None,
        *,
        item_type: str = "skill",
        config: BuildConfig | None = None,
        runtime_config: BuildConfig | None = None,
        item_jsonl_path: str | None = None,
    ) -> str | Path:
        timer = StageTimer("IndexBuilder.build", logger=LOGGER)
        try:
            with timer.phase("resolve_inputs"):
                if output_dir is None:
                    raise ValueError("IndexBuilder.build: output_dir is required")
                normalized_item_paths = resolve_item_paths_or_error(
                    item_paths=item_paths,
                    item_jsonl_path=item_jsonl_path,
                    operation="build",
                )
                resolved_config = resolve_build_config(config=config, runtime_config=runtime_config)
                pre_scanned_skills, manifest_item_paths = load_pre_scanned_items(
                    item_jsonl_path=item_jsonl_path,
                    default_paths=normalized_item_paths,
                )
                output_value = str(output_dir).strip()

            def workflow_factory(local_output_dir: Path):
                return _IndexBuildWorkflow(
                    item_paths=manifest_item_paths,
                    output_dir=local_output_dir,
                    resolved_config=resolved_config,
                    item_type=item_type,
                    pre_scanned_skills=pre_scanned_skills,
                    manifest_item_paths=manifest_item_paths,
                )

            return _build_and_maybe_upload_s3(
                output_value=output_value,
                local_output_dir=Path(output_dir),
                temp_prefix="retriever-index-s3-output-",
                workflow_factory=workflow_factory,
                timer=timer,
            )
        finally:
            timer.finish()

    @staticmethod
    def add(
        item_paths: list[str] | None = None,
        base_index_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        *,
        item_type: str = "skill",
        config: BuildConfig | None = None,
        runtime_config: BuildConfig | None = None,
        item_jsonl_path: str | None = None,
    ) -> str | Path:
        timer = StageTimer("IndexBuilder.add", logger=LOGGER)
        try:
            with timer.phase("resolve_inputs"):
                if base_index_dir is None:
                    raise ValueError("IndexBuilder.add: base_index_dir is required")
                if output_dir is None:
                    raise ValueError("IndexBuilder.add: output_dir is required")
                base_dir = materialize_existing_index_dir(base_index_dir, cache_namespace="retriever-index-add-cache")
                manifest = load_manifest(base_dir)
                raw_existing = manifest.get("item_paths")
                existing = normalize_item_paths(raw_existing if isinstance(raw_existing, list) else ())
                normalized_item_paths = resolve_item_paths_or_error(
                    item_paths=item_paths,
                    item_jsonl_path=item_jsonl_path,
                    operation="add",
                )
                added_scanned_skills, added_paths = load_pre_scanned_items(
                    item_jsonl_path=item_jsonl_path,
                    default_paths=normalized_item_paths,
                )
                combined = normalize_item_paths([*existing, *added_paths])
                resolved_config = resolve_build_config(config=config, runtime_config=runtime_config)
                output_value = str(output_dir).strip()

            def workflow_factory(local_output_dir: Path):
                return _IncrementalIndexBuildWorkflow(
                    item_paths=combined,
                    output_dir=local_output_dir,
                    resolved_config=resolved_config,
                    base_index_dir=base_dir,
                    added_paths=added_paths,
                    removed_paths=[],
                    item_type=item_type,
                    added_scanned_skills=added_scanned_skills,
                    manifest_item_paths=combined,
                )

            return _build_and_maybe_upload_s3(
                output_value=output_value,
                local_output_dir=Path(output_dir),
                temp_prefix="retriever-index-add-s3-output-",
                workflow_factory=workflow_factory,
                timer=timer,
            )
        finally:
            timer.finish()

    @staticmethod
    def delete(
        item_paths: list[str] | None = None,
        base_index_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        *,
        item_type: str = "skill",
        config: BuildConfig | None = None,
        runtime_config: BuildConfig | None = None,
        item_jsonl_path: str | None = None,
    ) -> str | Path:
        timer = StageTimer("IndexBuilder.delete", logger=LOGGER)
        try:
            with timer.phase("resolve_inputs"):
                if base_index_dir is None:
                    raise ValueError("IndexBuilder.delete: base_index_dir is required")
                if output_dir is None:
                    raise ValueError("IndexBuilder.delete: output_dir is required")
                base_dir = materialize_existing_index_dir(
                    base_index_dir, cache_namespace="retriever-index-delete-cache"
                )
                manifest = load_manifest(base_dir)
                raw_existing = manifest.get("item_paths")
                existing = normalize_item_paths(raw_existing if isinstance(raw_existing, list) else ())
                normalized_item_paths = resolve_item_paths_or_error(
                    item_paths=item_paths,
                    item_jsonl_path=item_jsonl_path,
                    operation="delete",
                )
                _, removed_paths = load_pre_scanned_items(
                    item_jsonl_path=item_jsonl_path,
                    default_paths=normalized_item_paths,
                )
                removed = set(removed_paths)
                normalized_remaining = normalize_item_paths([path for path in existing if path not in removed])
                resolved_config = resolve_build_config(config=config, runtime_config=runtime_config)
                output_value = str(output_dir).strip()

            def workflow_factory(local_output_dir: Path):
                return _IncrementalIndexBuildWorkflow(
                    item_paths=normalized_remaining,
                    output_dir=local_output_dir,
                    resolved_config=resolved_config,
                    base_index_dir=base_dir,
                    added_paths=[],
                    removed_paths=sorted(removed),
                    item_type=item_type,
                    manifest_item_paths=normalized_remaining,
                )

            return _build_and_maybe_upload_s3(
                output_value=output_value,
                local_output_dir=Path(output_dir),
                temp_prefix="retriever-index-delete-s3-output-",
                workflow_factory=workflow_factory,
                timer=timer,
            )
        finally:
            timer.finish()


def _build_and_maybe_upload_s3(
    *,
    output_value: str,
    local_output_dir: Path,
    temp_prefix: str,
    workflow_factory,
    timer: StageTimer,
) -> str | Path:
    if is_s3_uri(output_value):
        with tempfile.TemporaryDirectory(prefix=temp_prefix) as tmpdir:
            materialized_output_dir = Path(tmpdir) / "index"
            workflow_factory(materialized_output_dir).build()
            with timer.phase("upload_s3_output"):
                upload_local_dir_to_s3(materialized_output_dir, output_value)
        return output_value.rstrip("/")
    return workflow_factory(local_output_dir).build()


# Backward-compatible private names for local tests/scripts that imported them
# from the old monolithic module.
_branch_and_ancestors = branch_and_ancestors
_download_remote_zip = download_remote_zip
_extract_item_zip = extract_item_zip
_extract_json_object = extract_json_object
_is_descendant_cid = is_descendant_cid
_load_pre_scanned_items = load_pre_scanned_items
_materialize_existing_index_dir = materialize_existing_index_dir
_normalize_manifest_item_path = normalize_manifest_item_path
_parent_branches_for_workers = parent_branches_for_workers
_resolve_item_paths_or_error = resolve_item_paths_or_error
_resolve_materialized_item_paths = resolve_materialized_item_paths
_safe_extract_zip = safe_extract_zip
_validate_item_dir = validate_item_dir


__all__ = [
    "IndexBuilder",
    "ResolvedItemPath",
    "_IndexBuildWorkflow",
    "_IncrementalIndexBuildWorkflow",
]
