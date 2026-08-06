from __future__ import annotations

from typing import Optional

from .mixin import TreeBuilderMixin
from .preset_writer import TreePresetWriter as _TreePresetWriter
from .schema import TreeNode


class TreeBuilderAdaptersMixin(TreeBuilderMixin):
    def _sorted_skills(self, skills: list[dict]) -> list[dict]:
        return self.grouping_engine.sorted_skills(skills)

    def _iter_group_items(self, groups: dict):
        return self.grouping_engine.iter_group_items(groups)

    def _normalize_prompt_for_fingerprint(self, prompt: str) -> str:
        """Normalize prompt text to keep fingerprint stable across runs."""
        return self.llm_runtime.normalize_prompt_for_fingerprint(prompt)

    def _prompt_fingerprint(self, prompt: str) -> str:
        """Compute deterministic prompt fingerprint."""
        return self.llm_runtime.prompt_fingerprint(prompt)

    def _sampling_seed(self, parent_context: Optional[dict], skills_count: int) -> int:
        return self.grouping_engine.sampling_seed(parent_context, skills_count)

    def _extract_cache_hit(self, response) -> Optional[bool]:
        """Best-effort extraction of cache hit status from response metadata."""
        return self.llm_runtime.extract_cache_hit(response)

    def _extract_cache_hit_from_mapping(self, mapping: dict) -> Optional[bool]:
        """Parse cache hit from a mapping (recursively over nested dicts)."""
        return self.llm_runtime.extract_cache_hit_from_mapping(mapping)

    def _record_cache_observation(self, cache_hit: Optional[bool]) -> None:
        """Aggregate cache hit/miss counters."""
        self.llm_runtime.record_cache_observation(cache_hit)

    def _print_cache_stats(self) -> None:
        """Print cache observability metrics for intuitive build feedback."""
        self.llm_runtime.print_cache_stats()

    def _build_groups_from_assignments(self, groups: dict, assignments: dict) -> dict:
        return self.grouping_engine.build_groups_from_assignments(groups, assignments)

    def _classify_skills(self, skills: list[dict], groups: dict, verbose: bool = False) -> dict:
        return self.grouping_engine.classify_skills(skills, groups, verbose=verbose)

    def _classify_skills_single(
        self,
        skills: list[dict],
        groups: dict,
        verbose: bool = False,
        is_retry: bool = False,
    ) -> dict:
        return self.grouping_engine.classify_skills_single(
            skills,
            groups,
            verbose=verbose,
            is_retry=is_retry,
        )

    def _batched_classify_skills(
        self,
        skills: list[dict],
        groups: dict,
        batch_size: int,
        verbose: bool = False,
    ) -> dict:
        return self.grouping_engine.batched_classify_skills(
            skills,
            groups,
            batch_size=batch_size,
            verbose=verbose,
        )

    def _validate_and_recover(
        self,
        skills: list[dict],
        groups: dict,
        assignments: dict,
        verbose: bool = False,
    ) -> dict:
        return self.grouping_engine.validate_and_recover(
            skills,
            groups,
            assignments,
            verbose=verbose,
        )

    def _discover_groups(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        return self.grouping_engine.discover_groups(skills, parent_context, verbose=verbose)

    def _merge_group_definitions(self, all_group_defs: list[dict], verbose: bool = False) -> dict:
        return self.grouping_engine.merge_group_definitions(all_group_defs, verbose=verbose)

    def _split_skills(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        return self.grouping_engine.split_skills(skills, parent_context, verbose=verbose)

    def _split_skills_single(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        verbose: bool = False,
    ) -> dict:
        return self.grouping_engine.split_skills_single(skills, parent_context, verbose=verbose)

    def _batched_split_skills(
        self,
        skills: list[dict],
        parent_context: Optional[dict],
        batch_size: int,
        verbose: bool = False,
    ) -> dict:
        return self.grouping_engine.batched_split_skills(
            skills,
            parent_context,
            batch_size=batch_size,
            verbose=verbose,
        )

    def _call_llm(self, prompt: str, is_retry: bool = False, retry_left: int | None = None) -> str:
        """Call LLM and return response."""
        return self.llm_runtime.call_llm(prompt, is_retry=is_retry, retry_left=retry_left)

    def _call_llm_json(self, prompt: str, max_retries: int = 3, is_retry: bool = False) -> dict:
        """Call LLM expecting a JSON dict response, with retry on format errors."""
        return self.llm_runtime.call_llm_json(prompt, max_retries=max_retries, is_retry=is_retry)

    def _format_skills_list(self, skills: list[dict]) -> str:
        return self.grouping_engine.format_skills_list(skills)

    def _tree_to_dict(self, tree: TreeNode) -> dict:
        writer = getattr(self, "preset_writer", None)
        if writer is None:
            writer = _TreePresetWriter(self._tree_builder())
        converted = writer.tree_to_dict(tree)
        return dict(converted)

    def _tree_to_orchestrator_preset(self, tree_dict: dict) -> dict:
        return self.preset_writer.tree_to_orchestrator_preset(tree_dict)

    def _flatten_capability_tree(self, tree: dict) -> list[dict]:
        return self.preset_writer.flatten_capability_tree(tree)

    def _rename_leaf_nodes(self, nodes: list[dict]) -> list[dict]:
        return self.preset_writer.rename_leaf_nodes(nodes)

    def _compact_leaf_cid_seed(self, *, worker_id: str, display_name: str, old_term: str) -> str:
        preset_writer = getattr(self, "preset_writer", None)
        if preset_writer is None:
            preset_writer = _TreePresetWriter(self._tree_builder())
        return preset_writer.compact_leaf_cid_seed(
            worker_id=worker_id,
            display_name=display_name,
            old_term=old_term,
        )

    def _cid_term(self, value: str, fallback: str = "Node") -> str:
        preset_writer = getattr(self, "preset_writer", None)
        if preset_writer is None:
            preset_writer = _TreePresetWriter(self._tree_builder())
        return preset_writer.cid_term(value, fallback=fallback)

    @staticmethod
    def _build_routing_policy(nodes: list[dict]) -> str:
        root_entries = sorted(
            [item for item in nodes if "." not in str(item.get("cid", ""))],
            key=lambda item: str(item.get("cid", "")),
        )
        lines = [
            "Route by descending the node tree one level at a time.",
            "Treat a user request as potentially multi-step unless the latest observation already "
            "fully satisfies every explicit requirement.",
            "Prefer leaves whose descriptions best match the next unmet sub-problem in the user request.",
            "After a worker returns, check whether unmet requirements still remain; "
            "if they do, continue routing instead of finishing early.",
            "Do not jump to User.Final after a single worker call when the user asked "
            "for multiple actions, dependencies, or deliverables.",
            "Use worker observations as intermediate state: "
            "one skill may gather facts or create prerequisites for a later skill.",
            "When multiple branches overlap, use the child descriptions as the local decision surface.",
            "Choose User.Final only when the latest observation set is sufficient to answer "
            "the whole user request, not just one subtask.",
        ]
        for item in root_entries:
            lines.append(f"If the request matches '{item['cid']}', continue under that branch.")
        return "\n".join(f"- {line}" for line in lines)

    def _build_tree_sketch(self, nodes: list[dict]) -> str:
        return self.preset_writer.build_tree_sketch(nodes)

    def _slug_term(self, value: str, fallback: str = "node") -> str:
        preset_writer = getattr(self, "preset_writer", None)
        if preset_writer is None:
            preset_writer = _TreePresetWriter(self._tree_builder())
        return preset_writer.slug_term(value, fallback=fallback)

    @staticmethod
    def _join_cid(parent: str, child: str) -> str:
        return _TreePresetWriter.join_cid(parent, child)

    @staticmethod
    def _parent_cid(cid: str) -> str:
        return _TreePresetWriter.parent_cid(cid)

    def _unique_child_cid(self, parent_cid: str, segment: str, used: set[str]) -> str:
        return self.preset_writer.unique_child_cid(parent_cid, segment, used)

    def _extract_keywords(self, *values: str, limit: int = 8) -> list[str]:
        return self.preset_writer.extract_keywords(*values, limit=limit)

    def _node_to_dict(self, node: TreeNode) -> dict:
        writer = getattr(self, "preset_writer", None)
        if writer is None:
            writer = _TreePresetWriter(self._tree_builder())
        payload = writer.node_to_dict(node)
        return payload.copy()

    def _print_tree(self, tree_dict: dict) -> None:
        """Print tree structure using rich (supports arbitrary depth)."""
        self.preset_writer.print_tree(tree_dict)

    def _add_node_to_rich_tree(self, parent_branch, node_dict: dict) -> None:
        """Recursively add nodes to rich tree."""
        self.preset_writer.add_node_to_rich_tree(parent_branch, node_dict)

    def _count_skills_in_dict(self, node_dict: dict) -> int:
        """Recursively count skills in a node dict."""
        return self.preset_writer.count_skills_in_dict(node_dict)
