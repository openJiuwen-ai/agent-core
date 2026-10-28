# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-task SkillsBench evolution: no validation split, five iterations.

Each task is rolled out alone. Skills shipped under ``environment/skills/``
are evolved as a named bundle (joint multi-skill): one rollout sees every
skill, reflect attributes patches by ``skill_name``, and a revision is
accepted atomically for the whole bundle. When the package has no skill
files, the run falls back to ``skill_inits`` / ``skill_init``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from openjiuwen.agent_evolving.skill_train.aggregate import MergeSettings, merge_patches
from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig
from openjiuwen.agent_evolving.skill_train.envs.base import EnvAdapter
from openjiuwen.agent_evolving.skill_train.select import rank_and_select
from openjiuwen.agent_evolving.skill_train.skill_bundle import (
    DEFAULT_SKILL_NAME,
    SkillBundle,
    copy_bundle,
    format_skill_bundle,
    group_patches_by_skill,
    is_default_singleton,
    load_skill_bundle,
    primary_skill,
)
from openjiuwen.agent_evolving.skill_train.skill_patch import apply_patch_with_report
from openjiuwen.agent_evolving.skill_train.trainer import SkillReflACTTrainer
from openjiuwen.agent_evolving.skill_train.update_modes import (
    get_payload_items,
    is_full_rewrite_minibatch_mode,
)
from openjiuwen.core.common.logging import logger


def _task_items(adapter: Any) -> list[dict]:
    """Every task package, without a train/val/test cut."""
    loader = adapter.dataloader
    source = str(loader.paths.data_path or loader.paths.split_dir or "").strip()
    items = list(loader.load_raw_items(source))
    limit = int(loader.paths.limit or 0)
    if limit > 0:
        items = items[:limit]
    return items


def _as_merge_patch(raw: dict) -> dict:
    """Lift ``patch.edits`` onto the object merge/apply read.

    The analyst stores the edit list under ``patch`` and provenance on the
    envelope. A one-item merge returns that envelope unchanged, so the edits
    have to be visible at the top level before aggregation.
    """
    nested = raw.get("patch")
    if not isinstance(nested, dict):
        return raw
    body = dict(nested)
    body.setdefault("source_type", raw.get("source_type"))
    if raw.get("skill_name"):
        body.setdefault("skill_name", raw.get("skill_name"))
    return body


def _split_by_source(patches: list[dict], source: str) -> list[dict]:
    return [patch for patch in patches if patch.get("source_type") == source]


def _applied(report: list[dict]) -> int:
    return sum(1 for entry in report if str(entry.get("status", "")).startswith("applied"))


def _task_base_skills(item: dict, fallback: SkillBundle) -> SkillBundle:
    """Load named skills from the task package; else use *fallback*."""
    loaded: SkillBundle = {}
    for record in item.get("skills") or []:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        path = Path(str(record.get("path") or ""))
        if not name or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if text.strip():
            loaded[name] = text if text.endswith("\n") else text + "\n"
    if loaded:
        return loaded
    return copy_bundle(fallback)


def _task_base_skill(item: dict, fallback: str) -> str:
    """Legacy single-document view used by older tests."""
    bundle = _task_base_skills(item, {DEFAULT_SKILL_NAME: fallback})
    return primary_skill(bundle)


def _rewrite_report(ranked: dict, current: str) -> tuple[str | None, list[dict]]:
    """Install the first complete ``new_skill`` and ignore extra candidates."""
    reports: list[dict] = []
    chosen: str | None = None
    for index, item in enumerate(get_payload_items(ranked, "full_rewrite_minibatch"), 1):
        text = str(item.get("new_skill") or "").strip()
        entry = {"index": index, "op": "full_rewrite", "title": str(item.get("title") or "")}
        if not text:
            entry["status"] = "skipped_empty"
        elif text == current.strip():
            entry["status"] = "skipped_unchanged"
        elif chosen is None:
            chosen = text if text.endswith("\n") else text + "\n"
            entry["status"] = "applied_full_rewrite"
        else:
            entry["status"] = "skipped_extra_candidate"
        reports.append(entry)
    return chosen, reports


