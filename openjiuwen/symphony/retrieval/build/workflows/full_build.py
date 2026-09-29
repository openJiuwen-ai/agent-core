from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Sequence

from openjiuwen.symphony.retrieval.build.models import TREE_INDEX_FILENAME
from openjiuwen.symphony.retrieval.build.scanners import create_scanner, normalize_item_type
from openjiuwen.symphony.retrieval.build.tree import DynamicTreeConfig, TreeBuildConfig, TreeManagerConfig
from openjiuwen.symphony.retrieval.build.tree.schema import normalize_root_categories
from openjiuwen.symphony.retrieval.build.workflows.item_sources import ResolvedItemPath, resolve_materialized_item_paths
from openjiuwen.symphony.retrieval.build.workflows.output_writer import unlink_if_exists, write_index_outputs
from openjiuwen.symphony.shared.profiling import StageTimer

from .artifacts import (
    ResolvedBuildConfig,
    build_catalog_records_from_nodes,
    build_fallback_tree_nodes,
    can_build_tree_with_llm,
)

LOGGER = logging.getLogger("index_builder")


class _IndexBuildWorkflow:
    def __init__(
        self,
        *,
        item_paths: Sequence[str],
        output_dir: Path,
        resolved_config: ResolvedBuildConfig,
        item_type: str,
        pre_scanned_skills: Dict[str, dict] | None = None,
        manifest_item_paths: Sequence[str] | None = None,
    ) -> None:
        self._item_paths = [str(path).strip() for path in item_paths if str(path).strip()]
        self._output_dir = output_dir.resolve()
        self._config = resolved_config
        self._item_type = normalize_item_type(item_type)
        self._pre_scanned_skills = (
            {str(key): dict(value) for key, value in (pre_scanned_skills or {}).items()}
            if pre_scanned_skills is not None
            else None
        )
        self._manifest_item_paths = [
            str(path).strip() for path in (manifest_item_paths or self._item_paths) if str(path).strip()
        ]

    def build(self) -> Path:
        timer = StageTimer("_IndexBuildWorkflow.build", logger=LOGGER)
        try:
            with timer.phase("prepare_workspace"):
                self._output_dir.mkdir(parents=True, exist_ok=True)
            if self._pre_scanned_skills is not None:
                return self._build_from_pre_scanned(timer=timer)
            with tempfile.TemporaryDirectory(prefix="retriever-index-build-") as tmpdir:
                aggregate_dir = Path(tmpdir) / "skills"
                aggregate_dir.mkdir(parents=True, exist_ok=True)
                with timer.phase("materialize_items"):
                    resolved_item_paths = resolve_materialized_item_paths(
                        self._item_paths,
                        work_dir=Path(tmpdir),
                        item_type=self._item_type,
                    )
                    self._materialize_skill_dirs(aggregate_dir, resolved_item_paths)

                tree_preset = self._build_tree_preset(
                    aggregate_dir=aggregate_dir,
                    tree_output_path=self._output_dir / TREE_INDEX_FILENAME,
                    resolved_item_paths=resolved_item_paths,
                    pre_scanned_skills=None,
                    timer=timer,
                )
                self._write_outputs(
                    tree_preset=tree_preset,
                    aggregate_dir=aggregate_dir,
                    resolved_item_paths=resolved_item_paths,
                    pre_scanned_skills=None,
                    timer=timer,
                )
            return self._output_dir
        finally:
            timer.finish()

    def _build_from_pre_scanned(self, *, timer: StageTimer) -> Path:
        pre_scanned_skills = self._pre_scanned_skills or {}
        tree_preset = self._build_tree_preset(
            aggregate_dir=self._output_dir,
            tree_output_path=self._output_dir / TREE_INDEX_FILENAME,
            resolved_item_paths=[],
            pre_scanned_skills=pre_scanned_skills,
            timer=timer,
        )
        self._write_outputs(
            tree_preset=tree_preset,
            aggregate_dir=self._output_dir,
            resolved_item_paths=[],
            pre_scanned_skills=pre_scanned_skills,
            timer=timer,
        )
        return self._output_dir

    def _build_tree_preset(
        self,
        *,
        aggregate_dir: Path,
        tree_output_path: Path,
        resolved_item_paths: Sequence[ResolvedItemPath],
        pre_scanned_skills: Dict[str, dict] | None,
        timer: StageTimer,
    ) -> dict:
        if can_build_tree_with_llm(self._config):
            LOGGER.info(
                "tree llm runtime | workers=%s | timeout_seconds=%s | classify_batch_cap=%s",
                self._config.tree_max_workers,
                self._config.tree_timeout_seconds,
                self._config.tree_classify_batch_cap,
            )
            with timer.phase("build_tree_llm"):
                from openjiuwen.symphony.retrieval.build.workflows import index_builder as public_module

                return public_module.build_tree(
                    skills_dir=aggregate_dir,
                    output_path=tree_output_path,
                    config=DynamicTreeConfig(
                        branching_factor=self._config.tree_branching_factor,
                        max_depth=self._config.tree_max_depth,
                        root_categories=normalize_root_categories(self._config.tree_root_categories),
                    ),
                    manager_config=_tree_manager_config(self._config),
                    client=self._config.llm_openai_client,
                    model=self._config.llm_model,
                    api_key=self._config.tree_llm_api_key,
                    base_url=self._config.tree_llm_base_url,
                    llm_seed=self._config.llm_seed,
                    max_workers=self._config.tree_max_workers,
                    verbose=False,
                    show_tree=False,
                    display_skills_dir=(
                        self._infer_display_skills_dir(resolved_item_paths) if pre_scanned_skills is None else None
                    ),
                    item_type=self._item_type,
                    skill_entries=(list(pre_scanned_skills.values()) if pre_scanned_skills is not None else None),
                )

        if not self._config.allow_fallback_tree:
            raise ValueError("Tree build requested but no LLM capability is configured and fallback is disabled")
        LOGGER.warning("build fallback: tree -> fallback_tree | reason=tree llm is unavailable")
        with timer.phase("build_tree_fallback"):
            if pre_scanned_skills is None:
                return {"nodes": build_fallback_tree_nodes(aggregate_dir=aggregate_dir)}
            return {"nodes": self._fallback_tree_nodes_from_scanned(pre_scanned_skills)}

    def _write_outputs(
        self,
        *,
        tree_preset: dict,
        aggregate_dir: Path,
        resolved_item_paths: Sequence[ResolvedItemPath],
        pre_scanned_skills: Dict[str, dict] | None,
        timer: StageTimer,
    ) -> None:
        with timer.phase("build_catalog_and_tree_outputs"):
            raw_nodes = list((tree_preset or {}).get("nodes") or [])
            catalog_records = self._build_catalog_records(
                aggregate_dir,
                raw_nodes,
                resolved_item_paths=resolved_item_paths,
                pre_scanned_skills=pre_scanned_skills,
            )
            write_index_outputs(
                output_dir=self._output_dir,
                manifest_item_paths=self._manifest_item_paths,
                tree_nodes=raw_nodes,
                catalog_records=catalog_records,
                mode="full",
                item_type=self._item_type,
                generate_tree_html=self._config.generate_tree_html,
            )

    @staticmethod
    def _unlink_if_exists(path: Path) -> None:
        unlink_if_exists(path)

    @staticmethod
    def _materialize_skill_dirs(aggregate_dir: Path, item_paths: Sequence[ResolvedItemPath]) -> None:
        for item in item_paths:
            skill_dir = item.materialized_dir
            destination = aggregate_dir / skill_dir.name
            if destination.exists():
                raise ValueError(f"Duplicate skill directory name detected: {skill_dir.name}")
            try:
                destination.symlink_to(skill_dir, target_is_directory=True)
            except Exception:
                shutil.copytree(skill_dir, destination)

    @staticmethod
    def _infer_display_skills_dir(item_paths: Sequence[ResolvedItemPath]) -> Path | None:
        local_dirs = [item.materialized_dir.parent.resolve() for item in item_paths if item.source_type == "local_dir"]
        if not local_dirs:
            return None
        parents = set(local_dirs)
        return next(iter(parents)) if len(parents) == 1 else None

    def _build_catalog_records(
        self,
        aggregate_dir: Path,
        tree_nodes: Sequence[object],
        *,
        resolved_item_paths: Sequence[ResolvedItemPath],
        pre_scanned_skills: Dict[str, dict] | None = None,
    ):
        if pre_scanned_skills is not None:
            scanned = {str(key): dict(value) for key, value in pre_scanned_skills.items()}
            return build_catalog_records_from_nodes(nodes=tree_nodes, scanned_skills=scanned)
        source_by_skill = {item.materialized_dir.name: item.source_path for item in resolved_item_paths}
        scanned = {
            str(item["id"]): item
            for item in create_scanner(self._item_type, aggregate_dir, display_items_dir=aggregate_dir).to_dict_list()
        }
        for worker_id, item in scanned.items():
            source_path = source_by_skill.get(worker_id)
            if source_path:
                item["path"] = source_path
        return build_catalog_records_from_nodes(nodes=tree_nodes, scanned_skills=scanned)

    @staticmethod
    def _fallback_tree_nodes_from_scanned(scanned_items: Dict[str, dict]) -> list[dict[str, object]]:
        nodes: list[dict[str, object]] = [
            {
                "cid": "Skills",
                "type": "branch",
                "description": "Fallback skill index built without LLM tree generation.",
            }
        ]
        for worker_id in sorted(str(key) for key in scanned_items):
            item = scanned_items.get(worker_id) or {}
            nodes.append(
                {
                    "cid": f"Skills.{worker_id}",
                    "type": "leaf",
                    "description": str(item.get("description") or item.get("name") or worker_id),
                    "worker_id": worker_id,
                }
            )
        return nodes


