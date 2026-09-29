from __future__ import annotations

from .expansion import TreeExpansionEngine as _TreeExpansionEngine
from .mixin import TreeBuilderMixin
from .repair import TreeRepairEngine as _TreeRepairEngine
from .schema import TreeNode
from .types import ChildGroup as _ChildGroup


class TreeBuilderExpansionRepairMixin(TreeBuilderMixin):
    def _root_group_definitions(self) -> dict[str, dict[str, str]]:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        return expansion_engine.root_group_definitions()

    def _create_child_node(
        self,
        *,
        parent: TreeNode,
        group_id: str,
        group_data: dict,
        depth: int,
    ) -> TreeNode:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        return expansion_engine.create_child_node(parent=parent, group_id=group_id, group_data=group_data, depth=depth)

    def _build_children_from_groups(
        self,
        node: TreeNode,
        skills: list[dict],
        groups: dict,
        depth: int,
        verbose: bool = False,
    ) -> list[_ChildGroup]:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        return expansion_engine.build_children_from_groups(node, skills, groups, depth, verbose)

    def _reassign_skills_to_children(
        self,
        unassigned_skills: list[dict],
        children_to_process: list[_ChildGroup],
    ) -> tuple[int, list[dict]]:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        return expansion_engine.reassign_skills_to_children(unassigned_skills, children_to_process)

    def _assign_unassigned_skills(
        self,
        *,
        node: TreeNode,
        all_skills: list[dict],
        remaining_skill_map: dict[str, dict],
        children_to_process: list[_ChildGroup],
        verbose: bool = False,
    ) -> None:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        expansion_engine.assign_unassigned_skills(
            node=node,
            all_skills=all_skills,
            remaining_skill_map=remaining_skill_map,
            children_to_process=children_to_process,
            verbose=verbose,
        )

    def _rewrite_node_label_after_singleton(
        self,
        node: TreeNode,
        children_to_process: list[_ChildGroup],
        verbose: bool = False,
    ) -> None:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        expansion_engine.rewrite_node_label_after_singleton(node, children_to_process, verbose)

    def _postprocess_tree(self, root: TreeNode, verbose: bool = False) -> None:
        repair_engine = getattr(self, "repair_engine", None) or _TreeRepairEngine(self._tree_builder())
        repair_engine.postprocess_tree(root, verbose)

    def _postprocess_node(self, node: TreeNode, verbose: bool = False) -> int:
        repair_engine = getattr(self, "repair_engine", None) or _TreeRepairEngine(self._tree_builder())
        return repair_engine.postprocess_node(node, verbose)

    def _rebalance_child_assignments(self, node: TreeNode, verbose: bool = False) -> int:
        repair_engine = getattr(self, "repair_engine", None) or _TreeRepairEngine(self._tree_builder())
        return repair_engine.rebalance_child_assignments(node, verbose)

    def _collect_subtree_skill_locations(self, node: TreeNode) -> list[tuple[TreeNode, dict]]:
        repair_engine = getattr(self, "repair_engine", None) or _TreeRepairEngine(self._tree_builder())
        return repair_engine.collect_subtree_skill_locations(node)

    def _collect_subtree_skill_dicts(self, node: TreeNode) -> list[dict]:
        repair_engine = getattr(self, "repair_engine", None) or _TreeRepairEngine(self._tree_builder())
        return repair_engine.collect_subtree_skill_dicts(node)

    def _existing_child_groups(self, children: list[TreeNode]) -> list[_ChildGroup]:
        expansion_engine = getattr(self, "expansion_engine", None) or _TreeExpansionEngine(self._tree_builder())
        return expansion_engine.existing_child_groups(children)

    def _choose_child_for_skill(self, skill_data: dict, children: list[TreeNode]) -> TreeNode:
        """Choose the best direct child for a skill, falling back to the largest subtree."""
        child_by_id = {child.id: child for child in children}
        groups = {
            child.id: {
                "name": child.name,
                "description": child.description,
                "select_when": child.select_when,
                "dont_select_when": child.dont_select_when,
            }
            for child in children
        }
        assignment = self._classify_skills_single(
            [skill_data],
            groups,
            verbose=False,
            is_retry=True,
        )
        child_id = assignment.get(str(skill_data.get("id", "")).strip())
        if child_id in child_by_id:
            return child_by_id[child_id]
        return max(children, key=lambda child: child.count_all_skills())

    def _insert_skill_into_subtree(self, node: TreeNode, skill_data: dict) -> None:
        """Insert a skill into the best-fitting location inside an existing subtree."""
        skill_id = str(skill_data.get("id", "")).strip()
        if not skill_id:
            return

        if node.is_leaf or not node.children:
            if any(skill.id == skill_id for skill in node.skills):
                return
            node.skills.append(self._skill_from_data(skill_data, path=node.id))
            return

        target_child = self._choose_child_for_skill(skill_data, node.children)
        self._insert_skill_into_subtree(target_child, skill_data)

    def _prune_empty_children(self, node: TreeNode) -> int:
        """Remove empty child subtrees after skill moves."""
        removed = 0
        kept_children: list[TreeNode] = []
        for child in node.children:
            removed += self._prune_empty_children(child)
            if child.children:
                if child.count_all_skills() <= 0:
                    removed += 1
                    continue
            elif not child.skills:
                removed += 1
                continue
            kept_children.append(child)
        node.children = kept_children
        return removed

    def _repair_small_leaf_children(self, node: TreeNode) -> int:
        """
        Merge direct leaf children with <2 skills back into their siblings.
        This keeps post-process from leaving obviously unstable tiny groups behind.
        """
        if self.settings.equiv_allow_singleton_groups:
            return 0
        if len(node.children) < 2:
            return 0

        tiny_leaf_children = [child for child in node.children if child.is_leaf and 0 < len(child.skills) < 2]
        if not tiny_leaf_children:
            return 0

        remaining_children = [child for child in node.children if child not in tiny_leaf_children]
        if len(remaining_children) < 2:
            return 0

        reassigned_skills = [self._skill_to_data(skill) for child in tiny_leaf_children for skill in child.skills]
        node.children = remaining_children
        for skill_data in reassigned_skills:
            target_child = self._choose_child_for_skill(skill_data, node.children)
            self._insert_skill_into_subtree(target_child, skill_data)
        return len(reassigned_skills)
