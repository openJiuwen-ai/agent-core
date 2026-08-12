from __future__ import annotations

from collections import Counter
from itertools import combinations
import re

from .mixin import TreeBuilderMixin
from .prompts import EQUIVALENCE_GROUPING_PROMPT, EQUIVALENCE_PAIRWISE_PROMPT
from .repair import TreeRepairEngine as _TreeRepairEngine
from .schema import TreeNode
from .shared import _GENERIC_TERMS, console


_PAIRWISE_BATCH_SIZE = 24
_PAIRWISE_DESCRIPTION_LIMIT = 800


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
        """Recall candidate groups, verify their pairs, then form deterministic components."""
        candidate_groups = self._discover_equivalence_candidates(second_leaf_node, leaf_children, verbose)
        if not candidate_groups:
            return {}

        leaf_map = {leaf.id: leaf for leaf in leaf_children}
        candidate_pairs = self._candidate_pairs_from_groups(leaf_map, candidate_groups)
        decisions = self._judge_candidate_pairs(second_leaf_node, leaf_map, candidate_pairs)
        components = self._equivalent_components(sorted(leaf_map), decisions)
        final_groups: dict[str, dict] = {}
        for group_index, component in enumerate(components, start=1):
            metadata = self._component_metadata(component, leaf_map, decisions)
            final_groups[f"g{group_index}"] = {**metadata, "leaf_ids": component}
        return final_groups

    def _discover_equivalence_candidates(
        self,
        second_leaf_node: TreeNode,
        leaf_children: list[TreeNode],
        verbose: bool,
    ) -> dict:
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
        expected_ids = {leaf.id for leaf in leaf_children}
        try:
            return self._validate_candidate_response(self._call_llm_json(prompt), expected_ids)
        except ValueError as error:
            if verbose:
                console.print(
                    f"[yellow]  Correcting invalid equivalence candidates for '{second_leaf_node.id}': {error}[/yellow]"
                )
            correction_prompt = (
                f"{prompt}\n\nYour previous response violated the required schema: {error}. "
                "Return corrected groups that cover every provided leaf id at least once."
            )
            payload = self._call_llm_json(correction_prompt, is_retry=True)
            return self._validate_candidate_response(payload, expected_ids)

    @staticmethod
    def _validate_candidate_response(payload: dict, expected_ids: set[str]) -> dict:
        groups = payload.get("groups") if isinstance(payload, dict) else None
        if not isinstance(groups, dict) or not groups:
            raise ValueError("Equivalence candidate response must contain non-empty groups")

        covered_ids: set[str] = set()
        for group_data in groups.values():
            if not isinstance(group_data, dict):
                raise ValueError("Equivalence candidate groups must be objects")
            raw_ids = group_data.get("leaf_ids")
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError("Every equivalence candidate group must contain leaf_ids")
            for raw_id in raw_ids:
                leaf_id = str(raw_id).strip()
                if leaf_id not in expected_ids:
                    raise ValueError(f"Equivalence candidate response has unknown leaf id: {leaf_id!r}")
                covered_ids.add(leaf_id)

        missing = sorted(expected_ids - covered_ids)
        if missing:
            raise ValueError(f"Equivalence candidate response omitted {len(missing)} leaf ids")
        return groups

    def _candidate_pairs_from_groups(
        self,
        leaf_map: dict[str, TreeNode],
        candidate_groups: dict,
    ) -> list[tuple[str, str]]:
        candidate_pairs: set[tuple[str, str]] = set()
        for _, candidate_data in self._iter_group_items(candidate_groups):
            if not isinstance(candidate_data, dict):
                continue
            raw_ids = candidate_data.get("leaf_ids", [])
            if not isinstance(raw_ids, list):
                continue
            member_ids: set[str] = set()
            for raw_id in raw_ids:
                leaf_id = str(raw_id).strip()
                if leaf_id in leaf_map:
                    member_ids.add(leaf_id)
            candidate_pairs.update(combinations(sorted(member_ids), 2))
        return sorted(candidate_pairs)

    def _judge_candidate_pairs(
        self,
        parent: TreeNode,
        leaf_map: dict[str, TreeNode],
        pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], dict]:
        decisions: dict[tuple[str, str], dict] = {}
        for start in range(0, len(pairs), _PAIRWISE_BATCH_SIZE):
            end = start + _PAIRWISE_BATCH_SIZE
            batch = pairs[start:end]
            prompt, pair_refs = self._pairwise_prompt(parent, leaf_map, batch, start)
            payload = self._call_llm_json(prompt)
            try:
                validated = self._validate_pairwise_response(payload, pair_refs)
            except ValueError as error:
                correction_prompt = (
                    f"{prompt}\n\nYour previous response violated the required schema: {error}. "
                    "Return a corrected JSON object covering every pair_id exactly once."
                )
                payload = self._call_llm_json(correction_prompt, is_retry=True)
                validated = self._validate_pairwise_response(payload, pair_refs)
            decisions.update(validated)
        return decisions

    def _pairwise_prompt(
        self,
        parent: TreeNode,
        leaf_map: dict[str, TreeNode],
        pairs: list[tuple[str, str]],
        offset: int,
    ) -> tuple[str, dict[str, tuple[str, str]]]:
        unique_ids: set[str] = set()
        for left_id, right_id in pairs:
            unique_ids.add(left_id)
            unique_ids.add(right_id)
        leaf_refs = {leaf_id: f"l{index:04d}" for index, leaf_id in enumerate(sorted(unique_ids), start=1)}
        leaf_lines = []
        for leaf_id in sorted(unique_ids):
            leaf = leaf_map[leaf_id]
            description = str(leaf.description or "(no description)")[:_PAIRWISE_DESCRIPTION_LIMIT]
            leaf_lines.append(
                f"- ref: {leaf_refs[leaf_id]}\n"
                f"  name: {leaf.name}\n"
                f"  description: {description}\n"
                f"  select_when: {str(leaf.select_when or '')[:400]}\n"
                f"  dont_select_when: {str(leaf.dont_select_when or '')[:400]}"
            )

        pair_refs: dict[str, tuple[str, str]] = {}
        pair_lines = []
        for index, (left_id, right_id) in enumerate(pairs, start=offset + 1):
            pair_id = f"p{index:05d}"
            pair_refs[pair_id] = (left_id, right_id)
            pair_lines.append(f"- pair_id: {pair_id}; left: {leaf_refs[left_id]}; right: {leaf_refs[right_id]}")

        prompt = EQUIVALENCE_PAIRWISE_PROMPT.format(
            parent_id=parent.id,
            parent_name=parent.name,
            parent_description=parent.description or "(no description)",
            leaf_nodes="\n".join(leaf_lines),
            candidate_pairs="\n".join(pair_lines),
        )
        return prompt, pair_refs

    @staticmethod
    def _validate_pairwise_response(
        payload: dict,
        pair_refs: dict[str, tuple[str, str]],
    ) -> dict[tuple[str, str], dict]:
        raw_decisions = payload.get("decisions") if isinstance(payload, dict) else None
        if not isinstance(raw_decisions, list):
            raise ValueError("Pairwise equivalence response must contain a decisions list")
        decisions: dict[tuple[str, str], dict] = {}
        seen_refs: set[str] = set()
        for item in raw_decisions:
            if not isinstance(item, dict):
                raise ValueError("Pairwise equivalence decisions must be objects")
            pair_id = str(item.get("pair_id") or "").strip()
            if pair_id not in pair_refs or pair_id in seen_refs:
                raise ValueError(f"Pairwise equivalence response has invalid pair id: {pair_id!r}")
            similar = item.get("similar")
            if not isinstance(similar, bool):
                raise ValueError(f"Pairwise equivalence decision {pair_id!r} must use a boolean similar value")
            shared_capability = str(item.get("shared_capability") or "").strip()
            if similar and not shared_capability:
                raise ValueError(f"Similar pair {pair_id!r} must include shared_capability")
            seen_refs.add(pair_id)
            decisions[pair_refs[pair_id]] = {
                "similar": similar,
                "shared_capability": shared_capability,
            }
        missing = sorted(set(pair_refs) - seen_refs)
        if missing:
            raise ValueError(f"Pairwise equivalence response omitted {len(missing)} candidate pairs")
        return decisions

    @staticmethod
    def _equivalent_components(
        member_ids: list[str],
        decisions: dict[tuple[str, str], dict],
    ) -> list[list[str]]:
        components = [[leaf_id] for leaf_id in sorted(member_ids)]
        while True:
            best_merge: tuple[int, tuple[str, ...], int, int] | None = None
            for left_index, left_members in enumerate(components):
                for right_index in range(left_index + 1, len(components)):
                    right_members = components[right_index]
                    if not TreeBuilderEquivalenceMixin._components_are_compatible(
                        left_members,
                        right_members,
                        decisions,
                    ):
                        continue
                    merged = tuple(sorted([*left_members, *right_members]))
                    candidate = (-len(merged), merged, left_index, right_index)
                    if best_merge is None or candidate < best_merge:
                        best_merge = candidate
            if best_merge is None:
                break
            _, merged, left_index, right_index = best_merge
            for index in sorted((left_index, right_index), reverse=True):
                components.pop(index)
            components.append(list(merged))
            components.sort(key=lambda members: members[0])
        return sorted(components, key=lambda members: tuple(members))

    @staticmethod
    def _components_are_compatible(
        left_members: list[str],
        right_members: list[str],
        decisions: dict[tuple[str, str], dict],
    ) -> bool:
        for left_id in left_members:
            for right_id in right_members:
                pair = (left_id, right_id) if left_id < right_id else (right_id, left_id)
                if not decisions.get(pair, {}).get("similar"):
                    return False
        return True

    @staticmethod
    def _component_metadata(
        component: list[str],
        leaf_map: dict[str, TreeNode],
        decisions: dict[tuple[str, str], dict],
    ) -> dict:
        if len(component) == 1:
            leaf = leaf_map[component[0]]
            return {
                "name": leaf.name or leaf.id,
                "description": leaf.description,
                "select_when": leaf.select_when,
                "dont_select_when": leaf.dont_select_when,
            }

        labels: list[str] = []
        members = set(component)
        for (left_id, right_id), decision in decisions.items():
            label = str(decision.get("shared_capability") or "").strip()
            if not decision.get("similar") or not label:
                continue
            if left_id in members and right_id in members:
                labels.append(label)
        if labels:
            counts = Counter(labels)
            name = min(counts, key=lambda item: (-counts[item], len(item), item.casefold(), item))
        else:
            name = leaf_map[component[0]].name or component[0]

        description = f"Skill implementations with the shared capability: {name}."
        select_when = f"Route here when the user needs {name}."
        dont_select_when = "Do not route here when the primary requested capability is different."
        return {
            "name": name,
            "description": description,
            "select_when": select_when,
            "dont_select_when": dont_select_when,
        }

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
