from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import openjiuwen.symphony.retrieval.build.workflows.index_builder as workflows_module
from openjiuwen.symphony.retrieval.build.io import load_catalog_records, load_manifest, load_tree_preset
from openjiuwen.symphony.retrieval.build.workflows.artifacts import BuildConfig, BuildMethod
from openjiuwen.symphony.retrieval.build.workflows.index_builder import IndexBuilder


def _write_skill_dir(root: Path, name: str, description: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{name} body content.\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_plugin_dir(root: Path, name: str, description: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            [
                f"name: {name}",
                "version: 0.1.0",
                f"display_name: {name} display",
                f"description: '{description}'",
                "metadata:",
                "  author: plugin-author",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "README.md").write_text(f"# {name}\n\n{description}\n", encoding="utf-8")
    return plugin_dir


def _write_legacy_plugin_dir(root: Path, name: str, description: str) -> Path:
    plugin_dir = root / name
    (plugin_dir / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "description": description, "author": "plugin-author"}),
        encoding="utf-8",
    )
    (plugin_dir / "README.md").write_text(f"# {name}\n\n{description}\n", encoding="utf-8")
    return plugin_dir


def _zip_item_dir(item_dir: Path, zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in item_dir.rglob("*"):
            archive.write(path, arcname=str(path.relative_to(item_dir.parent)))
    return zip_path


def _fake_build_tree(**kwargs):
    skills_dir = Path(kwargs["skills_dir"])
    skill_entries = kwargs.get("skill_entries")
    nodes = [{"cid": "Skills", "type": "branch", "description": "LLM skill branch"}]
    if skill_entries is not None:
        worker_ids = sorted(str(item.get("id") or "") for item in skill_entries if str(item.get("id") or ""))
    else:
        worker_ids = sorted(path.name for path in skills_dir.iterdir() if path.is_dir())
    for worker_id in worker_ids:
        nodes.append(
            {
                "cid": f"Skills.{worker_id}",
                "type": "leaf",
                "description": f"{worker_id} tree description",
                "worker_id": worker_id,
            }
        )
    return {"nodes": nodes}


class IndexBuilderWorkflowTests(unittest.TestCase):
    def test_build_writes_tree_catalog_and_manifest_with_llm_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            alpha = _write_skill_dir(root, "alpha", "alpha description")
            beta = _write_skill_dir(root, "beta", "beta description")
            output_dir = root / "index"
            config = BuildConfig(
                method=BuildMethod.TREE,
                llm_openai_client=cast(Any, object()),
                llm_model="fake-tree-model",
            )

            with patch.object(workflows_module, "build_tree", side_effect=_fake_build_tree):
                result = IndexBuilder.build([str(alpha), str(beta)], output_dir, config=config)

            self.assertEqual(result, output_dir.resolve())
            self.assertTrue((output_dir / "tree_index.yaml").exists())
            self.assertTrue((output_dir / "catalog.jsonl").exists())
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertFalse((output_dir / "tree_index.html").exists())

            manifest = load_manifest(output_dir)
            catalog = load_catalog_records(output_dir / "catalog.jsonl")
            tree_text = (output_dir / "tree_index.yaml").read_text(encoding="utf-8")

            self.assertEqual(manifest["mode"], "full")
            self.assertEqual(manifest["count"], 2)
            self.assertEqual(manifest["worker_ids"], ["alpha", "beta"])
            self.assertEqual([record.worker_id for record in catalog], ["alpha", "beta"])
            self.assertIn("Representative descendants:", tree_text)
            self.assertIn("Representative keywords:", tree_text)
            self.assertIn("alpha body content.", catalog[0].metadata["content"])

    def test_build_without_llm_uses_fallback_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            alpha = _write_skill_dir(root, "alpha", "alpha description")
            output_dir = root / "index"

            with patch.object(workflows_module, "build_tree", side_effect=AssertionError("LLM builder should not run")):
                result = IndexBuilder.build([str(alpha)], output_dir, config=BuildConfig())

            self.assertEqual(result, output_dir.resolve())
            tree = load_tree_preset(output_dir / "tree_index.yaml")
            self.assertEqual([node["cid"] for node in tree["nodes"]], ["Skills", "Skills.alpha"])
            self.assertIn("Fallback skill index built without LLM tree generation.", tree["nodes"][0]["description"])

    def test_build_without_llm_can_disable_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            alpha = _write_skill_dir(root, "alpha", "alpha description")

            with self.assertRaisesRegex(ValueError, "fallback is disabled"):
                IndexBuilder.build([str(alpha)], root / "index", config=BuildConfig(allow_fallback_tree=False))

    def test_add_and_delete_update_existing_tree_without_full_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            alpha = _write_skill_dir(root, "alpha", "alpha description")
            beta = _write_skill_dir(root, "beta", "beta description")
            gamma = _write_skill_dir(root, "gamma", "gamma description")
            base_dir = root / "base-index"
            add_dir = root / "add-index"
            delete_dir = root / "delete-index"
            config = BuildConfig(
                method=BuildMethod.TREE,
                llm_openai_client=cast(Any, object()),
                llm_model="fake-tree-model",
                incremental_max_change_ratio=1.0,
            )

            with patch.object(workflows_module, "build_tree", side_effect=_fake_build_tree) as build_tree_mock:
                IndexBuilder.build([str(alpha), str(beta)], base_dir, config=config)
                IndexBuilder.add([str(gamma)], base_dir, add_dir, config=config)
                IndexBuilder.delete([str(beta)], add_dir, delete_dir, config=config)

            self.assertEqual(build_tree_mock.call_count, 1)
            add_manifest = load_manifest(add_dir)
            delete_manifest = load_manifest(delete_dir)
            add_catalog = load_catalog_records(add_dir / "catalog.jsonl")
            delete_catalog = load_catalog_records(delete_dir / "catalog.jsonl")

            self.assertEqual(add_manifest["mode"], "incremental")
            self.assertEqual(len(add_manifest["item_paths"]), 3)
            self.assertEqual(len(delete_manifest["item_paths"]), 2)
            self.assertEqual([record.worker_id for record in add_catalog], ["alpha", "beta", "gamma"])
            self.assertEqual([record.worker_id for record in delete_catalog], ["alpha", "gamma"])
            self.assertIn(str(alpha.resolve()), delete_manifest["item_paths"])
            self.assertIn(str(gamma.resolve()), delete_manifest["item_paths"])

    def test_build_supports_s3_zip_input_and_s3_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            alpha = _write_skill_dir(root, "alpha", "alpha description")
            alpha_zip = _zip_item_dir(alpha, root / "alpha.zip")
            uploaded: dict[str, bytes] = {}
            config = BuildConfig(
                method=BuildMethod.TREE,
                llm_openai_client=cast(Any, object()),
                llm_model="fake-tree-model",
            )

            def fake_download(uri: str, destination: Path) -> Path:
                self.assertEqual(uri, "s3://bucket/skills/alpha.zip")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(alpha_zip.read_bytes())
                return destination

            def fake_upload(local_dir: Path, output_uri: str) -> None:
                self.assertEqual(output_uri, "s3://bucket/index")
                for path in local_dir.rglob("*"):
                    if path.is_file():
                        uploaded[str(path.relative_to(local_dir)).replace("\\", "/")] = path.read_bytes()

            with patch.object(workflows_module, "build_tree", side_effect=_fake_build_tree):
                with patch.object(workflows_module, "download_s3_object_to_path", side_effect=fake_download):
                    with patch.object(workflows_module, "upload_local_dir_to_s3", side_effect=fake_upload):
                        result = IndexBuilder.build(
                            ["s3://bucket/skills/alpha.zip"],
                            "s3://bucket/index",
                            config=config,
                        )

            self.assertEqual(result, "s3://bucket/index")
            self.assertIn("manifest.json", uploaded)
            manifest = json.loads(uploaded["manifest.json"].decode("utf-8"))
            self.assertEqual(manifest["item_paths"], ["s3://bucket/skills/alpha.zip"])
            self.assertIn("tree_index.yaml", uploaded)
            self.assertIn("catalog.jsonl", uploaded)

    def test_build_uses_plugin_scanner_for_local_zip_and_legacy_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plugin = _write_plugin_dir(root, "demo-plugin", "plugin description")
            plugin_zip = _zip_item_dir(plugin, root / "demo-plugin.zip")
            legacy = _write_legacy_plugin_dir(root, "legacy-plugin", "legacy plugin description")
            config = BuildConfig(
                method=BuildMethod.TREE,
                llm_openai_client=cast(Any, object()),
                llm_model="fake-tree-model",
            )

            with patch.object(workflows_module, "build_tree", side_effect=_fake_build_tree):
                IndexBuilder.build([str(plugin_zip)], root / "zip-index", item_type="plugin", config=config)
                IndexBuilder.build([str(legacy)], root / "legacy-index", item_type="plugin", config=config)

            zip_catalog = load_catalog_records(root / "zip-index" / "catalog.jsonl")
            legacy_catalog = load_catalog_records(root / "legacy-index" / "catalog.jsonl")
            self.assertEqual(zip_catalog[0].name, "demo-plugin display")
            self.assertEqual(zip_catalog[0].description, "plugin description")
            self.assertEqual(zip_catalog[0].skill_path, str(plugin_zip.resolve()))
            self.assertEqual(legacy_catalog[0].name, "legacy-plugin")
            self.assertEqual(legacy_catalog[0].description, "legacy plugin description")

    def test_build_from_item_jsonl_uses_pre_scanned_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            jsonl_path = root / "items.jsonl"
            jsonl_path.write_text(
                json.dumps(
                    {
                        "contentExtendParam": {
                            "skillId": "weather",
                            "skillName": "Weather",
                            "skillDesc": "Weather forecast skill",
                            "githubUrl": "https://example.invalid/weather",
                            "stars": 9,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = IndexBuilder.build(
                output_dir=root / "index",
                item_jsonl_path=str(jsonl_path),
                config=BuildConfig(),
            )

            self.assertEqual(result, (root / "index").resolve())
            manifest = load_manifest(root / "index")
            catalog = load_catalog_records(root / "index" / "catalog.jsonl")
            tree = load_tree_preset(root / "index" / "tree_index.yaml")

            self.assertEqual(manifest["item_paths"], ["jsonl://skill/weather"])
            self.assertEqual(catalog[0].worker_id, "weather")
            self.assertEqual(catalog[0].name, "Weather")
            self.assertEqual(catalog[0].metadata["source_description"], "Weather forecast skill")
            self.assertEqual(tree["nodes"][1]["worker_id"], "weather")

    def test_incremental_jsonl_matches_existing_catalog_by_worker_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            def write_jsonl(path: Path, worker_ids: list[str]) -> None:
                lines = []
                for worker_id in worker_ids:
                    lines.append(
                        json.dumps(
                            {
                                "contentExtendParam": {
                                    "skillId": worker_id,
                                    "skillName": worker_id.title(),
                                    "skillDesc": f"{worker_id} description",
                                    "skillPath": f"/installed/{worker_id}/SKILL.md",
                                }
                            }
                        )
                    )
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            base_jsonl = root / "base.jsonl"
            add_jsonl = root / "add.jsonl"
            delete_jsonl = root / "delete.jsonl"
            write_jsonl(base_jsonl, ["alpha", "beta"])
            write_jsonl(add_jsonl, ["gamma"])
            write_jsonl(delete_jsonl, ["beta"])
            base_dir = root / "base-index"
            add_dir = root / "add-index"
            delete_dir = root / "delete-index"
            config = BuildConfig(
                method=BuildMethod.TREE,
                llm_openai_client=cast(Any, object()),
                llm_model="fake-tree-model",
                incremental_max_change_ratio=1.0,
                incremental_min_add_confidence=0.0,
                incremental_min_add_confidence_margin=0.0,
            )

            with patch.object(workflows_module, "build_tree", side_effect=_fake_build_tree) as build_tree_mock:
                IndexBuilder.build(output_dir=base_dir, item_jsonl_path=str(base_jsonl), config=config)
                IndexBuilder.add(
                    base_index_dir=base_dir,
                    output_dir=add_dir,
                    item_jsonl_path=str(add_jsonl),
                    config=config,
                )
                IndexBuilder.delete(
                    base_index_dir=add_dir,
                    output_dir=delete_dir,
                    item_jsonl_path=str(delete_jsonl),
                    config=config,
                )

            add_catalog = load_catalog_records(add_dir / "catalog.jsonl")
            delete_catalog = load_catalog_records(delete_dir / "catalog.jsonl")
            self.assertEqual(build_tree_mock.call_count, 1)
            self.assertEqual([record.worker_id for record in add_catalog], ["alpha", "beta", "gamma"])
            self.assertEqual([record.worker_id for record in delete_catalog], ["alpha", "gamma"])
            self.assertEqual(load_manifest(add_dir)["item_paths"], [
                "jsonl://skill/alpha",
                "jsonl://skill/beta",
                "jsonl://skill/gamma",
            ])
            self.assertEqual(add_catalog[0].skill_path, "/installed/alpha/SKILL.md")

    def test_rejects_invalid_paths_and_duplicate_materialized_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            alpha = _write_skill_dir(root / "left", "alpha", "left alpha")
            duplicate_alpha = _write_skill_dir(root / "right", "alpha", "right alpha")

            with self.assertRaisesRegex(FileNotFoundError, "Item path not found"):
                IndexBuilder.build([str(root / "missing")], root / "missing-index")
            with self.assertRaisesRegex(ValueError, "Duplicate skill directory name"):
                IndexBuilder.build([str(alpha), str(duplicate_alpha)], root / "duplicate-index")
            with self.assertRaisesRegex(ValueError, "item_paths is empty"):
                IndexBuilder.build([], root / "empty-index")


if __name__ == "__main__":
    unittest.main()
