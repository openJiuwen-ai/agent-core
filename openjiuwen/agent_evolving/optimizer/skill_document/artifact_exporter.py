# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Artifact exporter for training diagnostics (D1/D3/D7).

Writes intermediate artifacts (JSON, markdown, unified diffs) to a
structured directory. Independent of checkpoint system.

All methods are no-ops when output_dir is None.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any


class ArtifactExporter:
    """Stateless artifact exporter for training diagnostics.

    Directory structure:
        output_dir/epoch_N/step_M/{trajectories.jsonl, eval_results.json, ...}
        output_dir/epoch_N/{skill_before.md, gate_result.json, ...}
    """

    def __init__(self, output_dir: str | None = None, *, export_trajectories: bool = True):
        self._output_dir = Path(output_dir) if output_dir else None
        self._export_trajectories = export_trajectories

    @property
    def enabled(self) -> bool:
        return self._output_dir is not None

    def _epoch_dir(self, epoch: int) -> Path | None:
        if not self._output_dir:
            return None
        d = self._output_dir / f"epoch_{epoch}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _step_dir(self, epoch: int, step: int) -> Path | None:
        epoch_dir = self._epoch_dir(epoch)
        if not epoch_dir:
            return None
        d = epoch_dir / f"step_{step}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def export_trajectories(
        self,
        epoch: int,
        step: int,
        trajectories: list,
        eval_results: list,
    ) -> None:
        """Write trajectories as JSONL (one case per line)."""
        if not self._export_trajectories:
            return
        step_dir = self._step_dir(epoch, step)
        if not step_dir:
            return

        with open(step_dir / "trajectories.jsonl", "w", encoding="utf-8") as f:
            for traj, eval_result in zip(trajectories, eval_results):
                entry = {
                    "case_id": getattr(eval_result, "case_id", ""),
                    "score": getattr(eval_result, "score", 0.0),
                    "steps": [
                        {
                            "kind": getattr(s, "kind", ""),
                            "detail": str(getattr(s, "detail", ""))[:500],
                        }
                        for s in getattr(traj, "steps", [])
                    ],
                }
                f.write(json.dumps(entry, default=str) + "\n")

    def export_eval_results(
        self,
        epoch: int,
        step: int,
        eval_results: list,
        cases: list,
    ) -> None:
        """Write evaluation results as JSON."""
        step_dir = self._step_dir(epoch, step)
        if not step_dir:
            return

        results = []
        for er, case in zip(eval_results, cases):
            results.append(
                {
                    "case_id": getattr(case, "case_id", getattr(er, "case_id", "")),
                    "score": getattr(er, "score", 0.0),
                    "reason": getattr(er, "reason", ""),
                }
            )

        scores = [r["score"] for r in results]
        data = {
            "epoch": epoch,
            "step": step,
            "results": results,
            "avg_score": sum(scores) / len(scores) if scores else 0.0,
            "failure_rate": sum(1 for s in scores if s < 0.5) / len(scores) if scores else 0.0,
        }
        self._write_json(step_dir / "eval_results.json", data)

    def export_raw_patches(self, epoch: int, step: int, acc_round: int, patches: list) -> None:
        """Write raw patches from reflect phase."""
        step_dir = self._step_dir(epoch, step)
        if not step_dir:
            return

        serialized = []
        for p in patches:
            patch_obj = getattr(p, "patch", p)
            serialized.append(
                {
                    "source_type": getattr(p, "source_type", ""),
                    "failure_summary": getattr(p, "failure_summary", ""),
                    "reasoning": getattr(patch_obj, "reasoning", ""),
                    "edits": [
                        {
                            "op": getattr(e, "op", ""),
                            "content": getattr(e, "content", ""),
                            "target": getattr(e, "target", ""),
                        }
                        for e in getattr(patch_obj, "edits", [])
                    ],
                }
            )

        self._write_json(
            step_dir / "raw_patches.json",
            {"epoch": epoch, "step": step, "acc_round": acc_round, "patches": serialized},
        )

    def export_merged_patch(self, epoch: int, step: int, patch: Any) -> None:
        """Write merged patch from aggregate phase."""
        step_dir = self._step_dir(epoch, step)
        if not step_dir:
            return

        data = {
            "epoch": epoch,
            "step": step,
            "reasoning": getattr(patch, "reasoning", ""),
            "edits": [
                {
                    "op": getattr(e, "op", ""),
                    "content": getattr(e, "content", ""),
                    "target": getattr(e, "target", ""),
                }
                for e in getattr(patch, "edits", [])
            ],
        }
        self._write_json(step_dir / "merged_patch.json", data)

    def export_selected_edits(
        self,
        epoch: int,
        step: int,
        edits: list,
        rejected: list,
        budget: int,
    ) -> None:
        """Write selected edits from ranking phase."""
        step_dir = self._step_dir(epoch, step)
        if not step_dir:
            return

        data = {
            "epoch": epoch,
            "step": step,
            "budget": budget,
            "selected_count": len(edits),
            "edits": [
                {
                    "op": getattr(e, "op", ""),
                    "content": getattr(e, "content", ""),
                    "target": getattr(e, "target", ""),
                    "support_count": getattr(e, "support_count", 0),
                }
                for e in edits
            ],
            "rejected": rejected,
        }
        self._write_json(step_dir / "selected_edits.json", data)

    def export_skill_snapshot(self, epoch: int, step: int, skill_content: str, tag: str) -> None:
        """Write skill document snapshot (before/after)."""
        epoch_dir = self._epoch_dir(epoch)
        if not epoch_dir:
            return
        (epoch_dir / f"skill_{tag}.md").write_text(skill_content, encoding="utf-8")

    def export_gate_result(
        self,
        epoch: int,
        base_score: float | None,
        candidate_score: float | None,
        decision: str,
    ) -> None:
        """Write gate validation result."""
        epoch_dir = self._epoch_dir(epoch)
        if not epoch_dir:
            return

        data = {
            "epoch": epoch,
            "base_score": base_score,
            "candidate_score": candidate_score,
            "improvement": (
                candidate_score - base_score if base_score is not None and candidate_score is not None else None
            ),
            "decision": decision,
        }
        self._write_json(epoch_dir / "gate_result.json", data)

    def export_metrics(self, epoch: int, step: int, metrics: dict) -> None:
        """Write step-level metrics."""
        step_dir = self._step_dir(epoch, step)
        if not step_dir:
            return
        self._write_json(step_dir / "metrics.json", metrics)

    def export_skill_diff(self, epoch: int, step: int, before: str, after: str) -> None:
        """Write unified diff between skill versions."""
        step_dir = self._step_dir(epoch, step)
        if not step_dir:
            return

        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="skill_before",
            tofile="skill_after",
        )
        (step_dir / "applied_diff.patch").write_text("".join(diff), encoding="utf-8")
