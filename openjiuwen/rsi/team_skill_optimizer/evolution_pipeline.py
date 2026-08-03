# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""agent_evolving pipeline adapter for offline Team Skill optimization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience import ExperienceManager, ExperienceScorer
from openjiuwen.agent_evolving.experience.online_orchestrator import OnlineEvolutionOrchestrator
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.signal.team import (
    get_team_signal_skill_content,
    get_team_trajectory_issues,
)
from openjiuwen.agent_evolving.trajectory import Trajectory
from openjiuwen.agent_evolving.updater import SingleDimUpdater
from openjiuwen.core.operator.skill_call import SkillExperienceOperator
from openjiuwen.rsi.config import TeamSkillOptimizerConfig
from openjiuwen.rsi.member_optimizer.agents.factory import (
    load_member_optimizer_model,
)
from openjiuwen.rsi.team_skill_optimizer.artifacts import (
    ANALYSIS_SIGNAL_SOURCE,
    OPTIMIZER_SOURCE,
)
from openjiuwen.rsi.team_skill_optimizer.experience_optimizer import (
    TEAM_SKILL_RECORD_LLM_POLICY,
    TeamSkillExperienceOptimizer,
)
from openjiuwen.rsi.team_skill_optimizer.trajectory_loader import (
    trajectory_trace_paths,
)

MIN_REGENERATED_BODY_CHARS = 50


@dataclass(frozen=True, slots=True)
class EvolutionPipelineResult:
    request_id: str
    record_ids: list[str]
    applied_count: int


async def evolve_and_rebuild(
    *,
    config: TeamSkillOptimizerConfig,
    candidate_root: Path,
    candidate_skill_dir: Path,
    run_dir: Path,
    skill_name: str,
    signals: list[EvolutionSignal],
    user_query: str,
    trajectory: Trajectory | None,
    eval_ref_path: str,
    analysis_result_path: str,
) -> EvolutionPipelineResult:
    model = _load_team_skill_optimizer_model(config.model_config_ref)
    model_name = _model_name(model)
    store = EvolutionStore(str(candidate_root))
    if not store.skill_exists(skill_name):
        raise RuntimeError(f"candidate Team Skill was not resolvable by EvolutionStore: {candidate_skill_dir}")

    generator = TeamSkillExperienceOptimizer(
        model,
        model_name,
        config.language,
        debug_dir=str(run_dir / "_debug"),
        record_llm_policy=TEAM_SKILL_RECORD_LLM_POLICY,
        evolution_store=store,
    )
    scorer = ExperienceScorer(model, model_name, config.language)
    skill_ops: dict[str, SkillExperienceOperator] = {}
    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        subject_kind="team-skill",
        language=config.language,
        skill_ops=skill_ops,
        pending_approval_snapshots={},
        pending_governance={},
    )
    orchestrator = OnlineEvolutionOrchestrator(
        store=store,
        updater=SingleDimUpdater(generator),
        manager=manager,
        skill_ops=skill_ops,
        request_id_prefix="auto_team_skill_evolve",
        stage_source=OPTIMIZER_SOURCE,
    )

    request = await orchestrator.evolve(
        skill_name=skill_name,
        signals=signals,
        messages=[],
        user_query=user_query,
        trajectory=trajectory,
        requires_approval=False,
        metadata={
            "source": ANALYSIS_SIGNAL_SOURCE,
            "eval_ref_path": eval_ref_path,
            "analysis_result_path": analysis_result_path,
            "trajectory_trace_paths": trajectory_trace_paths(trajectory),
        },
        source=OPTIMIZER_SOURCE,
    )
    records = _records_from_request(request)
    request_id = str(getattr(request, "request_id", "") or "") if request is not None else ""
    if not records and _is_analysis_trajectory_issue_signal(signals):
        records = await _generate_records_from_analysis_signals(
            generator=generator,
            store=store,
            skill_name=skill_name,
            signals=signals,
            trajectory=trajectory,
        )
        if records and not request_id:
            request_id = "auto_team_skill_analysis_trajectory_patch"
    if not records:
        return EvolutionPipelineResult(request_id=request_id, record_ids=[], applied_count=0)

    _write_records_audit(run_dir / "records.json", records)
    current_content = await store.read_skill_content(skill_name)
    frontmatter, current_body = _split_frontmatter(current_content)
    rebuilt_body = await generator.regenerate_body(
        skill_name,
        current_body,
        records,
        user_intent=user_query,
    )
    if rebuilt_body is None or len(rebuilt_body.strip()) < MIN_REGENERATED_BODY_CHARS:
        raise RuntimeError("TeamSkillExperienceOptimizer.regenerate_body returned empty or too-short body")

    write_ok = await store.write_skill_content(
        skill_name,
        _join_frontmatter(frontmatter, rebuilt_body),
    )
    if not write_ok:
        raise RuntimeError(f"failed to write rebuilt SKILL.md for {skill_name}")

    record_ids = [record.id for record in records]
    applied_count = await store.mark_records_applied(skill_name, record_ids)
    return EvolutionPipelineResult(
        request_id=request_id,
        record_ids=record_ids,
        applied_count=applied_count,
    )


