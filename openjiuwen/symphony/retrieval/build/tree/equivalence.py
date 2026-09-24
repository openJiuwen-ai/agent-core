from __future__ import annotations

import re

from .mixin import TreeBuilderMixin
from .prompts import EQUIVALENCE_GROUPING_PROMPT
from .repair import TreeRepairEngine as _TreeRepairEngine
from .schema import TreeNode
from .shared import _GENERIC_TERMS, console


class TreeBuilderEquivalenceMixin(TreeBuilderMixin):
    def _normalize_to_equivalence_groups(self, root: TreeNode, verbose: bool = False) -> None:
        repair_engine = getattr(self, "repair_engine", None) or _TreeRepairEngine(self._tree_builder())
        repair_engine.normalize_to_equivalence_groups(root, verbose)

    @staticmethod
    def _is_second_leaf_node(node: TreeNode) -> bool:
        """Second-leaf node: has children and all children are leaf nodes."""
        return _TreeRepairEngine.is_second_leaf_node(node)

    def _split_second_leaf_node_into_equiv_groups(
        self,
        parent_node: TreeNode,
        second_leaf_node: TreeNode,
        verbose: bool = False,
    ) -> list[TreeNode]:
        repair_engine = getattr(self, "repair_engine", None) or _TreeRepairEngine(self._tree_builder())
        return repair_engine.split_second_leaf_node_into_equiv_groups(parent_node, second_leaf_node, verbose)

    def _discover_equivalence_groups(
        self,
        second_leaf_node: TreeNode,
        leaf_children: list[TreeNode],
        verbose: bool = False,
    ) -> dict:
        """Ask LLM to partition second-leaf children into equivalence groups."""
        leaf_lines = []
        for leaf in leaf_children:
            sample_skill_ids = ", ".join(skill.id for skill in leaf.skills[:5]) or "(none)"
            leaf_lines.append(
                f"- id: {leaf.id}\n"
                f"  name: {leaf.name}\n"
                f"  description: {leaf.description or '(no description)'}\n"
                f"  select_when: {leaf.select_when or ''}\n"
                f"  dont_select_when: {leaf.dont_select_when or ''}\n"
                f"  sample_skill_ids: {sample_skill_ids}"
            )

        prompt = EQUIVALENCE_GROUPING_PROMPT.format(
            parent_id=second_leaf_node.id,
            parent_name=second_leaf_node.name,
            parent_description=second_leaf_node.description or "(no description)",
            leaf_nodes="\n".join(leaf_lines),
            max_groups=self.settings.equiv_max_groups_per_parent,
        )
        result = self._call_llm_json(prompt)
        groups = result.get("groups", {})
        if not isinstance(groups, dict):
            if verbose:
                console.print(f"[yellow]  Equivalence grouping failed for '{second_leaf_node.id}'[/yellow]")
            return {}
        return groups

    def _normalize_equivalence_groups(self, leaf_children: list[TreeNode], groups: dict) -> list[dict]:
        """
        Normalize and repair LLM equivalence groups.

        Guarantees:
        - Every original leaf appears in exactly one output group
        - Unknown leaf IDs are ignored
        - Empty groups are removed
        """
        leaf_map = {leaf.id: leaf for leaf in leaf_children}
        assigned: set[str] = set()
        normalized: list[dict] = []

        for group_id, group_data in self._iter_group_items(groups):
            if not isinstance(group_data, dict):
                continue
            raw_leaf_ids = group_data.get("leaf_ids", [])
            if not isinstance(raw_leaf_ids, list):
                raw_leaf_ids = []
            leaf_nodes = []
            for leaf_id in raw_leaf_ids:
                lid = str(leaf_id).strip()
                if not lid or lid in assigned:
                    continue
                leaf = leaf_map.get(lid)
                if leaf is None:
                    continue
                assigned.add(lid)
                leaf_nodes.append(leaf)
            if not leaf_nodes:
                continue
            normalized.append(
                {
                    "id": self._build_equivalence_group_id(
                        group_id=str(group_id).strip(),
                        group_name=str(group_data.get("name") or "").strip(),
                        fallback="equiv-group",
                    ),
                    "name": str(group_data.get("name") or group_id),
                    "description": str(group_data.get("description") or ""),
                    "select_when": str(group_data.get("select_when") or ""),
                    "dont_select_when": str(group_data.get("dont_select_when") or ""),
                    "leaf_nodes": leaf_nodes,
                }
            )

        # Recovery: assign missing leaves conservatively.
        missing = [leaf for leaf in leaf_children if leaf.id not in assigned]
        if missing and normalized and not self.settings.equiv_allow_singleton_groups:
            largest_idx = max(range(len(normalized)), key=lambda idx: len(normalized[idx]["leaf_nodes"]))
            normalized[largest_idx]["leaf_nodes"].extend(missing)
        elif missing:
            for leaf in missing:
                normalized.append(
                    {
                        "id": f"equiv-{self._slug_term(leaf.id, fallback='leaf')}",
                        "name": leaf.name or leaf.id,
                        "description": leaf.description or "Equivalent capability group.",
                        "select_when": leaf.select_when,
                        "dont_select_when": leaf.dont_select_when,
                        "leaf_nodes": [leaf],
                    }
                )

        normalized = self._split_equivalence_groups_by_similarity(normalized)

        # Keep deterministic order.
        if self.settings.deterministic_prompts:
            for item in normalized:
                item["leaf_nodes"] = sorted(item["leaf_nodes"], key=lambda leaf: leaf.id)
            normalized.sort(key=lambda item: str(item.get("id", "")))
        return normalized

    def _build_equivalence_group_id(self, *, group_id: str, group_name: str, fallback: str) -> str:
        """
        Build a stable, readable node id for equivalence groups.

        LLMs often emit placeholder ids like G1/G2. We prefer semantic ids derived
        from the group name and only fall back to the raw id when it is informative.
        """
        raw_name = str(group_name or "").strip()
        raw_id = str(group_id or "").strip()
        generic_id = bool(re.fullmatch(r"g\d+(?:-\d+)?", raw_id.lower()))

        if raw_name:
            return self._slug_term(raw_name, fallback=fallback)
        if raw_id and not generic_id:
            return self._slug_term(raw_id, fallback=fallback)
        return self._slug_term(fallback, fallback="equiv-group")

    def _split_equivalence_groups_by_similarity(self, groups: list[dict]) -> list[dict]:
        """
        Split coarse LLM groups by lexical similarity connectivity.

        This is a conservative guardrail: if a group has disconnected semantic components
        under the configured similarity threshold, we keep them as separate equivalence groups.
        """
        if not groups:
            return groups
        if self.settings.equiv_min_lexical_similarity <= 0:
            return groups

        refined: list[dict] = []
        for group in groups:
            leaf_nodes = list(group.get("leaf_nodes", []) or [])
            if len(leaf_nodes) <= 1:
                refined.append(group)
                continue
            components = self._connected_leaf_components(
                leaf_nodes,
                self.settings.equiv_min_lexical_similarity,
            )
            if len(components) <= 1:
                refined.append(group)
                continue
            for idx, component in enumerate(components, start=1):
                refined.append(
                    {
                        "id": f"{group.get('id', 'equiv-group')}-{idx}",
                        "name": str(group.get("name", "Equivalent Group")),
                        "description": str(group.get("description", "")),
                        "select_when": str(group.get("select_when", "")),
                        "dont_select_when": str(group.get("dont_select_when", "")),
                        "leaf_nodes": component,
                    }
                )
        return refined

    @staticmethod
    def _connected_leaf_components(
        leaf_nodes: list[TreeNode],
        threshold: float,
    ) -> list[list[TreeNode]]:
        """Connected components over pairwise lexical similarity graph."""
        if len(leaf_nodes) <= 1:
            return [leaf_nodes]

        def tokens(leaf: TreeNode) -> set[str]:
            text = f"{leaf.id} {leaf.name} {leaf.description}"
            words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
            return {word for word in words if word not in _GENERIC_TERMS}

        token_map = {leaf.id: tokens(leaf) for leaf in leaf_nodes}
        index = {leaf.id: leaf for leaf in leaf_nodes}
        adj: dict[str, set[str]] = {leaf.id: set() for leaf in leaf_nodes}

        ids = [leaf.id for leaf in leaf_nodes]
        for i, left_id in enumerate(ids):
            left_tokens = token_map[left_id]
            right_start = i + 1
            for right_id in ids[right_start:]:
                right_tokens = token_map[right_id]
                union = left_tokens | right_tokens
                sim = (len(left_tokens & right_tokens) / len(union)) if union else 1.0
                if sim >= threshold:
                    adj[left_id].add(right_id)
                    adj[right_id].add(left_id)

        components: list[list[TreeNode]] = []
        visited: set[str] = set()
        for node_id in ids:
            if node_id in visited:
                continue
            stack = [node_id]
            visited.add(node_id)
            comp_ids: list[str] = []
            while stack:
                cur = stack.pop()
                comp_ids.append(cur)
                for nxt in adj[cur]:
                    if nxt in visited:
                        continue
                    visited.add(nxt)
                    stack.append(nxt)
            components.append([index[item_id] for item_id in comp_ids])
        return components
