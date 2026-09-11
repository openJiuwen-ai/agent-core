from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from openjiuwen.symphony.retrieval.build.tree.builder import TreeBuilder
from openjiuwen.symphony.retrieval.build.tree.schema import Skill, TreeNode


def _skill(skill_id: str, *, name: str | None = None, description: str = "") -> Skill:
    return Skill(id=skill_id, name=name or skill_id, description=description or skill_id, path="test")


class TreeBuilderPostprocessTests(unittest.TestCase):
    def _builder(self) -> TreeBuilder:
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        builder = TreeBuilder(
            skills_dir=Path(tmp_dir.name),
            model="test-tree-model",
            client=cast(Any, object()),
        )
        builder.settings.postprocess_min_skills = 2
        builder.settings.equiv_allow_singleton_groups = False
        return builder

    def test_rebalance_child_assignments_moves_skills_between_leaf_siblings(self) -> None:
        builder = self._builder()
        data_leaf = TreeNode(
            id="data-processing",
            name="Data Processing",
            description="Structured analysis.",
            skills=[_skill("sql-reporting"), _skill("web-crawler")],
        )
        automation_leaf = TreeNode(
            id="automation",
            name="Automation",
            description="Workflow automation.",
            skills=[_skill("browser-automation"), _skill("workflow-builder")],
        )
        parent = TreeNode(id="root-branch", name="Root Branch", children=[data_leaf, automation_leaf])
        assignments = {
            "sql-reporting": "data-processing",
            "web-crawler": "automation",
            "browser-automation": "automation",
            "workflow-builder": "automation",
        }

        def classify_skills(skills: list[dict], groups: dict, verbose: bool = False) -> dict:
            del skills, groups, verbose
            return assignments

        def validate_and_recover(
            skills: list[dict],
            groups: dict,
            current_assignments: dict,
            verbose: bool = False,
        ) -> dict:
            del skills, groups, verbose
            return current_assignments

        builder.grouping_engine.classify_skills = classify_skills
        builder.grouping_engine.validate_and_recover = validate_and_recover

        moved = getattr(builder, "_rebalance_child_assignments")(parent)

        self.assertEqual(moved, 1)
        self.assertEqual([skill.id for skill in data_leaf.skills], ["sql-reporting"])
        self.assertEqual(
            sorted(skill.id for skill in automation_leaf.skills),
            ["browser-automation", "web-crawler", "workflow-builder"],
        )

    def test_rebalance_child_assignments_routes_into_existing_subtree(self) -> None:
        builder = self._builder()
        analysis_leaf = TreeNode(
            id="analysis",
            name="Analysis",
            description="Analysis tools.",
            skills=[_skill("sql-reporting"), _skill("web-crawler")],
        )
        browser_leaf = TreeNode(
            id="browser-automation",
            name="Browser Automation",
            skills=[_skill("playwright-local")],
        )
        workflow_leaf = TreeNode(
            id="workflow-automation",
            name="Workflow Automation",
            skills=[_skill("cron-runner")],
        )
        automation_branch = TreeNode(id="automation", name="Automation", children=[browser_leaf, workflow_leaf])
        parent = TreeNode(id="root-branch", name="Root Branch", children=[analysis_leaf, automation_branch])
        assignments = {
            "sql-reporting": "analysis",
            "web-crawler": "automation",
            "playwright-local": "automation",
            "cron-runner": "automation",
        }

        def classify_skills(skills: list[dict], groups: dict, verbose: bool = False) -> dict:
            del skills, groups, verbose
            return assignments

        def validate_and_recover(
            skills: list[dict],
            groups: dict,
            current_assignments: dict,
            verbose: bool = False,
        ) -> dict:
            del skills, groups, verbose
            return current_assignments

        def classify_skills_single(
            skills: list[dict],
            groups: dict,
            verbose: bool = False,
            is_retry: bool = False,
        ) -> dict:
            del verbose, is_retry
            target = "browser-automation" if skills[0]["id"] == "web-crawler" else next(iter(groups.keys()))
            return {skills[0]["id"]: target}

        builder.grouping_engine.classify_skills = classify_skills
        builder.grouping_engine.validate_and_recover = validate_and_recover
        builder.grouping_engine.classify_skills_single = classify_skills_single

        moved = getattr(builder, "_rebalance_child_assignments")(parent)

        self.assertEqual(moved, 1)
        self.assertEqual([skill.id for skill in analysis_leaf.skills], ["sql-reporting"])
        self.assertEqual(sorted(skill.id for skill in browser_leaf.skills), ["playwright-local", "web-crawler"])
        self.assertEqual([skill.id for skill in workflow_leaf.skills], ["cron-runner"])

    def test_repair_small_leaf_children_merges_singleton_group(self) -> None:
        builder = self._builder()
        singleton_leaf = TreeNode(id="singleton", name="Singleton", skills=[_skill("web-crawler")])
        stable_leaf = TreeNode(
            id="automation",
            name="Automation",
            skills=[_skill("browser-automation"), _skill("workflow-builder")],
        )
        data_leaf = TreeNode(
            id="data-processing",
            name="Data Processing",
            skills=[_skill("sql-reporting"), _skill("table-cleanup")],
        )
        parent = TreeNode(id="root-branch", name="Root Branch", children=[singleton_leaf, stable_leaf, data_leaf])

        def classify_skills_single(
            skills: list[dict],
            groups: dict,
            verbose: bool = False,
            is_retry: bool = False,
        ) -> dict:
            del skills, groups, verbose, is_retry
            return {"web-crawler": "automation"}

        builder.grouping_engine.classify_skills_single = classify_skills_single

        reassigned = getattr(builder, "_repair_small_leaf_children")(parent)

        self.assertEqual(reassigned, 1)
        self.assertEqual(sorted(child.id for child in parent.children), ["automation", "data-processing"])
        self.assertEqual(
            sorted(skill.id for skill in stable_leaf.skills),
            ["browser-automation", "web-crawler", "workflow-builder"],
        )

    def test_equivalence_group_id_prefers_semantic_name_and_skips_root_children(self) -> None:
        builder = TreeBuilder.__new__(TreeBuilder)
        group_id = getattr(builder, "_build_equivalence_group_id")(
            group_id="G1",
            group_name="Academic Literature Search",
            fallback="search-research-equiv-1",
        )
        called = {"value": False}

        def fail_if_called(parent_node, second_leaf_node, verbose=False):
            called["value"] = True
            raise AssertionError("root-level equivalence regrouping should be skipped")

        setattr(builder, "_split_second_leaf_node_into_equiv_groups", fail_if_called)
        root = TreeNode(
            id="root",
            name="Root",
            children=[
                TreeNode(
                    id="search-research",
                    name="Search & Research",
                    children=[
                        TreeNode(id="left-leaf", name="Left Leaf", skills=[]),
                        TreeNode(id="right-leaf", name="Right Leaf", skills=[]),
                    ],
                )
            ],
        )

        getattr(builder, "_normalize_to_equivalence_groups")(root)

        self.assertEqual(group_id, "academic-literature-search")
        self.assertFalse(called["value"])
        self.assertEqual([node.id for node in root.children], ["search-research"])


if __name__ == "__main__":
    unittest.main()
