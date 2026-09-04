# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run one offline sleep cycle: harvest -> mine -> consolidate -> stage -> adopt."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.skill_train.llm_client import ChatLLMClient
from openjiuwen.agent_evolving.skill_train.sleep.adopt import (
    AdoptResult,
    adopt_all_staged_skills,
)
from openjiuwen.agent_evolving.skill_train.sleep.backend import Backend, build_backend
from openjiuwen.agent_evolving.skill_train.sleep.config import SleepConfig
from openjiuwen.agent_evolving.skill_train.sleep.harvest import harvest_otlp_trajectories
from openjiuwen.agent_evolving.skill_train.sleep.memory import ensure_skill_scaffold
from openjiuwen.agent_evolving.skill_train.sleep.mine import group_tasks_by_skill_hint, mine
from openjiuwen.agent_evolving.skill_train.sleep.multi_skill import (
    SkillGroup,
    accepted_group_skills,
    consolidate_groups,
    skill_group_reports,
)
from openjiuwen.agent_evolving.skill_train.sleep.staging import new_staging_dir, write_staging
from openjiuwen.agent_evolving.skill_train.sleep.state import SleepState, _now_iso
from openjiuwen.agent_evolving.skill_train.sleep.types import EditRecord, SleepReport, TaskRecord
from openjiuwen.core.common.logging import logger


@dataclass
class CycleOutcome:
    night: int
    report: SleepReport
    staging_dir: Optional[str] = None
    adopted: Optional[AdoptResult] = None
    adopted_skills: List[AdoptResult] = field(default_factory=list)
    tasks: List[TaskRecord] = field(default_factory=list)
    dry_run: bool = False


def _run_read_skill(store: EvolutionStore, name: str) -> str:
    import asyncio

    async def _read() -> str:
        return await store.read_skill_content(name) or ""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_read())
    raise RuntimeError(
        "_run_read_skill cannot be called from a running event loop"
    )


def _load_baseline_for_skill(
    cfg: SleepConfig,
    store: Optional[EvolutionStore],
    skill_name: str,
) -> str:
    content = ""
    if skill_name == cfg.skill_name and cfg.skill_init and Path(cfg.skill_init).exists():
        content = Path(cfg.skill_init).read_text(encoding="utf-8")
    elif store is not None and store.skill_exists(skill_name):
        content = _run_read_skill(store, skill_name)
    return ensure_skill_scaffold(
        content,
        name=skill_name,
        description="Skill consolidated by skill_train sleep",
    )


def _synthesize_rubrics(backend: Backend, tasks: List[TaskRecord]) -> int:
    """Replace heuristic rubrics with backend-synthesized ones; return count changed."""
    changed = 0
    for task in tasks:
        if task.reference_kind != "rubric":
            continue
        try:
            rubric = backend.synthesize_rubric(task)
        except Exception as exc:  # keep the night alive on a single bad call
            logger.warning("[skill_sleep] rubric synthesis failed for %s: %s", task.id, exc)
            continue
        rubric = (rubric or "").strip()
        if rubric and rubric != task.reference:
            task.reference = rubric
            changed += 1
    return changed


