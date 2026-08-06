from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openjiuwen.symphony.retrieval.build.tree import TreeBuildConfig
from openjiuwen.symphony.retrieval.build.tree.builder import TreeBuilder, build_tree
from openjiuwen.symphony.retrieval.build.tree.expansion import TreeExpansionEngine
from openjiuwen.symphony.retrieval.build.tree.grouping import TreeGroupingEngine
from openjiuwen.symphony.retrieval.build.tree.schema import DynamicTreeConfig, TreeNode, normalize_root_categories
from openjiuwen.symphony.retrieval.build.workflows.artifacts import BuildConfig, resolve_build_config


class TreeBuilderConfigTests(unittest.TestCase):
    def test_skill_profiles_are_disabled_by_default(self) -> None:
        self.assertFalse(TreeBuildConfig().skill_profiles_enabled)
        self.assertFalse(BuildConfig().tree_skill_profiles_enabled)
        self.assertFalse(resolve_build_config().tree_skill_profiles_enabled)

    def test_dynamic_tree_config_derives_thresholds_from_branching_factor(self) -> None:
        config = DynamicTreeConfig(branching_factor=10, max_depth=4)

        self.assertEqual(config.max_skills_per_node, 15)
        self.assertEqual(config.expand_threshold, 7)
        self.assertEqual(config.early_stop_skill_count, 17)
        self.assertEqual(config.lazy_split_threshold, 19)
        self.assertEqual(config.classification_batch_size, 60)
        self.assertEqual(config.structure_sample_size, 120)

    def test_build_tree_keeps_output_path_as_second_positional_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skills_dir = root / "skills"
            skills_dir.mkdir()
            output_path = root / "tree.yaml"

            result = build_tree(skills_dir, output_path, client=object(), model="fake-tree-model")

            self.assertEqual(result, {})

    def test_tree_builder_keeps_config_as_third_positional_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            skills_dir = root / "skills"
            skills_dir.mkdir()
            output_path = root / "tree.yaml"
            config = DynamicTreeConfig(branching_factor=3, max_depth=2)

            builder = TreeBuilder(skills_dir, output_path, config, client=object(), model="fake-tree-model")

            self.assertEqual(builder.config.branching_factor, 3)

    def test_normalize_root_categories_accepts_string_dict_and_nested_children(self) -> None:
        categories = normalize_root_categories(
            [
                "Research",
                {
                    "id": "dev-tools",
                    "name": "Development",
                    "description": "Coding workflows.",
                    "children": [
                        {"id": "frontend", "name": "Frontend"},
                        {"id": "backend", "name": "Backend", "select_when": "Use for APIs."},
                    ],
                },
            ]
        )

        assert categories is not None
        self.assertEqual(categories["research"]["name"], "Research")
        self.assertEqual(categories["dev-tools"]["description"], "Coding workflows.")
        self.assertEqual(categories["dev-tools"]["children"]["frontend"]["description"], "Skills related to frontend.")
        self.assertEqual(categories["dev-tools"]["children"]["backend"]["select_when"], "Use for APIs.")

    def test_normalize_root_categories_rejects_invalid_and_duplicate_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a list"):
            normalize_root_categories("Research")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            normalize_root_categories(["Research", {"id": "research", "name": "Another Research"}])
        with self.assertRaisesRegex(ValueError, "children must be"):
            normalize_root_categories([{"id": "dev", "children": "frontend"}])


class _FakeTreeBuilder:
    def __init__(self) -> None:
        self.config = DynamicTreeConfig(
            branching_factor=0,
            max_depth=4,
            root_categories={
                "development": {
                    "name": "Development",
                    "description": "Development skills.",
                    "children": {
                        "frontend": {"name": "Frontend", "description": "Frontend skills."},
                        "backend": {"name": "Backend", "description": "Backend skills."},
                    },
                }
            },
        )
        self.state = SimpleNamespace(progress=None, progress_task=None)
        self.settings = SimpleNamespace(
            equiv_allow_singleton_groups=True,
            deterministic_prompts=True,
        )
        self.grouping_engine = self
        self.operations = SimpleNamespace(assign_skills_to_leaf=self.assign_skills_to_leaf)
        self.classification_rounds: list[list[str]] = []

    def classify_skills(self, skills: list[dict], groups: dict, verbose: bool = False) -> dict:
        del verbose
        self.classification_rounds.append(list(groups.keys()))
        if set(groups) == {"development"}:
            return {skill["id"]: "development" for skill in skills}
        return {skill["id"]: ("frontend" if "frontend" in skill["id"] else "backend") for skill in skills}

    @staticmethod
    def validate_and_recover(
        skills: list[dict],
        groups: dict,
        assignments: dict,
        verbose: bool = False,
    ) -> dict:
        del skills, groups, verbose
        return assignments

    def build_groups_from_assignments(self, groups: dict, assignments: dict) -> dict:
        return TreeGroupingEngine(self).build_groups_from_assignments(groups, assignments)

    @staticmethod
    def split_skills(skills: list[dict], parent_context, verbose: bool = False) -> dict:
        del skills, parent_context, verbose
        raise AssertionError("configured categories should not call dynamic group discovery")

    @staticmethod
    def assign_skills_to_leaf(node: TreeNode, skills: list[dict]) -> None:
        del node, skills


class TreeExpansionConfigTests(unittest.TestCase):
    def test_nested_configured_categories_are_used_before_dynamic_split(self) -> None:
        builder = _FakeTreeBuilder()
        engine = TreeExpansionEngine(builder)
        skills = [{"id": "frontend-skill"}, {"id": "backend-skill"}]

        root = TreeNode(id="root", name="Root")
        root_groups = engine.process_node(node=root, skills=skills, depth=0, parent_context=None)
        development_groups = engine.process_node(
            node=root_groups[0].node,
            skills=root_groups[0].skills,
            depth=1,
            parent_context={
                "name": root_groups[0].node.name,
                "description": root_groups[0].node.description,
            },
        )

        self.assertEqual([child.node.id for child in root_groups], ["development"])
        self.assertEqual(set(root_groups[0].configured_children or {}), {"frontend", "backend"})
        self.assertEqual([child.node.id for child in development_groups], ["backend", "frontend"])
        self.assertEqual(builder.classification_rounds, [["development"], ["frontend", "backend"]])


if __name__ == "__main__":
    unittest.main()
