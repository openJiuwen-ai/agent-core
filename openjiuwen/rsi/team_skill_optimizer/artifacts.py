# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Filesystem artifacts for offline Team Skill optimization."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.rsi.config import TeamSkillOptimizerConfig

OPTIMIZER_SOURCE = "auto_coordinating_harness_team_skill_optimizer"
ANALYSIS_SIGNAL_SOURCE = "auto_coordinating_harness_analysis"
CURRENT_TEAM_SKILL_DIR = "current_team_skill"
CURRENT_TEAM_SKILL_REF = "current_team_skill_ref.yaml"


@dataclass(frozen=True, slots=True)
class SkillRef:
    """Resolved source Team Skill reference."""

    skill_dir: Path
    skill_name: str
    skill_md_path: Path


@dataclass(frozen=True, slots=True)
class OptimizationArtifacts:
    """Filesystem layout for one optimization attempt."""

    run_dir: Path
    candidate_root: Path
    candidate_skill_dir: Path
    publish_dir: Path

    @property
    def optimization_id(self) -> str:
        return self.run_dir.name


def resolve_source_skill(team_skill_ref_path: str) -> SkillRef:
    if not str(team_skill_ref_path or "").strip():
        raise RuntimeError("team_skill_ref_path is required")
    path = Path(team_skill_ref_path).expanduser().resolve()
    skill_dir = path.parent if path.is_file() else path
    if not skill_dir.is_dir():
        raise RuntimeError(f"team_skill_ref_path does not resolve to a directory: {team_skill_ref_path}")

    store = EvolutionStore(str(skill_dir.parent))
    skill_md = store.find_skill_md(skill_dir)
    if skill_md is None:
        raise RuntimeError(f"Team Skill directory must contain SKILL.md or a markdown entrypoint: {skill_dir}")
    return SkillRef(skill_dir=skill_dir, skill_name=skill_dir.name, skill_md_path=skill_md)


def allocate_optimization_dir(output_root: Path) -> Path:
    index = 1
    while True:
        run_dir = output_root / f"tso_{index:03d}"
        if not run_dir.exists():
            run_dir.mkdir(parents=True)
            return run_dir
        index += 1


def prepare_candidate_workspace(
    *,
    output_root: Path,
    run_dir: Path,
    source_ref: SkillRef,
) -> OptimizationArtifacts:
    candidate_root_base = _candidate_root_base(output_root)
    candidate_root = candidate_root_base / run_dir.name / "c1"
    candidate_skill_dir = candidate_root / source_ref.skill_name
    candidate_root.mkdir(parents=True, exist_ok=True)
    copy_team_skill_dir(source_ref.skill_dir, candidate_skill_dir)
    return OptimizationArtifacts(
        run_dir=run_dir,
        candidate_root=candidate_root,
        candidate_skill_dir=candidate_skill_dir,
        publish_dir=output_root / CURRENT_TEAM_SKILL_DIR,
    )


def _candidate_root_base(output_root: Path) -> Path:
    # In orchestrator workspaces, avoid nesting candidates under
    # <team>/team_skills/... because Windows path length is tight.
    team_workspace = output_root.parent
    workspace_root = team_workspace.parent
    if output_root.name == "team_skills" and workspace_root.name == "workspace":
        return workspace_root / "tc"
    return output_root / "tc"


def copy_team_skill_dir(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in {"__pycache__", ".pytest_cache"} or name.endswith(".pyc")}

    shutil.copytree(source_dir, target_dir, ignore=ignore)


def publish_candidate(candidate_skill_dir: Path, publish_dir: Path) -> None:
    output_root = publish_dir.parent
    tmp_dir = output_root / f".{publish_dir.name}_tmp_{uuid.uuid4().hex[:8]}"
    backup_dir = output_root / f".{publish_dir.name}_backup_{uuid.uuid4().hex[:8]}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    shutil.copytree(candidate_skill_dir, tmp_dir)
    try:
        if publish_dir.exists():
            publish_dir.rename(backup_dir)
        tmp_dir.rename(publish_dir)
    except Exception:
        if publish_dir.exists():
            shutil.rmtree(publish_dir)
        if backup_dir.exists():
            backup_dir.rename(publish_dir)
        raise
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def write_current_ref(
    *,
    output_root: Path,
    returned_path: str,
    optimization_ref_path: str,
) -> None:
    write_yaml(
        output_root / CURRENT_TEAM_SKILL_REF,
        {
            "team_skill_ref_path": returned_path,
            "source_optimization_ref_path": optimization_ref_path,
            "updated_at": now_iso(),
        },
    )


def write_metadata(
    *,
    run_dir: Path,
    status: str,
    config: TeamSkillOptimizerConfig,
    eval_ref_path: str,
    analysis_result_path: str,
    team_skill_ref_path: str,
    source_skill_ref: SkillRef | None = None,
    selected_issue_ids: list[str] | None = None,
    skipped_warnings: list[dict[str, Any]] | None = None,
    source_issues_path: str = "",
    signal_count: int = 0,
    request_id: str = "",
    record_ids: list[str] | None = None,
    applied_count: int = 0,
    candidate_path: str = "",
    returned_path: str = "",
    trajectory_trace_paths: list[str] | None = None,
    error: str = "",
) -> None:
    metadata = {
        "optimizer": OPTIMIZER_SOURCE,
        "optimization_id": run_dir.name,
        "status": status,
        "created_at": now_iso(),
        "source_eval_ref_path": eval_ref_path,
        "source_analysis_result_path": analysis_result_path,
        "source_issues_path": source_issues_path,
        "source_team_skill_ref_path": team_skill_ref_path,
        "source_skill_name": source_skill_ref.skill_name if source_skill_ref is not None else "",
        "source_skill_dir": str(source_skill_ref.skill_dir) if source_skill_ref is not None else "",
        "selected_issue_ids": list(selected_issue_ids or []),
        "skipped_warnings": list(skipped_warnings or []),
        "signal_count": signal_count,
        "request_id": request_id,
        "record_ids": list(record_ids or []),
        "generated_record_count": len(record_ids or []),
        "applied_count": applied_count,
        "candidate_path": candidate_path,
        "returned_team_skill_ref_path": returned_path,
        "language": config.language,
        "auto_approve": config.auto_approve,
        "trajectory_trace_paths": list(trajectory_trace_paths or []),
    }
    if error:
        metadata["error"] = error
    write_yaml(run_dir / "optimization_metadata.yaml", metadata)
    write_yaml(
        run_dir / "team_skill_optimization_ref.yaml",
        {
            "optimization_id": run_dir.name,
            "status": status,
            "optimized_team_skill_ref_path": returned_path,
            "source_eval_ref_path": eval_ref_path,
            "source_analysis_result_path": analysis_result_path,
            "source_team_skill_ref_path": team_skill_ref_path,
            "optimization_metadata_path": str((run_dir / "optimization_metadata.yaml").resolve()),
            "updated_at": now_iso(),
        },
    )


def existing_current_or_source(output_root: Path, source_path: str) -> str:
    current_dir = output_root / CURRENT_TEAM_SKILL_DIR
    if current_dir.is_dir():
        return str(current_dir.resolve())
    if source_path:
        return str(Path(source_path).expanduser().resolve())
    return ""


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False)


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
