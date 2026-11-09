from __future__ import annotations

from openjiuwen.symphony.model_bridge import _BuildInterrupted

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Sequence

from openjiuwen.symphony.config import RetrievalBuildConfig

from openjiuwen.symphony.retrieval.build.models import TREE_INDEX_FILENAME
from openjiuwen.symphony.retrieval.build.scanners import create_scanner, normalize_item_type
from openjiuwen.symphony.retrieval.build.tree.builder import TreeBuilder
from openjiuwen.symphony.retrieval.build.tree.schema import TreeNode
from openjiuwen.symphony.retrieval.build.workflows.item_sources import ResolvedItemPath, resolve_materialized_item_paths
from openjiuwen.symphony.retrieval.build.workflows.output_writer import unlink_if_exists, write_index_outputs
from openjiuwen.symphony.shared.profiling import StageTimer

from .artifacts import (
    build_catalog_records_from_nodes,
    build_fallback_tree_nodes,
    build_fallback_tree_nodes_from_records,
    can_build_tree_with_llm,
)

LOGGER = logging.getLogger("index_builder")


class _IndexBuildWorkflow:
    def __init__(
        self,
        *,
        item_paths: Sequence[str],
        output_dir: Path,
        config: RetrievalBuildConfig,
        model: Any = None,
        item_type: str,
        pre_scanned_skills: Dict[str, dict] | None = None,
        manifest_item_paths: Sequence[str] | None = None,
    ) -> None:
        self._item_paths = [str(path).strip() for path in item_paths if str(path).strip()]
        self._output_dir = output_dir.resolve()
        self._config = config
        self._model = model
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
        if can_build_tree_with_llm(self._model):
            LOGGER.info(
                "tree llm runtime | method=%s | timeout_seconds=%s",
                "one-shot" if self._item_type == "skill" else "recursive",
                self._config.request.timeout_seconds,
            )
            with timer.phase("build_tree_llm"):
                from openjiuwen.symphony.retrieval.build.workflows import index_builder as public_module

                try:
                    if self._item_type == "skill":
                        return self._build_one_shot_tree(aggregate_dir, pre_scanned_skills)
                    return public_module.build_tree(
                        skills_dir=aggregate_dir,
                        output_path=tree_output_path,
                        config=self._config,
                        model=self._model,
                        item_type=self._item_type,
                        skill_entries=(list(pre_scanned_skills.values()) if pre_scanned_skills is not None else None),
                    )
                except Exception as exc:
                    if isinstance(exc, _BuildInterrupted):
                        raise
                    if not self._config.task.allow_fallback_tree:
                        raise
                    LOGGER.warning(
                        "build fallback: tree -> fallback_tree | reason=tree llm failed",
                        exc_info=True,
                    )

        if not self._config.task.allow_fallback_tree:
            raise ValueError("Tree build requested but no LLM capability is configured and fallback is disabled")
        LOGGER.warning("build fallback: tree -> fallback_tree | reason=tree llm is unavailable")
        with timer.phase("build_tree_fallback"):
            if pre_scanned_skills is None:
                return {"nodes": build_fallback_tree_nodes(aggregate_dir=aggregate_dir)}
            return {"nodes": self._fallback_tree_nodes_from_scanned(pre_scanned_skills)}

    def _build_one_shot_tree(self, aggregate_dir: Path, pre_scanned_skills: Dict[str, dict] | None) -> dict:
        from openjiuwen.symphony.retrieval.build.tree.one_shot import OneShotSkill, OneShotSkillTreeBuilder

        entries = (
            list(pre_scanned_skills.values())
            if pre_scanned_skills is not None
            else create_scanner("skill", aggregate_dir).to_dict_list()
        )
        builder = TreeBuilder(
            skills_dir=aggregate_dir, config=self._config, model=self._model, skill_entries=entries,
        )
        entries = builder._enrich_skill_profiles(entries, verbose=self._config.output.verbose)
        skills = [
            OneShotSkill(
                name=item.get("name") or item["id"],
                description=item.get("description") or "",
                worker_id=item["id"],
                skill_path=item.get("path") or "",
            )
            for item in entries
        ]
        result = OneShotSkillTreeBuilder(model=self._model, config=self._config).build(skills)
        builder.state.llm_calls += result.llm_calls
        builder.state.input_tokens += result.prompt_tokens
        builder.state.output_tokens += result.completion_tokens
        builder.state.total_tokens += result.total_tokens
        if self._config.output.cache_metrics_enabled:
            builder.state.cache_unknown += result.llm_calls
        # The one-shot assignment algorithm remains intact. Shared profile/repair
        # stages operate on its resulting taxonomy, without another full build.
        tree = _preset_tree(result.tree_preset, entries, builder)
        builder._repair_tree(tree, source_skills=entries, verbose=self._config.output.verbose)
        preset = builder.preset_writer.tree_to_orchestrator_preset(builder._tree_to_dict(tree), preserve_ids=True)
        if self._config.output.show_tree:
            builder._print_tree(builder._tree_to_dict(tree))
        builder._print_cache_stats()
        builder.close()
        LOGGER.info(
            "one-shot tree built | skills=%s | llm_calls=%s | prompt_tokens=%s | completion_tokens=%s "
            "| total_tokens=%s | elapsed_seconds=%.3f",
            len(skills),
            result.llm_calls,
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
            result.elapsed_seconds,
        )
        return preset

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
                generate_tree_html=self._config.output.generate_tree_html,
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
        return build_fallback_tree_nodes_from_records(
            {
                str(worker_id): str(item.get("description") or item.get("name") or worker_id)
                for worker_id, item in scanned_items.items()
            }
        )


def _preset_tree(preset: dict, entries: list[dict], builder: TreeBuilder) -> TreeNode:
    root = TreeNode(id="root", name="Root")
    branches = {"": root}
    by_id = {str(entry["id"]): entry for entry in entries}
    raw_nodes = list(preset.get("nodes") or [])
    for item in sorted(raw_nodes, key=lambda node: str(node.get("cid", "")).count(".")):
        cid = str(item.get("cid") or "")
        parent_id, _, segment = cid.rpartition(".")
        parent = branches.get(parent_id, root)
        if item.get("type") == "branch":
            branch = TreeNode(
                id=segment or cid, name=segment or cid,
                description=str(item.get("description") or ""),
                depth=cid.count(".") + 1, parent_id=parent.id,
            )
            parent.children.append(branch)
            branches[cid] = branch
        else:
            entry = by_id.get(str(item.get("worker_id") or ""))
            if entry is not None:
                payload = {**entry, "skill_path": entry.get("skill_path") or entry.get("path") or ""}
                parent.skills.append(builder._skill_from_data(payload, path=parent.id))
    return root
