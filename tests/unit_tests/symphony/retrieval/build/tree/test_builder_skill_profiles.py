from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from openjiuwen.symphony.retrieval.build.io.tree import write_tree_preset
from openjiuwen.symphony.retrieval.build.tree import TreeBuildConfig, TreeManagerConfig
from openjiuwen.symphony.retrieval.build.tree.builder import TreeBuilder
from openjiuwen.symphony.retrieval.build.tree.preset_writer import TreePresetWriter
from openjiuwen.symphony.retrieval.build.tree.schema import Skill, TreeNode
from openjiuwen.symphony.retrieval.build.workflows.artifacts import build_catalog_records_from_nodes
from openjiuwen.symphony.retrieval.search.artifacts.loading import CatalogRecord as LoadedCatalogRecord
from openjiuwen.symphony.retrieval.search.artifacts.loading import load_tree_root


class TreeBuilderSkillProfileTests(unittest.TestCase):
    def test_branch_profiles_are_exported_and_rendered_for_retrieval(self) -> None:
        builder = TreeBuilder.__new__(TreeBuilder)
        writer = TreePresetWriter(builder)
        root = TreeNode(
            id="root",
            name="Root",
            children=[
                TreeNode(
                    id="research",
                    name="Research",
                    description="Research and information gathering.",
                    select_when="Use for literature, web, or market research.",
                    dont_select_when="Avoid for drafting final prose.",
                    skills=[Skill(id="paper-search", name="paper-search", description="Finds academic papers.")],
                )
            ],
        )

        preset = writer.tree_to_orchestrator_preset(writer.tree_to_dict(root))
        branch = next(node for node in preset["nodes"] if node["type"] == "branch")
        with tempfile.TemporaryDirectory() as tmp:
            tree_path = Path(tmp) / "tree.yaml"
            write_tree_preset(preset, tree_path)
            root_node = load_tree_root(
                tree_path,
                catalog_records=(
                    LoadedCatalogRecord(
                        choice_id="paper-search",
                        payload="Research.PaperSearch",
                        name="paper-search",
                        description="Finds academic papers.",
                    ),
                ),
            )

        self.assertEqual(branch["select_when"], "Use for literature, web, or market research.")
        self.assertEqual(branch["dont_select_when"], "Avoid for drafting final prose.")
        self.assertEqual(len(root_node.children), 1)
        self.assertIn("Select when: Use for literature", root_node.children[0].description)
        self.assertIn("Don't select when: Avoid for drafting", root_node.children[0].description)

    def test_skill_profiles_replace_long_source_description_for_routing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = TreeBuilder(
                skills_dir=Path(tmpdir),
                model="fake-tree-model",
                client=cast(Any, object()),
                manager_config=TreeManagerConfig(
                    build=TreeBuildConfig(
                        skill_profiles_enabled=True,
                        skill_profile_description_limit=80,
                        skill_profile_rule_limit=60,
                    )
                ),
            )

            def call_llm_json(prompt: str) -> dict:
                del prompt
                return {
                    "profiles": {
                        "weather": {
                            "description": "Gets current weather and forecast information for a location.",
                            "select_when": "Use for weather, temperature, rain, wind, or forecast requests.",
                            "dont_select_when": "Avoid for maps, routing, or travel planning.",
                        }
                    }
                }

            setattr(builder, "_call_llm_json", call_llm_json)
            enriched = getattr(builder, "_enrich_skill_profiles")(
                [
                    {
                        "id": "weather",
                        "name": "weather",
                        "description": "Very long original description. " * 40,
                        "content": "Weather skill body.",
                    }
                ]
            )

            self.assertEqual(len(enriched), 1)
            skill = enriched[0]
            self.assertEqual(
                skill["routing_description"],
                "Gets current weather and forecast information for a location.",
            )
            self.assertIn("Select when: Use for weather", skill["description"])
            self.assertIn("Don't select when: Avoid for maps", skill["description"])
            self.assertIn("Very long original description.", skill["source_description"])
            self.assertNotIn("Very long original description.", skill["description"])

    def test_disabled_skill_profiles_preserve_original_skill_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = TreeBuilder(
                skills_dir=Path(tmpdir),
                model="fake-tree-model",
                client=cast(Any, object()),
                manager_config=TreeManagerConfig(build=TreeBuildConfig(skill_profiles_enabled=False)),
            )

            def call_llm_json(prompt: str) -> None:
                del prompt
                self.fail("skill profile LLM should not be called")

            setattr(builder, "_call_llm_json", call_llm_json)
            original = {
                "id": "weather",
                "name": "Weather Lookup",
                "description": "Original detailed weather description. " * 20,
                "content": "Weather skill body.",
            }

            enriched = getattr(builder, "_enrich_skill_profiles")([original])
            writer = TreePresetWriter(builder)
            root = TreeNode(
                id="root",
                name="Root",
                children=[
                    TreeNode(
                        id="utilities",
                        name="Utilities",
                        skills=[
                            Skill(
                                id=original["id"],
                                name=original["name"],
                                description=original["description"],
                            )
                        ],
                    )
                ],
            )
            preset = writer.tree_to_orchestrator_preset(writer.tree_to_dict(root))
            leaf = next(node for node in preset["nodes"] if node["type"] == "leaf")

            self.assertEqual(enriched, [original])
            self.assertEqual(leaf["description"], original["description"].strip())
            self.assertEqual(leaf["worker_id"], original["id"])
            self.assertEqual(leaf.get("select_when"), "")
            self.assertEqual(leaf.get("dont_select_when"), "")
            self.assertEqual(leaf.get("source_description"), "")

    def test_catalog_prefers_tree_leaf_description_over_scanned_description(self) -> None:
        records = build_catalog_records_from_nodes(
            nodes=[
                {
                    "cid": "LifestyleUtility.Weather",
                    "type": "leaf",
                    "worker_id": "weather",
                    "description": "Gets weather forecasts.",
                    "select_when": "Use for weather requests.",
                    "dont_select_when": "Avoid for map routing.",
                    "source_description": "Raw weather skill description.",
                }
            ],
            scanned_skills={
                "weather": {
                    "id": "weather",
                    "name": "weather",
                    "description": "Raw weather skill description.",
                    "content": "Weather skill body.",
                    "path": "/tmp/weather/SKILL.md",
                }
            },
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].description, "Gets weather forecasts.")
        self.assertEqual(records[0].metadata["source_description"], "Raw weather skill description.")
        self.assertEqual(records[0].metadata["select_when"], "Use for weather requests.")
        self.assertEqual(records[0].metadata["dont_select_when"], "Avoid for map routing.")


if __name__ == "__main__":
    unittest.main()
