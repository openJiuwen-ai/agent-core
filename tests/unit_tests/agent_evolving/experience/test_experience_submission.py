# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord, EvolutionTarget
from openjiuwen.agent_evolving.experience.submission import ExperienceSubmissionService
from openjiuwen.core.common.exception.errors import ValidationError


def _make_record(record_id: str = "ev_1") -> EvolutionRecord:
    record = EvolutionRecord.make(
        source="test",
        context="ctx",
        summary="summary",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content="old content",
            target=EvolutionTarget.BODY,
        ),
    )
    record.id = record_id
    return record


@pytest.mark.asyncio
async def test_apply_experience_drafts_persists_records():
    store = Mock()
    store.skill_exists.return_value = True
    store.append_record = AsyncMock()
    snapshots = {}
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots=snapshots)

    result = await submission_service.apply_experience_drafts(
        {"kind": "skill", "name": "skill-a"},
        [{"summary": "Use parser fields", "content": "Prefer parser fields."}],
    )

    assert result["success"] is True
    assert result["operation"] == "evolve"
    assert result["applied_count"] == 1
    assert result["pending_count"] == 0
    assert snapshots == {}
    store.skill_exists.assert_called_once_with("skill-a", subject_kind="skill")
    store.append_record.assert_awaited_once()
    assert store.append_record.await_args.kwargs == {"subject_kind": "skill"}


@pytest.mark.asyncio
async def test_apply_experience_drafts_retains_unwritten_tail_on_partial_failure():
    store = Mock()
    store.skill_exists.return_value = True
    store.append_record = AsyncMock(side_effect=[None, RuntimeError("disk full")])
    snapshots = {}
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots=snapshots)

    result = await submission_service.apply_experience_drafts(
        {"kind": "skill", "name": "skill-a"},
        [
            {"summary": "First", "content": "First content."},
            {"summary": "Second", "content": "Second content."},
        ],
    )

    assert result["success"] is False
    assert result["status"] == "partial"
    assert result["pending_count"] == 1
    assert result["request_id"] in snapshots
    assert len(snapshots[result["request_id"]].payload) == 1
    assert snapshots[result["request_id"]].subject_kind == "skill"


@pytest.mark.asyncio
async def test_apply_experience_drafts_allows_binary_evolution_assets(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "xlsx"
    assets_dir = skill_dir / "evolution" / "assets"
    assets_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# xlsx\n", encoding="utf-8")
    workbook_bytes = b"PK\x03\x04binary-workbook-\x86-content"
    workbook_path = assets_dir / "sample.xlsx"
    workbook_path.write_bytes(workbook_bytes)
    submission_service = ExperienceSubmissionService(
        store=EvolutionStore(str(root)),
        pending_approval_snapshots={},
    )

    result = await submission_service.apply_experience_drafts(
        {"kind": "skill", "name": "xlsx"},
        [{"summary": "Handle workbook templates", "content": "Preserve workbook assets during evolution."}],
    )

    assert result["success"] is True
    assert result["operation"] == "evolve"
    assert result["applied_count"] == 1
    assert result["pending_count"] == 0
    assert result["errors"] == []
    assert workbook_path.read_bytes() == workbook_bytes


def test_submission_service_normalizes_team_skill_alias_to_swarm_skill():
    store = Mock()
    store.skill_exists.return_value = True
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})

    subject, draft = submission_service.validate_experience_drafts(
        {"kind": "team-skill", "name": "team-a"},
        [{"summary": "Clarify handoff", "content": "Clarify handoff before delegating."}],
    )

    assert subject.to_payload() == {"kind": "swarm-skill", "name": "team-a"}
    assert draft.subject.to_payload() == {"kind": "swarm-skill", "name": "team-a"}
    store.skill_exists.assert_called_once_with("team-a", subject_kind="swarm-skill")


def test_submission_service_accepts_legacy_team_skill_subject_alias():
    store = Mock()
    store.skill_exists.return_value = True
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})

    subject, _ = submission_service.validate_experience_drafts(
        {"kind": "team-skill", "name": "team-a"},
        [{"summary": "Clarify handoff", "content": "Clarify handoff before delegating."}],
    )

    assert subject.to_payload() == {"kind": "swarm-skill", "name": "team-a"}


@pytest.mark.asyncio
async def test_submission_service_accepts_shared_subject_kinds_without_configuration():
    store = Mock()
    store.skill_exists.return_value = True
    store.append_record = AsyncMock()
    shared_submission_service = ExperienceSubmissionService(
        store=store,
        pending_approval_snapshots={},
    )

    result = await shared_submission_service.apply_experience_drafts(
        {"kind": "team-skill", "name": "team-a"},
        [{"summary": "Clarify handoff", "content": "Clarify ownership boundaries."}],
    )

    assert result["operation"] == "evolve"
    assert result["subject"]["kind"] == "swarm-skill"
    store.append_record.assert_awaited_once()
    assert store.append_record.await_args.args[0] == "team-a"
    assert store.append_record.await_args.kwargs == {"subject_kind": "swarm-skill"}


@pytest.mark.asyncio
async def test_apply_simplify_actions_executes_normalized_actions():
    record = _make_record("ev_1")
    store = Mock()
    store.skill_exists.return_value = True
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    store.update_record_content = AsyncMock(return_value=record)
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})

    result = await submission_service.apply_simplify_actions(
        {"kind": "skill", "name": "skill-a"},
        [{"action": "refine", "record_id": "ev_1", "new_content": "better"}],
    )

    assert result["success"] is True
    assert result["operation"] == "simplify"
    assert result["applied_count"] == 1
    assert result["action_counts"]["refined"] == 1
    store.load_full_evolution_log.assert_awaited_once_with("skill-a", subject_kind="skill")
    store.update_record_content.assert_awaited_once_with("skill-a", "ev_1", "better", subject_kind="skill")