def run_sleep_cycle(
    cfg: Optional[SleepConfig] = None,
    *,
    dry_run: bool = False,
    backend: Optional[Backend] = None,
    target_client: Optional[ChatLLMClient] = None,
    optimizer_client: Optional[ChatLLMClient] = None,
    seed_tasks: Optional[List[TaskRecord]] = None,
    evolution_store: Optional[EvolutionStore] = None,
    clock: Optional[float] = None,
) -> CycleOutcome:
    """Run one full sleep cycle and return the outcome.

    Tasks are grouped by detected skill hint only. Each group consolidates
    independently against that skill's baseline. Gate-accepted groups are
    staged and persisted via EvolutionStore. Tasks without a unique skill
    hint are skipped (no fallback managed skill).

    ``dry_run``: harvest+mine+consolidate but do not stage/adopt.
    """
    cfg = cfg or SleepConfig()
    if not cfg.project:
        cfg.project = os.getcwd()

    state_dir = cfg.resolved_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    state = SleepState.load(state_dir / "state.json")
    night = state.begin_night(clock)
    started = _now_iso(clock)

    store = evolution_store
    if store is None and cfg.skills_base_dir:
        store = EvolutionStore(cfg.skills_base_dir)

    backend = backend or build_backend(
        cfg.backend,
        target_client=target_client,
        optimizer_client=optimizer_client,
        preferences=cfg.preferences,
    )

    if cfg.progress:
        logger.info("[skill_sleep] night %s start project=%s backend=%s", night, cfg.project, backend.name)

    digests = []
    if seed_tasks is None:
        digests = harvest_otlp_trajectories(cfg)
        tasks = mine(
            digests,
            max_tasks=cfg.max_tasks_per_night,
            val_fraction=cfg.val_fraction,
            test_fraction=cfg.test_fraction,
            seed=cfg.seed,
        )
    else:
        tasks = list(seed_tasks)

    baseline_memory = cfg.memory_init or ""
    notes: List[str] = []
    if tasks and str(cfg.rubric_synthesis or "off").strip().lower() == "llm":
        synthesized = _synthesize_rubrics(backend, tasks)
        if synthesized:
            notes.append(f"rubric_synthesized={synthesized}")
    if not tasks:
        notes.append("no tasks mined from OTLP trajectories")
        report = SleepReport(
            night=night,
            project=cfg.project,
            started_at=started,
            ended_at=_now_iso(clock),
            n_sessions=len(digests),
            n_tasks=0,
            notes=notes,
        )
        state.record_night({"night": night, "accepted": False, "n_tasks": 0})
        state.set_last_harvest(cfg.project)
        state.save()
        return CycleOutcome(night=night, report=report, tasks=[], dry_run=dry_run)

    grouped = group_tasks_by_skill_hint(tasks)
    skipped_no_hint = sum(1 for task in tasks if not (task.skill_hint or "").strip())
    if skipped_no_hint:
        notes.append(f"skipped_no_skill_hint={skipped_no_hint}")
    if not grouped:
        notes.append("no_detected_skills_to_update")
        report = SleepReport(
            night=night,
            project=cfg.project,
            started_at=started,
            ended_at=_now_iso(clock),
            n_sessions=len(digests),
            n_tasks=len(tasks),
            notes=notes,
        )
        state.record_night({"night": night, "accepted": False, "n_tasks": len(tasks)})
        state.set_last_harvest(cfg.project)
        state.save()
        return CycleOutcome(night=night, report=report, tasks=tasks, dry_run=dry_run)

    skill_groups: List[SkillGroup] = []
    for name, group_tasks in grouped.items():
        skill_groups.append(
            SkillGroup(
                skill_name=name,
                skill=_load_baseline_for_skill(cfg, store, name),
                tasks=group_tasks,
            )
        )
    if cfg.progress:
        logger.info(
            "[skill_sleep] night %s skill_groups=%s",
            night,
            [g.skill_name for g in skill_groups],
        )

    outcomes = consolidate_groups(
        backend,
        skill_groups,
        baseline_memory,
        edit_budget=cfg.edit_budget,
        gate_metric=cfg.gate_metric,
        gate_mixed_weight=cfg.gate_mixed_weight,
        gate_no_regression=cfg.gate_no_regression,
        gate_mode=cfg.gate_mode,
        evolve_skill=cfg.evolve_skill,
        night=night,
    )
    group_rows = skill_group_reports(outcomes)
    accepted_skills = accepted_group_skills(outcomes) if cfg.evolve_skill else {}

    all_edits: List[EditRecord] = []
    all_rejected: List[EditRecord] = []
    all_unmatched: List[EditRecord] = []
    all_trials: List[dict] = []
    holdout_leaked = False
    for outcome in outcomes.values():
        if outcome.result is None:
            continue
        all_edits.extend(outcome.result.applied_edits)
        all_rejected.extend(outcome.result.rejected_edits)
        all_unmatched.extend(outcome.result.unmatched_edits)
        all_trials.extend(outcome.result.gate_trials)
        holdout_leaked = holdout_leaked or outcome.result.holdout_leaked

    any_accepted = bool(accepted_skills)
    # Aggregate scores from accepted groups (or first consolidated group).
    score_source = None
    for name in accepted_skills:
        score_source = outcomes[name].result
        break
    if score_source is None:
        for outcome in outcomes.values():
            if outcome.result is not None:
                score_source = outcome.result
                break

    if not all_edits and not any_accepted:
        notes.append("no_edits_or_not_accepted")
    for row in group_rows:
        if row.status != "consolidated":
            notes.append(f"{row.skill_name}:{row.status}:{row.reason}")

    report = SleepReport(
        night=night,
        project=cfg.project,
        started_at=started,
        ended_at=_now_iso(clock),
        n_sessions=len(digests),
        n_tasks=len(tasks),
        n_replayed=len(tasks),
        baseline_score=score_source.baseline_score if score_source else 0.0,
        candidate_score=score_source.candidate_score if score_source else 0.0,
        accepted=any_accepted,
        gate_action=(
            "multi_skill_accept" if any_accepted else (score_source.gate_action if score_source else "reject")
        ),
        holdout_leaked=holdout_leaked,
        edits=all_edits,
        rejected_edits=all_rejected,
        unmatched_edits=all_unmatched,
        skill_groups=group_rows,
        notes=notes,
        gate_no_regression=cfg.gate_no_regression,
        gate_trials=all_trials,
    )

    staging_dir: Optional[str] = None
    adopted_skills: List[AdoptResult] = []
    adopted: Optional[AdoptResult] = None
    if not dry_run:
        staging_path = new_staging_dir(cfg.resolved_staging_root(), clock=clock)
        write_staging(
            staging_path,
            report=report,
            proposed_skill=None,
            baseline_skill="",
            skill_name="",
            skill_proposals=accepted_skills,
        )
        staging_dir = str(staging_path)
        if accepted_skills:
            if store is None:
                raise ValueError(
                    "gate accepted skill update(s) but EvolutionStore is missing; "
                    "pass skills_base_dir / evolution_store so skills can be persisted"
                )
            adopted_skills = adopt_all_staged_skills(staging_path, store=store)
            adopted = adopted_skills[0] if adopted_skills else None

    state.add_to_archive([task.to_dict() for task in tasks])
    state.record_night(
        {
            "night": night,
            "accepted": report.accepted,
            "n_tasks": report.n_tasks,
            "skill_groups": [row.to_dict() for row in group_rows],
            "baseline_score": report.baseline_score,
            "candidate_score": report.candidate_score,
            "staging_dir": staging_dir,
            "timestamp": time.time() if clock is None else clock,
        }
    )
    state.set_last_harvest(cfg.project)
    state.save()

    if cfg.progress:
        logger.info(
            "[skill_sleep] night %s done accepted=%s groups=%s staging=%s",
            night,
            report.accepted,
            [r.skill_name for r in group_rows],
            staging_dir,
        )
    return CycleOutcome(
        night=night,
        report=report,
        staging_dir=staging_dir,
        adopted=adopted,
        adopted_skills=adopted_skills,
        tasks=tasks,
        dry_run=dry_run,
    )