def _revise_one_skill(
    body: str,
    skill_patches: list[dict],
    *,
    edit_budget: int,
    merge_batch_size: int,
    analyst_workers: int,
    update_mode: str,
    task_id: str = "",
    skill_name: str = "",
) -> tuple[str | None, list[dict]]:
    """Merge/select/apply patches for a single named skill body."""
    if not skill_patches:
        return None, []
    merged = merge_patches(
        body,
        _split_by_source(skill_patches, "failure"),
        _split_by_source(skill_patches, "success"),
        MergeSettings(
            batch_size=merge_batch_size,
            workers=analyst_workers,
            update_mode=update_mode,
        ),
    )
    ranked = rank_and_select(
        body,
        merged,
        max_edits=edit_budget,
        update_mode=update_mode,
    )
    kept = len(get_payload_items(ranked, update_mode))
    logger.info(
        "[4/6 SELECT] id=%s skill=%s pool=%s kept=%s budget=%s",
        task_id,
        skill_name or "?",
        len(skill_patches),
        kept,
        edit_budget,
    )
    if kept <= 0:
        logger.info("[5/6 APPLY] id=%s skill=%s applied=0 changed=False", task_id, skill_name or "?")
        return None, []
    if is_full_rewrite_minibatch_mode(update_mode):
        candidate, report = _rewrite_report(ranked, body)
    else:
        candidate, report = apply_patch_with_report(body, ranked)
    changed = candidate is not None and _applied(report) > 0 and candidate != body
    logger.info(
        "[5/6 APPLY] id=%s skill=%s applied=%s changed=%s",
        task_id,
        skill_name or "?",
        _applied(report),
        changed,
    )
    if not changed:
        return None, report
    return candidate, report


