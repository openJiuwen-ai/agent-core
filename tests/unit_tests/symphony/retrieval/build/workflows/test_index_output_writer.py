from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openjiuwen.symphony.retrieval.build.io import load_catalog_records, load_manifest, load_tree_preset
from openjiuwen.symphony.retrieval.build.models import (
    CATALOG_FILENAME,
    TREE_HTML_FILENAME,
    TREE_INDEX_FILENAME,
    CatalogRecord,
)
from openjiuwen.symphony.retrieval.build.workflows.output_writer import (
    unlink_if_exists,
    write_index_outputs,
)


def _catalog_record(worker_id: str, cid: str, *, skill_path: str = "") -> CatalogRecord:
    return CatalogRecord(
        worker_id=worker_id,
        cid=cid,
        name=f"{worker_id.title()} Skill",
        description=f"{worker_id} weather forecast helper",
        skill_path=skill_path,
        branch_path=tuple(cid.split(".")[:-1]),
        category=".".join(cid.split(".")[:-1]),
        retrieval_text=f"{worker_id} retrieval text",
        metadata={"content": f"{worker_id} body"},
    )


class IndexOutputWriterTests(unittest.TestCase):
    def test_write_index_outputs_writes_artifacts_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            nodes = [
                {"cid": "Skills", "type": "branch", "description": "Skill root"},
                {"cid": "Skills.weather", "type": "leaf", "worker_id": "weather"},
            ]
            catalog_records = [_catalog_record("weather", "Skills.weather", skill_path="/skills/weather")]

            enriched_nodes = write_index_outputs(
                output_dir=output_dir,
                manifest_item_paths=["/skills/weather"],
                tree_nodes=nodes,
                catalog_records=catalog_records,
                mode="incremental",
                item_type="skill",
                generate_tree_html=False,
            )

            manifest = load_manifest(output_dir)
            catalog = load_catalog_records(output_dir / CATALOG_FILENAME)
            tree = load_tree_preset(output_dir / TREE_INDEX_FILENAME)

            self.assertEqual(manifest["mode"], "incremental")
            self.assertEqual(manifest["item_paths"], ["/skills/weather"])
            self.assertEqual(manifest["worker_ids"], ["weather"])
            self.assertEqual(catalog[0].worker_id, "weather")
            self.assertEqual(enriched_nodes, tree["nodes"])
            self.assertIn("Representative descendants:", tree["nodes"][0]["description"])
            self.assertFalse((output_dir / TREE_HTML_FILENAME).exists())

    def test_write_index_outputs_removes_stale_html_when_generation_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            stale_html = output_dir / TREE_HTML_FILENAME
            stale_html.write_text("<html>old</html>", encoding="utf-8")

            write_index_outputs(
                output_dir=output_dir,
                manifest_item_paths=["/skills/weather"],
                tree_nodes=[
                    {"cid": "Skills", "type": "branch", "description": "Skill root"},
                    {"cid": "Skills.weather", "type": "leaf", "worker_id": "weather"},
                ],
                catalog_records=[_catalog_record("weather", "Skills.weather")],
                mode="full",
                item_type="skill",
                generate_tree_html=False,
            )

            self.assertFalse(stale_html.exists())
            self.assertTrue((output_dir / TREE_INDEX_FILENAME).exists())
            self.assertTrue((output_dir / CATALOG_FILENAME).exists())

    def test_unlink_if_exists_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "old.html"
            path.write_text("old", encoding="utf-8")

            unlink_if_exists(path)
            unlink_if_exists(path)

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
