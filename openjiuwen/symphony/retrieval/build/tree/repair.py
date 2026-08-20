from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.symphony.shared.rich_compat import Console, Panel

from .schema import FIXED_ROOT_CATEGORIES, TreeNode

if TYPE_CHECKING:
    from .builder import TreeBuilder


console = Console()


class TreeRepairEngine:
    """Owns post-build repair passes and equivalence regrouping."""

    def __init__(self, builder: "TreeBuilder") -> None:
        self._builder = builder

    def postprocess_tree(self, root: TreeNode, verbose: bool = False) -> None:
        total_reassignments = 0
        for repair_pass in range(1, self._builder.settings.postprocess_max_passes + 1):
            moved = self.postprocess_node(root, verbose=verbose)
            total_reassignments += moved
            if verbose:
                console.print(f"[dim]  Post-process pass {repair_pass}: reassigned {moved} skills[/dim]")
            if moved <= 0:
                break
        if total_reassignments > 0:
            console.print(
                Panel(
                    f"[bold green]Post-process repaired {total_reassignments} "
                    "misplaced skill assignments.[/bold green]",
                    title="[bold green]Tree Repair[/bold green]",
                    border_style="green",
                )
            )

    def postprocess_node(self, node: TreeNode, verbose: bool = False) -> int:
        if node.is_leaf:
            return 0
        moved = 0
        for child in list(node.children):
            moved += self.postprocess_node(child, verbose=verbose)
        moved += self.rebalance_child_assignments(node, verbose=verbose)
        return moved

    def rebalance_child_assignments(self, node: TreeNode, verbose: bool = False) -> int:
        builder = self._builder
        if len(node.children) < 2:
            return 0
        groups = {
            child.id: {
                "name": child.name,
                "description": child.description,
                "select_when": child.select_when,
                "dont_select_when": child.dont_select_when,
            }
            for child in node.children
        }
        if len(groups) < 2:
            return 0

        skill_entries: list[dict] = []
        skill_data_by_id: dict[str, dict] = {}
        source_leaf_by_skill_id: dict[str, TreeNode] = {}
        source_child_by_skill_id: dict[str, str] = {}

        for child in node.children:
            for leaf_node, skill_data in self.collect_subtree_skill_locations(child):
                skill_id = str(skill_data.get("id", "")).strip()
                if not skill_id:
                    continue
                skill_entries.append(skill_data)
                skill_data_by_id[skill_id] = skill_data
                source_leaf_by_skill_id[skill_id] = leaf_node
                source_child_by_skill_id[skill_id] = child.id

        if len(skill_entries) < builder.settings.postprocess_min_skills:
            return 0

        assignments = builder.grouping_engine.classify_skills(skill_entries, groups, verbose=False)
        assignments = builder.grouping_engine.validate_and_recover(
            skill_entries,
            groups,
            assignments,
            verbose=False,
        )

        child_by_id = {child.id: child for child in node.children}
        moves: list[tuple[str, str]] = []
        for skill_id, current_child_id in source_child_by_skill_id.items():
            target_child_id = assignments.get(skill_id)
            if not target_child_id or target_child_id == current_child_id or target_child_id not in child_by_id:
                continue
            moves.append((skill_id, target_child_id))

        if not moves:
            return 0

        for skill_id, target_child_id in moves:
            source_leaf = source_leaf_by_skill_id.get(skill_id)
            skill_data = skill_data_by_id.get(skill_id)
            target_child = child_by_id.get(target_child_id)
            if source_leaf is None or skill_data is None or target_child is None:
                continue
            source_leaf.skills = [skill for skill in source_leaf.skills if skill.id != skill_id]
            builder.operations.insert_skill_into_subtree(target_child, skill_data)

        removed_empty = builder.operations.prune_empty_children(node)
        reassigned_tiny = builder.operations.repair_small_leaf_children(node)
        if removed_empty or reassigned_tiny:
            builder.operations.prune_empty_children(node)
            if len(node.children) >= 2:
                builder.expansion_engine.rewrite_node_label_after_singleton(
                    node,
                    builder.expansion_engine.existing_child_groups(node.children),
                    verbose=verbose,
                )

        total_moved = len(moves) + reassigned_tiny
        if verbose and total_moved > 0:
            console.print(
                f"[dim]  Post-process repaired '{node.id}': moved={len(moves)}, "
                f"tiny_group_reassigned={reassigned_tiny}, removed_empty={removed_empty}[/dim]"
            )
        return total_moved

    def collect_subtree_skill_locations(self, node: TreeNode) -> list[tuple[TreeNode, dict]]:
        if node.is_leaf:
            return [(node, skill.to_dict(include_content=True)) for skill in node.skills]
        results: list[tuple[TreeNode, dict]] = []
        for child in node.children:
            results.extend(self.collect_subtree_skill_locations(child))
        return results

    def collect_subtree_skill_dicts(self, node: TreeNode) -> list[dict]:
        return [skill_data for _, skill_data in self.collect_subtree_skill_locations(node)]

    def normalize_to_equivalence_groups(self, root: TreeNode, verbose: bool = False) -> None:
        configured_terminal_paths = self._configured_terminal_paths(root.id)
        if configured_terminal_paths:
            self._normalize_configured_scopes(
                root,
                path=(root.id,),
                terminal_paths=configured_terminal_paths,
                verbose=verbose,
            )
            return
        if root.is_leaf:
            return
        updated_children: list[TreeNode] = []
        split_count = 0
        for child in list(root.children):
            self.normalize_to_equivalence_groups(child, verbose=verbose)
            if root.id != "root" and self.is_second_leaf_node(child):
                replacement_nodes = self.split_second_leaf_node_into_equiv_groups(root, child, verbose=verbose)
                updated_children.extend(replacement_nodes)
                if len(replacement_nodes) > 1 or replacement_nodes[0].id != child.id:
                    split_count += 1
            else:
                updated_children.append(child)
        root.children = updated_children
        if verbose and split_count > 0:
            console.print(
                f"[dim]  Equivalence regrouping updated {split_count} second-leaf nodes under '{root.id}'[/dim]"
            )

    def _configured_terminal_paths(self, root_id: str) -> set[tuple[str, ...]]:
        config = getattr(self._builder, "config", None)
        categories = getattr(config, "root_categories", None) or FIXED_ROOT_CATEGORIES
        terminal_paths: set[tuple[str, ...]] = set()

        def visit(entries: dict, parent_path: tuple[str, ...]) -> None:
            for category_id, raw_payload in entries.items():
                path = (*parent_path, str(category_id))
                payload = raw_payload if isinstance(raw_payload, dict) else {}
                children = payload.get("children")
                if isinstance(children, dict) and children:
                    visit(children, path)
                else:
                    terminal_paths.add(path)

        visit(categories, (str(root_id),))
        return terminal_paths

    def _normalize_configured_scopes(
        self,
        node: TreeNode,
        *,
        path: tuple[str, ...],
        terminal_paths: set[tuple[str, ...]],
        verbose: bool,
    ) -> None:
        if path in terminal_paths:
            self._normalize_configured_scope(node, verbose=verbose)
            return
        for child in node.children:
            self._normalize_configured_scopes(
                child,
                path=(*path, child.id),
                terminal_paths=terminal_paths,
                verbose=verbose,
            )

    def _normalize_configured_scope(self, scope: TreeNode, *, verbose: bool) -> None:
        builder = self._builder
        skills = sorted(scope.collect_all_skills(), key=lambda item: item.id)
        if not skills:
            return
        skill_leaves = [
            TreeNode(
                id=skill.id,
                name=skill.name,
                description=skill.description or skill.source_description,
                select_when=skill.select_when,
                dont_select_when=skill.dont_select_when,
                skills=[skill],
                depth=scope.depth + 1,
                parent_id=scope.id,
            )
            for skill in skills
        ]
        if len(skill_leaves) == 1:
            only_leaf = skill_leaves[0]
            normalized_groups = [
                {
                    "id": only_leaf.id,
                    "name": only_leaf.name,
                    "description": only_leaf.description,
                    "select_when": only_leaf.select_when,
                    "dont_select_when": only_leaf.dont_select_when,
                    "leaf_nodes": [only_leaf],
                }
            ]
        else:
            groups = builder.operations.discover_equivalence_groups(scope, skill_leaves, verbose=verbose)
            if not groups:
                return
            normalized_groups = builder.operations.normalize_equivalence_groups(skill_leaves, groups)
            if not normalized_groups:
                return

        expected_ids = {skill.id for skill in skills}
        actual_ids: set[str] = set()
        for group in normalized_groups:
            for leaf in group.get("leaf_nodes", []):
                actual_ids.update(skill.id for skill in leaf.skills)
        if actual_ids != expected_ids:
            return

        replacement_nodes: list[TreeNode] = []
        used_ids: set[str] = set()
        for index, group in enumerate(normalized_groups, start=1):
            base_id = builder.operations.build_equivalence_group_id(
                group_id=str(group.get("id") or "").strip(),
                group_name=str(group.get("name") or "").strip(),
                fallback=f"{scope.id}-equiv-{index}",
            )
            group_id = base_id
            suffix = 2
            while group_id in used_ids:
                group_id = f"{base_id}-{suffix}"
                suffix += 1
            used_ids.add(group_id)
            group_skills = [skill for leaf in group.get("leaf_nodes", []) for skill in leaf.skills]
            for skill in group_skills:
                skill.path = group_id
            replacement_nodes.append(
                TreeNode(
                    id=group_id,
                    name=str(group.get("name") or group_id),
                    description=str(group.get("description") or scope.description),
                    select_when=str(group.get("select_when") or ""),
                    dont_select_when=str(group.get("dont_select_when") or ""),
                    skills=group_skills,
                    depth=scope.depth + 1,
                    parent_id=scope.id,
                )
            )
        scope.skills = []
        scope.children = sorted(replacement_nodes, key=lambda item: item.id)
        if verbose:
            console.print(
                f"[dim]  Equivalence regrouping created {len(replacement_nodes)} groups under '{scope.id}'[/dim]"
            )

    @staticmethod
    def is_second_leaf_node(node: TreeNode) -> bool:
        if not node.children:
            return False
        return all(child.is_leaf for child in node.children)

    def split_second_leaf_node_into_equiv_groups(
        self,
        parent_node: TreeNode,
        second_leaf_node: TreeNode,
        verbose: bool = False,
    ) -> list[TreeNode]:
        builder = self._builder
        leaf_children = list(second_leaf_node.children)
        if len(leaf_children) <= 1:
            return [second_leaf_node]
        groups = builder.operations.discover_equivalence_groups(
            second_leaf_node,
            leaf_children,
            verbose=verbose,
        )
        if not groups:
            return [second_leaf_node]
        normalized_groups = builder.operations.normalize_equivalence_groups(leaf_children, groups)
        if len(normalized_groups) <= 1:
            only_group = normalized_groups[0]
            second_leaf_node.name = only_group.get("name", second_leaf_node.name)
            second_leaf_node.description = only_group.get("description", second_leaf_node.description)
            second_leaf_node.select_when = only_group.get("select_when", second_leaf_node.select_when)
            second_leaf_node.dont_select_when = only_group.get("dont_select_when", second_leaf_node.dont_select_when)
            return [second_leaf_node]

        used_ids = {child.id for child in parent_node.children}
        replacement_nodes: list[TreeNode] = []
        for idx, group in enumerate(normalized_groups, start=1):
            base_id = builder.operations.build_equivalence_group_id(
                group_id=str(group.get("id") or "").strip(),
                group_name=str(group.get("name") or "").strip(),
                fallback=f"{second_leaf_node.id}-equiv-{idx}",
            )
            group_id = base_id
            suffix = 2
            while group_id in used_ids:
                group_id = f"{base_id}-{suffix}"
                suffix += 1
            used_ids.add(group_id)
            new_node = TreeNode(
                id=group_id,
                name=str(group.get("name") or group_id),
                description=str(group.get("description") or second_leaf_node.description),
                select_when=str(group.get("select_when") or ""),
                dont_select_when=str(group.get("dont_select_when") or ""),
                depth=second_leaf_node.depth,
                parent_id=second_leaf_node.parent_id,
            )
            for leaf in group.get("leaf_nodes", []):
                leaf.parent_id = new_node.id
                leaf.depth = new_node.depth + 1
                new_node.children.append(leaf)
            replacement_nodes.append(new_node)
        if verbose:
            console.print(
                f"[dim]  Split '{second_leaf_node.id}' into {len(replacement_nodes)} equivalence groups[/dim]"
            )
        return replacement_nodes