def _revise(
    adapter: EnvAdapter,
    item: dict,
    result: dict,
    skill: str | Mapping[str, str],
    step_dir: Path,
    *,
    seed: int,
    edit_budget: int,
    merge_batch_size: int,
    analyst_workers: int,
    update_mode: str,
) -> str | SkillBundle | None:
    """Return the next skill (string or bundle) when reflection produced a revision."""
    if isinstance(skill, Mapping):
        current = {str(k): str(v) for k, v in skill.items()}
    else:
        current = {DEFAULT_SKILL_NAME: str(skill)}
    skill_view = format_skill_bundle(current)

    raw = adapter.reflect(
        [result],
        skill_view,
        str(step_dir),
        prediction_dir=str(step_dir / "rollout" / "predictions"),
        patches_dir=str(step_dir / "patches"),
        random_seed=seed,
        skills=current,
    )
    lifted = [_as_merge_patch(patch) for patch in raw if patch]
    # Preserve skill_name on the merge-facing dict for grouping.
    for patch, original in zip(lifted, [p for p in raw if p]):
        if original.get("skill_name") and not patch.get("skill_name"):
            patch["skill_name"] = original["skill_name"]

    by_skill = group_patches_by_skill(lifted, skill_names=current)
    task_id = str(item.get("id") or "")
    if not lifted:
        logger.info("[4/6 SELECT] id=%s kept=0 reason=no_patches", task_id)
        logger.info("[5/6 APPLY] id=%s applied=0 changed=False", task_id)
    candidate = copy_bundle(current)
    all_reports: list[dict] = []
    changed = False
    for name, body in current.items():
        updated, report = _revise_one_skill(
            body,
            by_skill.get(name) or [],
            edit_budget=edit_budget,
            merge_batch_size=merge_batch_size,
            analyst_workers=analyst_workers,
            update_mode=update_mode,
            task_id=task_id,
            skill_name=name,
        )
        for entry in report:
            row = dict(entry)
            row["skill_name"] = name
            all_reports.append(row)
        if updated is not None:
            candidate[name] = updated
            changed = True
            (step_dir / f"candidate_skill__{name}.md").write_text(updated, encoding="utf-8")

    (step_dir / "apply_report.json").write_text(
        json.dumps(all_reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not changed:
        return None
    (step_dir / "candidate_skill.md").write_text(format_skill_bundle(candidate), encoding="utf-8")
    if is_default_singleton(candidate) and is_default_singleton(current):
        return candidate[DEFAULT_SKILL_NAME]
    return candidate


def _save_bundle_version(out_dir: Path, step: int, skills: SkillBundle) -> list[str]:
    """Persist per-skill versions; return paths written."""
    paths: list[str] = []
    if is_default_singleton(skills):
        path = out_dir / f"skill_v{step:03d}.md"
        path.write_text(next(iter(skills.values())), encoding="utf-8")
        return [str(path)]
    for name, body in skills.items():
        skill_dir = out_dir / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / f"skill_v{step:03d}.md"
        path.write_text(body, encoding="utf-8")
        paths.append(str(path))
    return paths


def _save_best_bundle(out_dir: Path, skills: SkillBundle) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    text = format_skill_bundle(skills) if len(skills) > 1 else primary_skill(skills)
    (out_dir / "final_skill.md").write_text(text, encoding="utf-8")
    (out_dir / "best_skill.md").write_text(text, encoding="utf-8")
    if len(skills) > 1 or not is_default_singleton(skills):
        best_dir = out_dir / "best_skills"
        best_dir.mkdir(parents=True, exist_ok=True)
        for name, body in skills.items():
            # Task-package layout: best_skills/<name>/SKILL.md (like environment/skills/<name>/).
            skill_dir = best_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def _as_bundle(value: str | Mapping[str, str]) -> SkillBundle:
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    return {DEFAULT_SKILL_NAME: str(value)}


def evolve_task(
    item: dict,
    *,
    initial_skill: str = "",
    initial_skills: SkillBundle | None = None,
    iterations: int,
    out_dir: Path,
    rollout,
    revise,
) -> dict:
    """Roll one task for *iterations* rounds, keeping the current skill when reflection produces none.

    Best-skill selection is by ``(hard, soft)``. Equal scores prefer the later
    iteration (the skill that just rolled out), so a rewrite that merely ties
    the previous score still becomes ``best_skill``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    skills = copy_bundle(initial_skills) if initial_skills else _as_bundle(initial_skill)
    versions: list[str] = []
    passed_iteration: int | None = None
    reason = "budget"
    task_id = str(item.get("id") or "task")
    ran = 0
    best_skills = copy_bundle(skills)
    best_hard = -1.0
    best_soft = -1.0
    best_iteration: int | None = None
    total = max(1, iterations)

    for step in range(1, total + 1):
        ran = step
        step_dir = out_dir / f"iter_{step:03d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[1/6 ROLLOUT] start iter=%s/%s id=%s", step, total, task_id)
        # Legacy callbacks expect a string for the default singleton bundle.
        live = primary_skill(skills) if is_default_singleton(skills) else skills
        rows = list(rollout([item], live, str(step_dir / "rollout")))
        result = rows[0] if rows else {"id": task_id, "hard": 0, "soft": 0.0}
        hard = float(result.get("hard") or 0)
        soft = float(result.get("soft") or 0)
        logger.info(
            "[1/6 ROLLOUT] done iter=%s/%s id=%s hard=%s soft=%s",
            step,
            total,
            task_id,
            result.get("hard", 0),
            result.get("soft", 0.0),
        )
        if hard == 1 and passed_iteration is None:
            passed_iteration = step
        # Tie-break toward the later iteration when (hard, soft) are equal.
        if best_iteration is None or (hard, soft) >= (best_hard, best_soft):
            best_skills = copy_bundle(skills)
            best_hard = hard
            best_soft = soft
            best_iteration = step
        revised = revise(item, result, live, step_dir, step)
        if revised:
            skills = _as_bundle(revised)
            versions.extend(_save_bundle_version(out_dir, step, skills))
        logger.info(
            "[6/6 GATE] iter=%s/%s id=%s action=%s best_iter=%s best_hard=%s",
            step,
            total,
            task_id,
            "revise" if revised else "keep",
            best_iteration,
            best_hard,
        )

    _save_best_bundle(out_dir, best_skills)
    return {
        "id": task_id,
        "passed_iteration": passed_iteration,
        "stopped_reason": reason,
        "versions": versions,
        "final_skill": str(out_dir / "final_skill.md"),
        "best_skill": str(out_dir / "best_skill.md"),
        "best_iteration": best_iteration,
        "best_hard": best_hard,
        "best_soft": best_soft,
        "n_iterations_run": ran,
        "skill_names": list(best_skills.keys()),
    }


def _store_summary(out_root: Path, records: list[dict], iterations: int) -> dict:
    """Write ``summary.json``, merging tasks already saved by a sibling run."""
    path = out_root / "summary.json"
    lock_path = out_root / ".summary.lock"
    out_root.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            time.sleep(0.2)
    try:
        merged: dict[str, dict] = {}
        if path.is_file():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                old = {}
            for row in old.get("tasks") or []:
                if isinstance(row, dict) and row.get("id"):
                    merged[str(row["id"])] = row
        for row in records:
            merged[str(row.get("id") or "")] = row
        tasks = [row for key, row in merged.items() if key]
        summary = {
            "env": "skillsbench",
            "iterations": iterations,
            "n_tasks": len(tasks),
            "n_passed": sum(1 for row in tasks if row.get("passed_iteration")),
            "multi_skill": True,
            "tasks": tasks,
        }
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


def run_per_task_training(*, config: SkillTrainConfig, trainer: SkillReflACTTrainer, adapter: EnvAdapter) -> dict:
    """Evolve a separate skill bundle for every SkillsBench task."""
    cfg = config.to_trainer_cfg()
    backend = trainer.bind_clients(cfg)
    EnvAdapter.setup(adapter, cfg)

    items = _task_items(adapter)
    if not items:
        raise ValueError("SkillsBench per-task training found no tasks")

    fallback = load_skill_bundle(
        skill_init=str(config.skill_init or ""),
        skill_inits=config.skill_inits,
    )
    out_root = Path(config.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    iterations = max(1, int(config.num_epochs or 5))
    update_mode = str(cfg.get("skill_update_mode") or "patch")
    logger.info(
        "[skill_train] target_backend=%s env=skillsbench tasks=%s iterations=%s update_mode=%s",
        backend,
        len(items),
        iterations,
        update_mode,
    )

    def _rollout(batch, skill, out_dir):
        if isinstance(skill, Mapping):
            bundle = {str(k): str(v) for k, v in skill.items()}
            return adapter.rollout(batch, format_skill_bundle(bundle), out_dir, skills=bundle)
        return adapter.rollout(batch, skill, out_dir)

    def _revise_bound(item, result, skill, step_dir, step):
        return _revise(
            adapter,
            item,
            result,
            skill,
            step_dir,
            seed=int(config.seed) + step,
            edit_budget=int(config.edit_budget),
            merge_batch_size=int(config.merge_batch_size),
            analyst_workers=int(config.analyst_workers),
            update_mode=update_mode,
        )

    records: list[dict] = []
    for index, item in enumerate(items, start=1):
        task_id = str(item.get("id") or f"task_{index}")
        logger.info("[skillsbench] task %s/%s id=%s", index, len(items), task_id)
        initial = _task_base_skills(item, fallback)
        record = evolve_task(
            item,
            initial_skills=initial,
            iterations=iterations,
            out_dir=out_root / "tasks" / task_id,
            rollout=_rollout,
            revise=_revise_bound,
        )
        records.append(record)
        logger.info(
            "[skillsbench] id=%s stop=%s passed_at=%s best_iter=%s best_hard=%s skills=%s versions=%s",
            task_id,
            record["stopped_reason"],
            record["passed_iteration"],
            record["best_iteration"],
            record["best_hard"],
            record.get("skill_names"),
            len(record["versions"]),
        )

    return _store_summary(out_root, records, iterations)
