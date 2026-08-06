from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openjiuwen.symphony.retrieval.build.io.config_loader import parse_json_or_yaml, read_config_text
from openjiuwen.symphony.retrieval.build.tree.root_categories import (
    load_tree_root_categories,
    resolve_tree_root_categories,
)
from openjiuwen.symphony.retrieval.build.tree.schema import (
    MultiLevelSearchResult,
    SearchStep,
    Skill,
    TreeNode,
    parse_json_from_response,
)
from openjiuwen.symphony.retrieval.build.tree.visualizer import generate_html


class TreeSchemaVisualizerConfigTests(unittest.TestCase):
    def test_tree_node_round_trips_recursive_and_capability_shapes(self) -> None:
        recursive = {
            "id": "root",
            "name": "Root",
            "children": [
                {
                    "id": "branch",
                    "name": "Branch",
                    "select_when": "Use branch.",
                    "skills": [
                        {
                            "id": "alpha",
                            "name": "Alpha",
                            "description": "Alpha desc",
                            "skill_path": "/tmp/alpha/SKILL.md",
                            "content": "Alpha body",
                            "select_when": "Use alpha.",
                            "dont_select_when": "Avoid beta.",
                            "source_description": "Raw alpha.",
                            "github_url": "https://example.invalid/alpha",
                            "stars": 5,
                            "is_official": True,
                            "author": "tester",
                        }
                    ],
                }
            ],
        }
        node = TreeNode.from_recursive_tree(recursive)
        capability = TreeNode.from_capability_tree(
            {
                "domains": {
                    "dev": {
                        "name": "Development",
                        "description": "Dev domain",
                        "types": {
                            "testing": {
                                "name": "Testing",
                                "skills": [{"id": "unit-test", "name": "Unit Test", "description": "Writes tests."}],
                            }
                        },
                    }
                }
            }
        )

        node.children[0].pending_split = True
        self.assertEqual(node.count_all_skills(), 1)
        self.assertEqual([skill.id for skill in node.collect_all_skills()], ["alpha"])
        self.assertEqual([leaf.id for leaf in node.get_leaf_nodes()], ["branch"])
        self.assertEqual([pending.id for pending in node.get_pending_split_nodes()], ["branch"])
        node.clear_pending_splits()
        self.assertEqual(node.get_pending_split_nodes(), [])
        self.assertIn("select_when", node.to_dict()["children"][0])
        self.assertEqual(capability.children[0].children[0].skills[0].path, "dev/testing")

    def test_skill_search_result_and_json_response_parsing(self) -> None:
        skill = Skill(
            id="alpha",
            name="Alpha",
            description="Alpha desc",
            skill_path="/tmp/alpha",
            content="body",
            select_when="Use alpha.",
        )
        step = SearchStep(level=1, node_id="root", options=["a", "b"], selected=["a"], is_parallel=True)
        result = MultiLevelSearchResult(
            query="alpha",
            selected_skills=[skill.to_dict()],
            steps=[step],
            llm_calls=2,
            parallel_rounds=1,
        )

        self.assertNotIn("content", skill.to_dict(include_content=False))
        self.assertEqual(result.steps[0].selected, ["a"])
        self.assertEqual(parse_json_from_response('```json\n{"ok": true}\n```'), {"ok": True})
        self.assertEqual(parse_json_from_response('prefix {"value": [1, 2]} suffix'), {"value": [1, 2]})
        self.assertEqual(parse_json_from_response("prefix [1, 2] suffix"), [1, 2])
        self.assertEqual(parse_json_from_response(object(), default=[]), [])
        self.assertEqual(parse_json_from_response("{bad}", default={"fallback": True}), {"fallback": True})

    def test_config_loader_and_root_categories_support_json_yaml_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path = root / "roots.json"
            yaml_path = root / "roots.yaml"
            bad_path = root / "bad.yaml"
            other_path = root / "other.json"
            json_path.write_text('{"root_categories": [{"id": "dev", "name": "Development"}]}', encoding="utf-8")
            yaml_path.write_text("tree_root_categories:\n  - id: research\n    name: Research\n", encoding="utf-8")
            bad_path.write_text("root_categories: not-a-list\n", encoding="utf-8")
            other_path.write_text('{"other": []}', encoding="utf-8")

            self.assertEqual(parse_json_or_yaml('["Research"]', source="inline"), ["Research"])
            self.assertEqual(read_config_text(json_path, description="roots"), json_path.read_text(encoding="utf-8"))
            self.assertEqual(load_tree_root_categories(json_path)[0]["id"], "dev")
            self.assertEqual(resolve_tree_root_categories(yaml_path)[0]["id"], "research")
            self.assertEqual(resolve_tree_root_categories(["Research"]), ["Research"])
            self.assertIsNone(resolve_tree_root_categories(None))
            with self.assertRaisesRegex(ValueError, "must be a list"):
                load_tree_root_categories(bad_path)
            with self.assertRaisesRegex(ValueError, "path is empty"):
                read_config_text("", description="roots")
            with self.assertRaisesRegex(FileNotFoundError, "file not found"):
                read_config_text(root / "missing.yaml", description="roots")
            with self.assertRaisesRegex(ValueError, "expected a root category"):
                load_tree_root_categories(other_path)

    def test_visualizer_generates_recursive_and_legacy_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recursive_output = root / "recursive.html"
            legacy_output = root / "legacy.html"
            generate_html(
                {
                    "name": "Skill Root",
                    "id": "root",
                    "children": [
                        {
                            "name": "Development",
                            "id": "dev",
                            "description": "Dev tools",
                            "children": [
                                {
                                    "type": "leaf",
                                    "worker_id": "alpha",
                                    "name": "Alpha",
                                    "description": "Alpha desc",
                                    "github_url": "https://example.invalid/alpha",
                                    "stars": 5,
                                    "author": "tester",
                                }
                            ],
                        }
                    ],
                },
                recursive_output,
            )
            generate_html(
                {
                    "domains": {
                        "office": {
                            "name": "Office",
                            "description": "Office domain",
                            "types": {
                                "docs": {
                                    "name": "Documents",
                                    "skills": [
                                        {
                                            "id": "doc-writer",
                                            "name": "Doc Writer",
                                            "description": "Writes docs",
                                            "author": "tester",
                                        }
                                    ],
                                }
                            },
                        }
                    }
                },
                legacy_output,
            )

            recursive_html = recursive_output.read_text(encoding="utf-8")
            legacy_html = legacy_output.read_text(encoding="utf-8")
            self.assertIn("Skill Root", recursive_html)
            self.assertIn("source", recursive_html)
            self.assertIn("Doc Writer", legacy_html)
            self.assertIn("Tree Depth", legacy_html)


if __name__ == "__main__":
    unittest.main()
