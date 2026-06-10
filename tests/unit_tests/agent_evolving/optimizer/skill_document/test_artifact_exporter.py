# coding: utf-8
"""Tests for ArtifactExporter."""

import json
from unittest.mock import MagicMock

from openjiuwen.agent_evolving.optimizer.skill_document.artifact_exporter import ArtifactExporter


class TestArtifactExporter:
    @staticmethod
    def test_output_dir_none_is_noop():
        """All methods should be no-ops when output_dir is None."""
        exp = ArtifactExporter(output_dir=None)
        # None of these should raise
        exp.export_trajectories(0, 0, [], [])
        exp.export_eval_results(0, 0, [], [])
        exp.export_raw_patches(0, 0, 0, [])
        exp.export_merged_patch(0, 0, MagicMock())
        exp.export_selected_edits(0, 0, [], [], 10)
        exp.export_skill_snapshot(0, 0, "# Skill", "before")
        exp.export_gate_result(0, 0.5, 0.6, "accepted")
        exp.export_metrics(0, 0, {"n_cases": 10})
        exp.export_skill_diff(0, 0, "before", "after")

    @staticmethod
    def test_export_trajectories_writes_jsonl(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        traj = MagicMock()
        traj.steps = [MagicMock(kind="llm")]
        eval_result = MagicMock()
        eval_result.case_id = "c1"
        eval_result.score = 0.8

        case = MagicMock()
        case.case_id = "c1"

        exp.export_trajectories(0, 0, [traj], [eval_result])

        path = tmp_path / "epoch_0" / "step_0" / "trajectories.jsonl"
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["case_id"] == "c1"

    @staticmethod
    def test_export_trajectories_disabled(tmp_path):
        """export_trajectories=False should skip trajectory export."""
        exp = ArtifactExporter(output_dir=str(tmp_path), export_trajectories=False)
        exp.export_trajectories(0, 0, [MagicMock()], [MagicMock()])

        path = tmp_path / "epoch_0" / "step_0" / "trajectories.jsonl"
        assert not path.exists()

    @staticmethod
    def test_export_eval_results(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        eval_result = MagicMock()
        eval_result.case_id = "c1"
        eval_result.score = 0.7
        eval_result.reason = "good"

        case = MagicMock()
        case.case_id = "c1"

        exp.export_eval_results(0, 0, [eval_result], [case])

        path = tmp_path / "epoch_0" / "step_0" / "eval_results.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["epoch"] == 0
        assert data["step"] == 0
        assert len(data["results"]) == 1
        assert data["results"][0]["score"] == 0.7

    @staticmethod
    def test_export_raw_patches(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        patch = MagicMock()
        patch.patch = MagicMock()
        patch.patch.edits = [MagicMock(op="append", content="new rule")]
        patch.patch.reasoning = "improve clarity"
        patch.source_type = "failure"
        patch.failure_summary = "agent confused"

        exp.export_raw_patches(0, 0, 0, [patch])

        path = tmp_path / "epoch_0" / "step_0" / "raw_patches.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["epoch"] == 0
        assert len(data["patches"]) == 1

    @staticmethod
    def test_export_selected_edits(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        edit = MagicMock()
        edit.op = "append"
        edit.content = "new rule"
        edit.support_count = 3

        exp.export_selected_edits(0, 0, [edit], [], budget=10)

        path = tmp_path / "epoch_0" / "step_0" / "selected_edits.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["budget"] == 10
        assert data["selected_count"] == 1

    @staticmethod
    def test_export_gate_result(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        exp.export_gate_result(0, base_score=0.5, candidate_score=0.7, decision="accepted")

        path = tmp_path / "epoch_0" / "gate_result.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["base_score"] == 0.5
        assert data["candidate_score"] == 0.7
        assert data["decision"] == "accepted"

    @staticmethod
    def test_export_skill_snapshot(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        exp.export_skill_snapshot(0, 0, "# My Skill Content", tag="before")

        path = tmp_path / "epoch_0" / "skill_before.md"
        assert path.exists()
        assert path.read_text() == "# My Skill Content"

    @staticmethod
    def test_export_skill_diff(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        before = "line1\nline2\nline3\n"
        after = "line1\nline2_modified\nline3\n"

        exp.export_skill_diff(0, 0, before, after)

        path = tmp_path / "epoch_0" / "step_0" / "applied_diff.patch"
        assert path.exists()
        diff_text = path.read_text()
        assert "line2_modified" in diff_text
        # Should be unified diff format
        assert "---" in diff_text or "@@" in diff_text

    @staticmethod
    def test_export_metrics(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        exp.export_metrics(0, 0, {"n_cases": 10, "avg_score": 0.75, "duration_secs": 5.2})

        path = tmp_path / "epoch_0" / "step_0" / "metrics.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["n_cases"] == 10

    @staticmethod
    def test_directory_structure(tmp_path):
        """Verify epoch_N/step_M/ hierarchy."""
        exp = ArtifactExporter(output_dir=str(tmp_path))
        exp.export_metrics(2, 1, {"step": "test"})

        epoch_dir = tmp_path / "epoch_2"
        step_dir = epoch_dir / "step_1"
        assert epoch_dir.is_dir()
        assert step_dir.is_dir()

    @staticmethod
    def test_export_merged_patch(tmp_path):
        exp = ArtifactExporter(output_dir=str(tmp_path))
        patch = MagicMock()
        patch.edits = [MagicMock(op="append", content="merged rule")]
        patch.reasoning = "merged for clarity"

        exp.export_merged_patch(0, 0, patch)

        path = tmp_path / "epoch_0" / "step_0" / "merged_patch.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["epoch"] == 0