@pytest.mark.asyncio
async def test_apply_simplify_actions_rejects_missing_merge_refs():
    store = Mock()
    store.skill_exists.return_value = True
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[_make_record("ev_1")]))
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})

    with pytest.raises(ValidationError, match="record not found: ev_2"):
        await submission_service.apply_simplify_actions(
            {"kind": "skill", "name": "browser"},
            [{"action": "MERGE", "record_id": "ev_1", "merge_remove_ids": ["ev_2"], "new_content": "Merged."}],
        )


@pytest.mark.asyncio
async def test_apply_simplify_actions_returns_partial_when_action_errors(monkeypatch):
    store = Mock()
    store.skill_exists.return_value = True
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[_make_record("ev_1")]))
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})

    async def fake_execute_simplify_actions(**kwargs):
        return {"deleted": 0, "merged": 0, "refined": 0, "kept": 0, "errors": 1}

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.experience.submission.execute_simplify_actions",
        fake_execute_simplify_actions,
    )

    result = await submission_service.apply_simplify_actions(
        {"kind": "skill", "name": "browser"},
        [{"action": "DELETE", "record_id": "ev_1"}],
    )

    assert result["success"] is False
    assert result["status"] == "partial"
    assert result["operation"] == "simplify"


def test_prepare_evolve_submission_resolves_and_validates():
    store = Mock()
    store.skill_exists.return_value = True
    submission_service = ExperienceSubmissionService(store=store)

    review_runtime = Mock()
    mock_resolved = Mock()
    mock_resolved.experience_drafts = (
        {"summary": "s", "content": "c", "target": "body", "section": "Troubleshooting"},
    )
    review_runtime.resolve_selected_proposals.return_value = mock_resolved

    result = submission_service.prepare_evolve_submission(
        review_runtime=review_runtime,
        evolution_review_ref="ref-1",
        subject={"kind": "skill", "name": "test"},
        selected_proposal_ids=["p1"],
        session_id="session-1",
    )

    review_runtime.resolve_selected_proposals.assert_called_once_with(
        "ref-1",
        subject={"kind": "skill", "name": "test"},
        selected_proposal_ids=["p1"],
        session_id="session-1",
    )
    assert result is mock_resolved


def test_prepare_evolve_submission_propagates_invalid_ref():
    store = Mock()
    store.skill_exists.return_value = True
    submission_service = ExperienceSubmissionService(store=store)

    review_runtime = Mock()
    review_runtime.resolve_selected_proposals.side_effect = ValueError("unknown ref")

    with pytest.raises(ValueError, match="unknown ref"):
        submission_service.prepare_evolve_submission(
            review_runtime=review_runtime,
            evolution_review_ref="bad-ref",
            subject={"kind": "skill", "name": "test"},
            selected_proposal_ids=["p1"],
            session_id="session-1",
        )


def test_prepare_evolve_submission_propagates_invalid_drafts():
    store = Mock()
    store.skill_exists.return_value = True
    submission_service = ExperienceSubmissionService(store=store)

    review_runtime = Mock()
    mock_resolved = Mock()
    mock_resolved.experience_drafts = ()
    review_runtime.resolve_selected_proposals.return_value = mock_resolved

    with pytest.raises(ValidationError):
        submission_service.prepare_evolve_submission(
            review_runtime=review_runtime,
            evolution_review_ref="ref-1",
            subject={"kind": "skill", "name": "test"},
            selected_proposal_ids=["p1"],
            session_id="session-1",
        )


def test_prepare_evolve_submission_does_not_consume_ref():
    store = Mock()
    store.skill_exists.return_value = True
    submission_service = ExperienceSubmissionService(store=store)

    review_runtime = Mock()
    mock_resolved = Mock()
    mock_resolved.experience_drafts = (
        {"summary": "s", "content": "c", "target": "body", "section": "Troubleshooting"},
    )
    review_runtime.resolve_selected_proposals.return_value = mock_resolved

    submission_service.prepare_evolve_submission(
        review_runtime=review_runtime,
        evolution_review_ref="ref-1",
        subject={"kind": "skill", "name": "test"},
        selected_proposal_ids=["p1"],
        session_id="session-1",
    )

    review_runtime.consume_prepared_submission.assert_not_called()


@pytest.mark.asyncio
async def test_apply_prepared_evolve_submission_uses_prepared_record_source():
    store = Mock()
    store.skill_exists.return_value = True
    store.append_record = AsyncMock()
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})
    prepared = Mock()
    prepared.subject = {"kind": "skill", "name": "test"}
    prepared.record_source = "execution_failure"
    prepared.experience_drafts = (
        {"summary": "Use parser fields", "content": "Prefer parser fields.", "target": "body", "section": "Troubleshooting"},
    )

    await submission_service.apply_prepared_evolve_submission(prepared, source="agent_evolve_tool")

    written_record = store.append_record.await_args.args[1]
    assert written_record.source == "execution_failure"


@pytest.mark.asyncio
async def test_apply_prepared_evolve_submission_falls_back_to_source_without_record_source():
    store = Mock()
    store.skill_exists.return_value = True
    store.append_record = AsyncMock()
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})
    prepared = Mock()
    prepared.subject = {"kind": "skill", "name": "test"}
    prepared.record_source = None
    prepared.experience_drafts = (
        {"summary": "Use parser fields", "content": "Prefer parser fields.", "target": "body", "section": "Troubleshooting"},
    )

    await submission_service.apply_prepared_evolve_submission(prepared)

    written_record = store.append_record.await_args.args[1]
    assert written_record.source == "agent_evolve_tool"