def _tree_manager_config(config: ResolvedBuildConfig) -> TreeManagerConfig:
    return TreeManagerConfig(
        branching_factor=config.tree_branching_factor,
        max_depth=config.tree_max_depth,
        root_categories=normalize_root_categories(config.tree_root_categories),
        build=TreeBuildConfig(
            max_workers=config.tree_max_workers,
            caching=config.tree_caching,
            num_retries=config.tree_num_retries,
            timeout=config.tree_timeout_seconds,
            classify_batch_cap=config.tree_classify_batch_cap,
            context_window=config.tree_context_window,
            max_output_tokens=config.tree_max_output_tokens,
            postprocess_enabled=config.tree_postprocess_enabled,
            postprocess_max_passes=config.tree_postprocess_max_passes,
            postprocess_min_skills=config.tree_postprocess_min_skills,
            equiv_grouping_enabled=config.tree_equiv_grouping_enabled,
            equiv_max_groups_per_parent=config.tree_equiv_max_groups_per_parent,
            equiv_allow_singleton_groups=config.tree_equiv_allow_singleton_groups,
            equiv_min_lexical_similarity=config.tree_equiv_min_lexical_similarity,
            deterministic_prompts=config.tree_deterministic_prompts,
            discovery_seed=config.tree_discovery_seed,
            prompt_fingerprint_version=config.tree_prompt_fingerprint_version,
            cache_observability=config.tree_cache_observability,
            skill_profiles_enabled=config.tree_skill_profiles_enabled,
            skill_profile_select_rules_enabled=config.tree_skill_profile_select_rules_enabled,
            skill_profile_batch_size=config.tree_skill_profile_batch_size,
            skill_profile_description_limit=config.tree_skill_profile_description_limit,
            skill_profile_rule_limit=config.tree_skill_profile_rule_limit,
        ),
    )
