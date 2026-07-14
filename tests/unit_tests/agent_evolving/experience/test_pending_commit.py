# coding: utf-8
"""Tests for canonical staged pending-change commit helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience.types import PendingChange
from openjiuwen.agent_evolving.experience.lifecycle import PendingCommitResult
from openjiuwen.agent_evolving.experience.common import commit_pending_change


def _make_record(record_id: str, *, content: str = "fix issue") -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source="execution_failure",
        timestamp="2026-01-01T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


def _make_pending(
    change_id: str,
    *,
    skill_name: str = "skill-a",
    change_type: str = "skill_experience_entry",
) -> PendingChange:
    return PendingChange(
        operator_id=f"skill_experience_{skill_name}",
        skill_name=skill_name,
        change_type=change_type,
        payload=[_make_record(f"{change_id}-record")],
        created_at="2026-01-01T00:00:00+00:00",
        change_id=change_id,
    )


def _prepare_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")


def test_pending_commit_result_is_exposed_from_experience_package():
    assert PendingCommitResult(applied_count=1, pending_count=0) == PendingCommitResult(
        applied_count=1,
        pending_count=0,
    )


@pytest.mark.asyncio
async def test_commit_pending_change_clears_snapshot_on_success(tmp_path: Path):
    root = tmp_path / "skills"
    _prepare_skill(root, "skill-a")
    store = EvolutionStore(str(root))
    pending = _make_pending("pending-1")
    pending_by_id = {pending.change_id: pending}

    result = await commit_pending_change(pending_by_id, pending.change_id, store=store)

    assert result.applied_count == 1
    assert result.pending_count == 0
    assert pending.change_id not in pending_by_id
    log = await store.load_evolution_log("skill-a")
    assert [record.id for record in log.entries] == ["pending-1-record"]


@pytest.mark.asyncio
async def test_commit_pending_change_retains_unwritten_tail_on_record_failure(tmp_path: Path):
    root = tmp_path / "skills"
    _prepare_skill(root, "skill-a")
    store = EvolutionStore(str(root))
    pending = PendingChange(
        operator_id="skill_experience_skill-a",
        skill_name="skill-a",
        change_type="skill_experience_entry",
        payload=[_make_record("ev_1"), _make_record("ev_2")],
        created_at="2026-01-01T00:00:00+00:00",
        change_id="pending-2",
    )
    pending_by_id = {pending.change_id: pending}

    original_append = store.append_record

    async def append_then_fail(skill_name: str, record: EvolutionRecord) -> None:
        if record.id == "ev_2":
            raise OSError("disk full")
        await original_append(skill_name, record)

    store.append_record = append_then_fail

    result = await commit_pending_change(pending_by_id, pending.change_id, store=store)

    assert result.applied_count == 1
    assert result.pending_count == 1
    assert result.errors == ["disk full"]
    assert pending_by_id[pending.change_id] is pending
    assert [record.id for record in pending.payload] == ["ev_2"]
    log = await store.load_evolution_log("skill-a")
    assert [record.id for record in log.entries] == ["ev_1"]

    store.append_record = original_append
    retried = await commit_pending_change(pending_by_id, pending.change_id, store=store)

    assert retried.applied_count == 1
    assert retried.pending_count == 0
    assert pending.change_id not in pending_by_id
    log = await store.load_evolution_log("skill-a")
    assert [record.id for record in log.entries] == ["ev_1", "ev_2"]


@pytest.mark.asyncio
async def test_commit_pending_change_supports_legacy_experience_entry(tmp_path: Path):
    root = tmp_path / "skills"
    _prepare_skill(root, "skill-a")
    store = EvolutionStore(str(root))
    pending = _make_pending("pending-legacy", change_type="experience_entry")
    pending_by_id = {pending.change_id: pending}

    result = await commit_pending_change(pending_by_id, pending.change_id, store=store)

    assert result.applied_count == 1
    assert result.pending_count == 0
    assert pending.change_id not in pending_by_id


@pytest.mark.asyncio
async def test_commit_pending_change_rejects_missing_change_id(tmp_path: Path):
    root = tmp_path / "skills"
    _prepare_skill(root, "skill-a")
    store = EvolutionStore(str(root))

    with pytest.raises(KeyError, match="missing-change"):
        await commit_pending_change({}, "missing-change", store=store)


@pytest.mark.asyncio
async def test_commit_pending_change_reaches_team_skill_experience_entry_path(tmp_path: Path):
    root = tmp_path / "skills"
    _prepare_skill(root, "team-skill-a")
    store = EvolutionStore(str(root))
    pending = _make_pending(
        "pending-team",
        skill_name="team-skill-a",
        change_type="skill_experience_entry",
    )
    pending_by_id = {pending.change_id: pending}

    result = await commit_pending_change(pending_by_id, pending.change_id, store=store)

    assert result.applied_count == 1
    assert result.pending_count == 0
    assert pending.change_id not in pending_by_id
    log = await store.load_evolution_log("team-skill-a")
    assert [record.id for record in log.entries] == ["pending-team-record"]
