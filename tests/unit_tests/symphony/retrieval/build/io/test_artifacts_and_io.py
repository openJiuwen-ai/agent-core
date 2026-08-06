from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from openjiuwen.symphony.retrieval.build.io.items_jsonl import parse_jsonl_scanned_items
from openjiuwen.symphony.retrieval.build.io.manifest import load_manifest, write_manifest
from openjiuwen.symphony.retrieval.build.io.tree import (
    load_tree_preset,
    normalize_item_paths,
    parse_simple_nodes_yaml,
    write_tree_preset,
)
from openjiuwen.symphony.retrieval.build.workflows.artifacts import (
    BuildConfig,
    BuildMethod,
    build_catalog_records_from_nodes,
    build_fallback_tree_nodes,
    build_retrieval_text,
    can_build_tree_with_llm,
    compact_text,
    resolve_build_config,
    write_catalog,
)


class ArtifactsAndIoTests(unittest.TestCase):
    def test_resolve_build_config_clamps_and_rejects_conflicts(self) -> None:
        config = BuildConfig(
            method=BuildMethod.TREE,
            llm_model="tree-model",
            llm_api_key="tree-key",
            tree_branching_factor=0,
            tree_max_depth=0,
            tree_max_workers=0,
            tree_equiv_min_lexical_similarity=5.0,
            generate_tree_html=True,
        )

        resolved = resolve_build_config(config=config)

        self.assertEqual(resolved.method, BuildMethod.TREE)
        self.assertEqual(resolved.llm_model, "tree-model")
        self.assertEqual(resolved.tree_llm_api_key, "tree-key")
        self.assertEqual(resolved.tree_branching_factor, 8)
        self.assertEqual(resolved.tree_max_depth, 6)
        self.assertEqual(resolved.tree_max_workers, 1)
        self.assertEqual(resolved.tree_equiv_min_lexical_similarity, 1.0)
        self.assertTrue(resolved.generate_tree_html)
        with self.assertRaisesRegex(ValueError, "Specify either config or runtime_config"):
            resolve_build_config(config=BuildConfig(), runtime_config=BuildConfig())
        with self.assertRaisesRegex(ValueError, "Unsupported build method"):
            resolve_build_config(config=BuildConfig(method=BuildMethod(8)))

    def test_llm_capability_detection_accepts_client_or_key(self) -> None:
        self.assertFalse(can_build_tree_with_llm(BuildConfig()))
        self.assertTrue(can_build_tree_with_llm(BuildConfig(llm_model="model", llm_openai_client=cast(Any, object()))))
        self.assertTrue(can_build_tree_with_llm(BuildConfig(llm_model="model", llm_api_key="key")))

    def test_catalog_records_prefer_tree_profiles_when_present(self) -> None:
        records = build_catalog_records_from_nodes(
            nodes=[
                {
                    "cid": "Skills.Weather",
                    "type": "leaf",
                    "worker_id": "weather",
                    "description": "Gets weather forecasts.",
                    "select_when": "Use for weather requests.",
                    "dont_select_when": "Avoid map routing.",
                    "source_description": "Raw weather skill description.",
                }
            ],
            scanned_skills={
                "weather": {
                    "id": "weather",
                    "name": "Weather Lookup",
                    "description": "Raw weather skill description.",
                    "content": "Weather skill body.",
                    "path": "/tmp/weather/SKILL.md",
                }
            },
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.worker_id, "weather")
        self.assertEqual(record.name, "Weather Lookup")
        self.assertEqual(record.description, "Gets weather forecasts.")
        self.assertEqual(record.branch_path, ("Skills",))
        self.assertEqual(record.metadata["source_description"], "Raw weather skill description.")
        self.assertIn("Weather skill body.", record.retrieval_text)

    def test_write_catalog_manifest_and_tree_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = build_catalog_records_from_nodes(
                nodes=[
                    {"cid": "Skills", "type": "branch", "description": "Skill branch"},
                    {"cid": "Skills.Alpha", "type": "leaf", "worker_id": "alpha", "description": "Tree alpha"},
                ],
                scanned_skills={
                    "alpha": {
                        "id": "alpha",
                        "name": "Alpha Skill",
                        "description": "Scanned alpha",
                        "content": "Alpha content",
                        "path": root / "alpha" / "SKILL.md",
                    }
                },
            )

            write_catalog(records, root / "catalog.jsonl")
            write_manifest(root, ["s3://bucket/alpha.zip", root / "alpha"], records, mode="full", item_type="skill")
            write_tree_preset(
                {
                    "nodes": [
                        {"cid": "Skills.Alpha", "type": "leaf", "worker_id": "alpha", "description": "Tree alpha"},
                        {"cid": "Skills", "type": "branch", "description": "Skill branch"},
                    ]
                },
                root / "tree_index.yaml",
            )

            catalog_payload = json.loads((root / "catalog.jsonl").read_text(encoding="utf-8").strip())
            manifest = load_manifest(root)
            tree = load_tree_preset(root / "tree_index.yaml")

            self.assertEqual(catalog_payload["worker_id"], "alpha")
            self.assertEqual(manifest["item_type"], "skill")
            self.assertEqual(manifest["item_paths"][0], "s3://bucket/alpha.zip")
            self.assertEqual([node["cid"] for node in tree["nodes"]], ["Skills", "Skills.Alpha"])

    def test_jsonl_scanned_items_accepts_adjacent_json_and_dedupes(self) -> None:
        jsonl_content = (
            '{"contentExtendParam": {"skillId": "alpha", "skillName": "Alpha", '
            '"skillDesc": "Alpha desc", "stars": "3"}}\n'
            "not-json\n"
            '{"contentExtendParam": {"skillId": "alpha", "skillName": "Duplicate"}}'
            ',{"contentExtendParam": {"skillId": "beta", "skillName": "Beta", "isOfficial": true}}'
        )

        scanned, paths = parse_jsonl_scanned_items(jsonl_content)

        self.assertEqual(paths, ["jsonl://skill/alpha", "jsonl://skill/beta"])
        self.assertEqual(scanned["alpha"]["name"], "Alpha")
        self.assertEqual(scanned["alpha"]["stars"], 3)
        self.assertEqual(scanned["beta"]["description"], "Beta")
        self.assertTrue(scanned["beta"]["is_official"])

    def test_fallback_tree_nodes_and_yaml_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            (root / "alpha").mkdir()

            nodes = build_fallback_tree_nodes(aggregate_dir=root)
            parsed = parse_simple_nodes_yaml(
                "nodes:\n"
                '  - cid: "Skills"\n'
                '    type: "branch"\n'
                '  - cid: "Skills.Alpha"\n'
                '    type: "leaf"\n'
                '    worker_id: "alpha"\n'
            )

            self.assertEqual([node["worker_id"] for node in nodes if "worker_id" in node], ["alpha", "beta"])
            self.assertEqual(parsed["nodes"][1]["worker_id"], "alpha")
            self.assertEqual(
                normalize_item_paths(["s3://bucket/a.zip", "s3://bucket/a.zip", root / "alpha"]),
                ["s3://bucket/a.zip", str((root / "alpha").resolve())],
            )
            self.assertEqual(compact_text("a  b  c", limit=20), "a b c")
            self.assertTrue(compact_text("abcdef", limit=4).endswith("..."))
            self.assertIn(
                "alpha",
                build_retrieval_text(
                    worker_id="alpha",
                    name="",
                    description="",
                    content="",
                    cid="Skills.Alpha",
                ),
            )


if __name__ == "__main__":
    unittest.main()
