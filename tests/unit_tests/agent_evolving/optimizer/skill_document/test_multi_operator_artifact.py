# coding: utf-8
"""Tests for ArtifactExporter with operator_id support."""

import json

from openjiuwen.agent_evolving.optimizer.skill_document.artifact_exporter import ArtifactExporter
from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit, Patch, RawPatch


class TestOperatorIdArtifacts:
    """Per-operator artifact file creation."""

    @staticmethod
    def test_merged_patch_with_operator_id(tmp_path):
        exporter = ArtifactExporter(str(tmp_path))
        patch = Patch(edits=[Edit(op="append", content="x")], reasoning="r")

        exporter.export_merged_patch(0, 0, patch, operator_id="op_a")

        artifact_file = tmp_path / "epoch_0" / "step_0" / "merged_patch_op_a.json"
        assert artifact_file.exists()
        data = json.loads(artifact_file.read_text())
        assert data["operator_id"] == "op_a"

    @staticmethod
    def test_merged_patch_without_operator_id(tmp_path):
        exporter = ArtifactExporter(str(tmp_path))
        patch = Patch(edits=[], reasoning="r")

        exporter.export_merged_patch(0, 0, patch)

        artifact_file = tmp_path / "epoch_0" / "step_0" / "merged_patch.json"
        assert artifact_file.exists()

    @staticmethod
    def test_selected_edits_with_operator_id(tmp_path):
        exporter = ArtifactExporter(str(tmp_path))
        edits = [Edit(op="append", content="x")]

        exporter.export_selected_edits(0, 0, edits, [], 10, operator_id="op_b")

        artifact_file = tmp_path / "epoch_0" / "step_0" / "selected_edits_op_b.json"
        assert artifact_file.exists()
        data = json.loads(artifact_file.read_text())
        assert data["operator_id"] == "op_b"

    @staticmethod
    def test_selected_edits_without_operator_id(tmp_path):
        exporter = ArtifactExporter(str(tmp_path))

        exporter.export_selected_edits(0, 0, [], [], 10)

        artifact_file = tmp_path / "epoch_0" / "step_0" / "selected_edits.json"
        assert artifact_file.exists()

    @staticmethod
    def test_skill_snapshot_with_operator_id(tmp_path):
        exporter = ArtifactExporter(str(tmp_path))

        exporter.export_skill_snapshot(0, 0, "skill content", "before", operator_id="op_a")

        artifact_file = tmp_path / "epoch_0" / "skill_before_op_a.md"
        assert artifact_file.exists()
        assert artifact_file.read_text() == "skill content"

    @staticmethod
    def test_raw_patches_include_operator_id(tmp_path):
        exporter = ArtifactExporter(str(tmp_path))
        patches = [
            RawPatch(
                patch=Patch(edits=[Edit(op="append", content="x")]),
                source_type="failure",
                operator_id="op_a",
            ),
        ]

        exporter.export_raw_patches(0, 0, 0, patches)

        artifact_file = tmp_path / "epoch_0" / "step_0" / "raw_patches.json"
        data = json.loads(artifact_file.read_text())
        assert data["patches"][0]["operator_id"] == "op_a"