async def read_candidate_skill_content(candidate_root: Path, skill_name: str) -> str:
    store = EvolutionStore(str(candidate_root))
    if not store.skill_exists(skill_name):
        raise RuntimeError(f"candidate Team Skill was not resolvable by EvolutionStore: {candidate_root / skill_name}")
    return await store.read_skill_content(skill_name)


def _load_team_skill_optimizer_model(model_config_ref: str) -> Any:
    try:
        return load_member_optimizer_model(model_config_ref)
    except Exception as exc:
        raise RuntimeError(f"failed to load team skill optimizer model from {model_config_ref}: {exc}") from exc


def _model_name(model: Any) -> str:
    model_config = getattr(model, "model_config", None)
    for attr in ("model_name", "model"):
        value = getattr(model_config, attr, None)
        if value:
            return str(value)
    return "default"


def _records_from_request(request: Any) -> list[EvolutionRecord]:
    if request is None:
        return []
    proposal = getattr(request, "proposal", None)
    records = getattr(proposal, "records", []) if proposal is not None else []
    return [record for record in records if isinstance(record, EvolutionRecord)]


def _is_analysis_trajectory_issue_signal(signals: list[EvolutionSignal]) -> bool:
    if not signals:
        return False
    for signal in signals:
        if signal.signal_type != "trajectory_issue":
            return False
        context = signal.context or {}
        if context.get("source") != ANALYSIS_SIGNAL_SOURCE:
            return False
        if not get_team_trajectory_issues(signal):
            return False
    return True


async def _generate_records_from_analysis_signals(
    *,
    generator: TeamSkillExperienceOptimizer,
    store: EvolutionStore,
    skill_name: str,
    signals: list[EvolutionSignal],
    trajectory: Trajectory | None,
) -> list[EvolutionRecord]:
    patch_trajectory = trajectory or Trajectory(
        execution_id="auto_team_skill_analysis_trajectory_patch",
        session_id="auto_team_skill_analysis_trajectory_patch",
        source="offline",
        steps=[],
    )
    current_skill_content = await store.read_skill_content(skill_name)
    records: list[EvolutionRecord] = []
    for signal in signals:
        record = await generator.generate_trajectory_patch(
            patch_trajectory,
            skill_name,
            get_team_signal_skill_content(signal) or current_skill_content,
            get_team_trajectory_issues(signal),
        )
        if record is None:
            continue
        await store.append_record(skill_name, record)
        records.append(record)
    return records


def _write_records_audit(path: Path, records: list[EvolutionRecord]) -> None:
    payload = {"records": [record.to_dict() for record in records]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _split_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---"):
        return "", content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return "", content
    return f"---{parts[1]}---\n", parts[2].lstrip("\r\n")


def _join_frontmatter(frontmatter: str, body: str) -> str:
    body_text = body.strip()
    if frontmatter:
        return f"{frontmatter.rstrip()}\n\n{body_text}\n"
    return f"{body_text}\n"
