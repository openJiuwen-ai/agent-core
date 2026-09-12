# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stage sleep proposals without mutating live skills."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from openjiuwen.agent_evolving.skill_train.sleep.types import SleepReport

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _ts_dir(clock: Optional[float] = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock if clock is not None else time.time()))
    return stamp


def proposed_skill_filename(skill_name: str) -> str:
    name = (skill_name or "").strip()
    if not name or not _SAFE_NAME.match(name):
        raise ValueError(f"unsafe skill name for staging file: {skill_name!r}")
    return f"proposed_SKILL.{name}.md"


def new_staging_dir(root: Path | str, clock: Optional[float] = None) -> Path:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    base = _ts_dir(clock)
    path = root_path / base
    suffix = 2
    while path.exists():
        path = root_path / f"{base}-{suffix}"
        suffix += 1
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_staging(
    staging_dir: Path | str,
    *,
    report: SleepReport,
    proposed_skill: Optional[str] = None,
    proposed_memory: Optional[str] = None,
    baseline_skill: str = "",
    skill_name: str = "",
    skill_proposals: Optional[Mapping[str, str]] = None,
) -> Path:
    """Write proposal artifacts. Never touches live EvolutionStore skills.

    ``skill_proposals`` maps skill_name -> proposed SKILL.md body for multi-skill
    nights. Each lands as ``proposed_SKILL.<name>.md``. The legacy single-file
    ``proposed_SKILL.md`` is still written when ``proposed_skill`` is set.
    """
    path = Path(staging_dir)
    path.mkdir(parents=True, exist_ok=True)

    if proposed_skill is not None:
        (path / "proposed_SKILL.md").write_text(proposed_skill, encoding="utf-8")

    proposals = dict(skill_proposals or {})
    proposal_meta: List[Dict[str, Any]] = []
    for name, body in proposals.items():
        filename = proposed_skill_filename(name)
        (path / filename).write_text(body, encoding="utf-8")
        proposal_meta.append(
            {
                "skill_name": name,
                "filename": filename,
                "sha256": _sha256_text(body),
            }
        )

    if proposed_memory is not None:
        (path / "proposed_MEMORY.md").write_text(proposed_memory, encoding="utf-8")

    (path / "report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (path / "report.md").write_text(_render_report_md(report), encoding="utf-8")

    manifest: Dict[str, Any] = {
        "schema": "openjiuwen-skill-sleep-staging",
        "schema_version": 2,
        "skill_name": skill_name,
        "accepted": report.accepted,
        "gate_action": report.gate_action,
        "baseline_skill_sha256": _sha256_text(baseline_skill),
        "proposed_skill_sha256": _sha256_text(proposed_skill or ""),
        "has_proposed_skill": proposed_skill is not None,
        "has_proposed_memory": proposed_memory is not None,
        "skill_proposals": proposal_meta,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    latest = path.parent / ".latest"
    latest.write_text(str(path.resolve()), encoding="utf-8")
    return path


def load_manifest(staging_dir: Path | str) -> Dict[str, Any]:
    path = Path(staging_dir) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def read_proposed_skill(staging_dir: Path | str) -> str:
    path = Path(staging_dir) / "proposed_SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"proposed_SKILL.md missing in {staging_dir}")
    return path.read_text(encoding="utf-8")


def list_skill_proposals(staging_dir: Path | str) -> Dict[str, str]:
    """Return skill_name -> proposed body from staging (multi + legacy)."""
    path = Path(staging_dir)
    manifest = load_manifest(path)
    out: Dict[str, str] = {}
    for item in manifest.get("skill_proposals") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("skill_name") or "").strip()
        filename = str(item.get("filename") or "").strip()
        if not name or not filename:
            continue
        file_path = path / filename
        if not file_path.exists():
            continue
        body = file_path.read_text(encoding="utf-8")
        expected = str(item.get("sha256") or "")
        if expected and _sha256_text(body) != expected:
            raise ValueError(f"sha256 mismatch for staged skill {name}")
        out[name] = body
    if not out and manifest.get("has_proposed_skill"):
        name = str(manifest.get("skill_name") or "").strip()
        if name:
            out[name] = read_proposed_skill(path)
    return out


def _render_report_md(report: SleepReport) -> str:
    lines = [
        f"# Sleep report — night {report.night}",
        "",
        f"- project: `{report.project}`",
        f"- sessions: {report.n_sessions}",
        f"- tasks: {report.n_tasks}",
        f"- baseline: {report.baseline_score:.4f}",
        f"- candidate: {report.candidate_score:.4f}",
        f"- accepted: {report.accepted}",
        f"- gate: {report.gate_action}",
        f"- holdout_leaked: {report.holdout_leaked}",
        "",
        "## Applied edits",
    ]
    if report.edits:
        for edit in report.edits:
            lines.append(f"- [{edit.op}] {edit.content} ({edit.rationale})")
    else:
        lines.append("- (none)")
    if report.skill_groups:
        lines.extend(["", "## Skill groups"])
        for group in report.skill_groups:
            lines.append(
                f"- `{group.skill_name}` status={group.status} accepted={group.accepted} "
                f"tasks={group.n_tasks} gate={group.gate_action} reason={group.reason}"
            )
    if report.notes:
        lines.extend(["", "## Notes", *[f"- {note}" for note in report.notes]])
    return "\n".join(lines) + "\n"
