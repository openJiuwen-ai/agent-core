# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience import ExperienceProposal
from openjiuwen.agent_evolving.experience.query import ExperienceQueryService
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService
from openjiuwen.agent_evolving.experience.submission import ExperienceSubmissionService
from openjiuwen.agent_evolving.experience.types import PendingChange
from openjiuwen.agent_evolving.experience.skill_experience_manager import ExperienceManager
from openjiuwen.agent_evolving.types import ApplyResult, UpdateValue
from openjiuwen.agent_evolving.update_execution import summarize_apply_results
from openjiuwen.core.operator.skill_call import SkillExperienceOperator


def _make_record(*, content: str = "experience content") -> EvolutionRecord:
    return EvolutionRecord.make(
        source="signal:skill-a",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )


def _make_archive_pair() -> Mock:
    pair = Mock()
    pair.version = "v1"
    pair.evolution_archive_name = "evolutions.v1.json"
    pair.to_payload.return_value = {
        "version": "v1",
        "skill_archive": "SKILL.v1.md",
        "evolution_archive": "evolutions.v1.json",
    }
    return pair


def _make_archive_service() -> Mock:
    archive_service = Mock()
    archive_service.archive_current_pair = AsyncMock(return_value=_make_archive_pair())
    archive_service.prune = Mock()
    return archive_service


def _make_manager(*, language: str = "cn") -> ExperienceManager:
    store = Mock()
    store.append_record = AsyncMock()
    scorer = Mock()
    rebuild_service = ExperienceRebuildService(store=store, archive_service=_make_archive_service())
    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        language=language,
        rebuild_service=rebuild_service,
    )
    return manager


def test_manager_accepts_shared_service_instances_for_injection():
    store = Mock()
    store.append_record = AsyncMock()
    scorer = Mock()
    query_service = ExperienceQueryService(store=store)
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})
    rebuild_service = ExperienceRebuildService(store=store)

    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        language="cn",
        query_service=query_service,
        submission_service=submission_service,
        rebuild_service=rebuild_service,
    )

    assert manager.experience_query_service is query_service
    assert manager.experience_submission_service is submission_service
    assert manager.rebuild_service is rebuild_service


@pytest.mark.asyncio
async def test_manager_list_skill_experiences_delegates_to_query_service(monkeypatch):
    manager = _make_manager()
    fake = AsyncMock(return_value={"operation": "list", "items": []})
    monkeypatch.setattr(manager.experience_query_service, "list_experiences", fake)

    result = await manager.list_skill_experiences({"kind": "skill", "name": "skill-a"}, limit=5)

    assert result == {"operation": "list", "items": []}
    fake.assert_awaited_once_with(
        {"kind": "skill", "name": "skill-a"},
        min_score=None,
        limit=5,
        cursor=None,
        target=None,
        section=None,
        query=None,
        sort="score_desc",
    )


@pytest.mark.asyncio
async def test_manager_apply_experience_drafts_delegates_to_submission_service(monkeypatch):
    manager = _make_manager()
    fake = AsyncMock(return_value={"operation": "evolve", "success": True})
    monkeypatch.setattr(manager.experience_submission_service, "apply_experience_drafts", fake)

    result = await manager.apply_experience_drafts(
        {"kind": "skill", "name": "skill-a"},
        [{"summary": "Use parser fields", "content": "Prefer parser fields."}],
        source="passive_agent",
    )

    assert result == {"operation": "evolve", "success": True}
    fake.assert_awaited_once_with(
        {"kind": "skill", "name": "skill-a"},
        [{"summary": "Use parser fields", "content": "Prefer parser fields."}],
        source="passive_agent",
    )


@pytest.mark.asyncio
async def test_manager_request_rebuild_delegates_to_rebuild_service(monkeypatch):
    manager = _make_manager(language="en")
    fake = AsyncMock(
        return_value={
            "subject": {"kind": "skill", "name": "skill-a"},
            "records": [],
            "overflow_index": {"items": []},
        }
    )
    monkeypatch.setattr(manager.rebuild_service, "prepare_rebuild_context", fake)

    prompt = await manager.request_rebuild("skill-a", user_intent="optimize")

    assert prompt is not None
    assert "evolve_rebuild" in prompt
    fake.assert_awaited_once_with(
        {"kind": "skill", "name": "skill-a"},
        user_intent="optimize",
        min_score=0.5,
        max_context_records=40,
        max_context_chars=20000,
    )


@pytest.mark.asyncio
async def test_manager_uses_shared_query_service_for_supported_subject_kinds():
    store = Mock()
    scorer = Mock()
    query_service = ExperienceQueryService(store=store)
    query_service.list_experiences = AsyncMock(
        side_effect=[
            {"operation": "list", "subject": {"kind": "skill", "name": "skill-a"}},
            {"operation": "list", "subject": {"kind": "swarm-skill", "name": "team-a"}},
        ]
    )

    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        query_service=query_service,
    )

    asyncio_result = await manager.list_skill_experiences(
        {"kind": "skill", "name": "skill-a"},
    )
    assert asyncio_result["subject"]["kind"] == "skill"

    asyncio_swarm_result = await manager.list_skill_experiences(
        {"kind": "team-skill", "name": "team-a"},
    )
    assert asyncio_swarm_result["subject"]["kind"] == "swarm-skill"

    assert query_service.list_experiences.await_count == 2
    query_service.list_experiences.assert_any_await(
        {"kind": "skill", "name": "skill-a"},
        min_score=None,
        limit=20,
        cursor=None,
        target=None,
        section=None,
        query=None,
        sort="score_desc",
    )
    query_service.list_experiences.assert_any_await(
        {"kind": "team-skill", "name": "team-a"},
        min_score=None,
        limit=20,
        cursor=None,
        target=None,
        section=None,
        query=None,
        sort="score_desc",
    )


@pytest.mark.asyncio
async def test_manager_uses_shared_submission_service_for_supported_subject_kinds():
    store = Mock()
    scorer = Mock()
    store.skill_exists.return_value = True
    store.append_record = AsyncMock()
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots={})
    submission_service.apply_experience_drafts = AsyncMock(
        side_effect=[
            {"subject": {"kind": "skill", "name": "skill-a"}, "operation": "evolve", "request_id": "skill"},
            {"subject": {"kind": "swarm-skill", "name": "team-a"}, "operation": "evolve", "request_id": "swarm"},
        ]
    )

    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        submission_service=submission_service,
    )

    await manager.apply_experience_drafts(
        {"kind": "skill", "name": "skill-a"},
        [{"summary": "For skill", "content": "skill record"}],
    )
    await manager.apply_experience_drafts(
        {"kind": "team-skill", "name": "team-a"},
        [{"summary": "For swarm", "content": "swarm record"}],
    )

    assert submission_service.apply_experience_drafts.await_count == 2
    submission_service.apply_experience_drafts.assert_any_await(
        {"kind": "skill", "name": "skill-a"},
        [{"summary": "For skill", "content": "skill record"}],
        source="agent_evolve_tool",
    )
    submission_service.apply_experience_drafts.assert_any_await(
        {"kind": "team-skill", "name": "team-a"},
        [{"summary": "For swarm", "content": "swarm record"}],
        source="agent_evolve_tool",
    )


def test_manager_bind_pending_approval_snapshots_rebinds_shared_submission_service():
    store = Mock()
    scorer = Mock()
    first_snapshots = {"seed": PendingChange.make("seed", [])}
    submission_service = ExperienceSubmissionService(store=store, pending_approval_snapshots=first_snapshots)

    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        submission_service=submission_service,
    )

    shared: dict[str, PendingChange] = {}
    manager.bind_pending_approval_snapshots(shared)

    assert manager.pending_approval_snapshots is shared
    assert submission_service._pending_approval_snapshots is shared


@pytest.mark.asyncio
async def test_request_rebuild_uses_shared_service_with_subject_envelope():
    store = Mock()
    scorer = Mock()
    store.skill_exists.return_value = True
    rebuild_service = ExperienceRebuildService(store=store)
    rebuild_service.prepare_rebuild_context = AsyncMock(
        side_effect=[
            {
                "subject": {"kind": "skill", "name": "skill-a"},
                "records": [],
                "overflow_index": {"items": []},
                "skill_name": "skill-a",
                "min_score": 0.5,
            },
            {
                "subject": {"kind": "swarm-skill", "name": "team-a"},
                "records": [],
                "overflow_index": {"items": []},
                "skill_name": "team-a",
                "min_score": 0.5,
            },
        ]
    )
    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        rebuild_service=rebuild_service,
    )

    await manager.request_rebuild(
        "skill-a",
        subject={"kind": "skill", "name": "skill-a"},
        user_intent="optimize skill",
    )
    await manager.request_rebuild(
        "team-a",
        subject={"kind": "team-skill", "name": "team-a"},
        user_intent="optimize team",
    )

    assert rebuild_service.prepare_rebuild_context.await_count == 2
    rebuild_service.prepare_rebuild_context.assert_any_await(
        {"kind": "skill", "name": "skill-a"},
        user_intent="optimize skill",
        min_score=0.5,
        max_context_records=40,
        max_context_chars=20000,
    )
    rebuild_service.prepare_rebuild_context.assert_any_await(
        {"kind": "team-skill", "name": "team-a"},
        user_intent="optimize team",
        min_score=0.5,
        max_context_records=40,
        max_context_chars=20000,
    )


@pytest.mark.asyncio
async def test_list_skill_experiences_uses_subject_envelope():
    manager = _make_manager()
    record = _make_record(content="Keep structured fields.")
    record.id = "ev_1"
    record.summary = "Use structured fields when parsing output."
    manager._store.skill_exists.return_value = True
    manager._store.get_records_by_score = AsyncMock(return_value=[record])

    result = await manager.list_skill_experiences(
        {"kind": "skill", "name": "skill-a"},
        min_score=0.5,
        limit=20,
    )

    assert result["subject"] == {"kind": "skill", "name": "skill-a"}
    assert result["total_count"] == 1
    assert result["items"][0]["record_id"] == "ev_1"
    assert result["items"][0]["summary"] == "Use structured fields when parsing output."
    assert "content" not in result["items"][0]


@pytest.mark.asyncio
async def test_list_skill_experiences_query_uses_pipe_separated_or_terms():
    manager = _make_manager()
    parser_record = _make_record(content="Prefer structured parser fields.")
    parser_record.id = "ev_parser"
    parser_record.summary = "Use parser fields when parsing output."
    other_record = _make_record(content="Keep final reports concise.")
    other_record.id = "ev_report"
    other_record.summary = "Keep final reports concise."
    manager._store.skill_exists.return_value = True
    manager._store.get_records_by_score = AsyncMock(return_value=[other_record, parser_record])

    result = await manager.list_skill_experiences(
        {"kind": "skill", "name": "skill-a"},
        query="capture|parser|lesson",
    )

    assert result["total_count"] == 1
    assert [item["record_id"] for item in result["items"]] == ["ev_parser"]


@pytest.mark.asyncio
async def test_list_skill_experiences_query_supports_chinese_literal_terms():
    manager = _make_manager()
    parser_record = _make_record(content="解析器失败时记录原始输入。")
    parser_record.id = "ev_parser_cn"
    parser_record.summary = "解析器失败时避免重复记录。"
    other_record = _make_record(content="生成报告时保持格式一致。")
    other_record.id = "ev_report_cn"
    other_record.summary = "生成报告时保持格式一致。"
    manager._store.skill_exists.return_value = True
    manager._store.get_records_by_score = AsyncMock(return_value=[other_record, parser_record])

    result = await manager.list_skill_experiences(
        {"kind": "skill", "name": "skill-a"},
        query="解析器|重复|记录",
    )

    assert result["total_count"] == 1
    assert [item["record_id"] for item in result["items"]] == ["ev_parser_cn"]


@pytest.mark.asyncio
async def test_list_skill_experiences_query_treats_spaces_as_literal_text():
    manager = _make_manager()
    parser_record = _make_record(content="Prefer structured parser fields.")
    parser_record.id = "ev_parser"
    parser_record.summary = "Use parser fields when parsing output."
    manager._store.skill_exists.return_value = True
    manager._store.get_records_by_score = AsyncMock(return_value=[parser_record])

    result = await manager.list_skill_experiences(
        {"kind": "skill", "name": "skill-a"},
        query="capture parser lesson",
    )

    assert result["total_count"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_read_agent_experiences_requires_record_ids():
    manager = _make_manager()
    record = _make_record(content="Keep structured fields.")
    record.id = "ev_1"
    manager._store.skill_exists.return_value = True
    manager._store.load_records_by_ids = AsyncMock(return_value=[record])

    result = await manager.read_agent_experiences(
        {"kind": "skill", "name": "skill-a"},
        record_ids=["ev_1"],
        max_content_chars=8,
    )

    assert result["operation"] == "read"
    assert result["items"][0]["record_id"] == "ev_1"
    assert result["items"][0]["content"] == "Keep str"


@pytest.mark.asyncio
async def test_apply_experience_drafts_persists_records():
    manager = _make_manager()
    manager._store.skill_exists.return_value = True
    manager._store.append_record = AsyncMock()

    result = await manager.apply_experience_drafts(
        {"kind": "skill", "name": "skill-a"},
        [{"summary": "Use parser fields", "content": "Prefer structured parser fields."}],
    )

    assert result["operation"] == "evolve"
    assert result["success"] is True
    assert result["applied_count"] == 1
    assert result["pending_count"] == 0
    assert len(result["record_ids"]) == 1
    manager._store.append_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_simplify_drafts_executes_actions():
    manager = _make_manager()
    record = _make_record(content="Old content")
    record.id = "ev_1"
    manager._store.skill_exists.return_value = True
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.update_record_content = AsyncMock(return_value=record)

    result = await manager.apply_simplify_drafts(
        {"kind": "skill", "name": "skill-a"},
        [{"action": "REFINE", "record_id": "ev_1", "new_content": "Better content"}],
    )

    assert result["operation"] == "simplify"
    assert result["success"] is True
    assert result["applied_count"] == 1
    assert result["action_counts"]["refined"] == 1


@pytest.mark.asyncio
async def test_apply_experience_drafts_retains_unwritten_tail_on_partial_failure():
    manager = _make_manager()
    manager._store.skill_exists.return_value = True
    manager._store.append_record = AsyncMock(side_effect=[None, RuntimeError("disk full")])

    result = await manager.apply_experience_drafts(
        {"kind": "skill", "name": "skill-a"},
        [
            {"summary": "First", "content": "First content."},
            {"summary": "Second", "content": "Second content."},
        ],
    )

    assert result["success"] is False
    assert result["status"] == "partial"
    assert result["applied_count"] == 1
    assert result["pending_count"] == 1
    assert result["retry_request_id"] == result["request_id"]
    assert result["errors"] == ["disk full"]
    assert result["request_id"] in manager.pending_approval_snapshots
    assert len(manager.pending_approval_snapshots[result["request_id"]].payload) == 1


@pytest.mark.asyncio
async def test_apply_simplify_drafts_reports_partial_failure_when_action_errors():
    manager = _make_manager()
    record = _make_record(content="Old content")
    record.id = "ev_1"
    manager._store.skill_exists.return_value = True
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.update_record_content = AsyncMock(return_value=None)

    result = await manager.apply_simplify_drafts(
        {"kind": "skill", "name": "skill-a"},
        [{"action": "REFINE", "record_id": "ev_1", "new_content": "Better content"}],
    )

    assert result["success"] is False
    assert result["applied_count"] == 0
    assert result["action_counts"]["errors"] == 1


@pytest.mark.asyncio
async def test_commit_proposal_uses_shared_lifecycle(monkeypatch):
    manager = _make_manager()
    record = _make_record(content="commit")
    proposal = ExperienceProposal(skill_name="skill-a", records=[record], requires_approval=False)
    captured: dict[str, object] = {}

    original_stage_records = manager.stage_records
    original_approve_request = manager.approve_request

    def capture_stage(skill_name, records, **kwargs):
        request = original_stage_records(skill_name, records, **kwargs)
        captured["request_id"] = request.request_id
        return request

    async def capture_approve(request_id):
        captured["approved_request_id"] = request_id
        return await original_approve_request(request_id)

    monkeypatch.setattr(manager, "stage_records", capture_stage)
    monkeypatch.setattr(manager, "approve_request", capture_approve)

    result = await manager.commit_proposal(proposal)

    assert result.applied_count == 1
    assert captured["approved_request_id"] == captured["request_id"]
    assert manager.pending_approval_snapshots == {}
    manager._store.append_record.assert_awaited_once_with("skill-a", record, subject_kind=None)


def test_stage_records_registers_pending_change_in_snapshot_store():
    manager = _make_manager()
    record = _make_record()

    request = manager.stage_records("skill-a", [record], requires_approval=True)

    assert request.request_id in manager.pending_approval_snapshots
    staged = manager.pending_approval_snapshots[request.request_id]
    assert staged.skill_name == "skill-a"
    assert summarize_apply_results(request.apply_results) == {"total": 1, "applied": 1, "failed": 0}
    host_result = request.to_host_result()
    assert host_result.status == "pending_approval"
    assert host_result.effect == "pending_change"
    assert host_result.request_id == request.request_id
    assert host_result.pending_count == 1


def test_stage_records_returns_staged_pending_when_stage_helper_rewrites_snapshot(monkeypatch):
    manager = _make_manager()
    record = _make_record()
    original_stage = manager._stage_pending_change

    def stage_with_rewrite(pending):
        staged = original_stage(pending)
        clone = PendingChange(
            operator_id=staged.operator_id,
            skill_name=staged.skill_name,
            change_type=staged.change_type,
            payload=list(staged.payload),
            created_at=staged.created_at,
            change_id=f"rewritten_{staged.change_id}",
        )
        manager.pending_approval_snapshots.pop(staged.change_id, None)
        manager.pending_approval_snapshots[clone.change_id] = clone
        return clone

    monkeypatch.setattr(manager, "_stage_pending_change", stage_with_rewrite)

    request = manager.stage_records("skill-a", [record], requires_approval=True)

    assert request.pending_change is not None
    assert request.request_id == request.pending_change.change_id
    assert request.request_id.startswith("rewritten_")
    assert request.pending_change is manager.pending_approval_snapshots[request.request_id]


def test_stage_records_uses_manager_apply_updates_without_operator_pending_state():
    manager = _make_manager()
    record = _make_record()
    skill_op = manager.skill_ops["skill-a"] = SkillExperienceOperator("skill-a")

    request = manager.stage_records("skill-a", [record], requires_approval=True)

    assert request.request_id in manager.pending_approval_snapshots
    assert request.pending_change is not None
    assert request.pending_change.payload == [record]
    assert request.pending_change.change_type == "skill_experience_entry"
    assert request.apply_results[0].records == [record]
    assert request.apply_results[0].change_type == "skill_experience_entry"
    assert request.apply_results[0].lifecycle_stage == "local_apply_completed"
    assert skill_op.get_state() == {}


def test_bind_pending_approval_snapshots_rebinds_snapshot_store():
    manager = _make_manager()
    first_request = manager.stage_records(
        "skill-a",
        [_make_record(content="first")],
        requires_approval=True,
    )

    rebound_snapshots = {}
    manager.bind_pending_approval_snapshots(rebound_snapshots)
    second_request = manager.stage_records(
        "skill-a",
        [_make_record(content="second")],
        requires_approval=True,
    )

    assert first_request.request_id not in rebound_snapshots
    assert second_request.request_id in rebound_snapshots
    assert manager.pending_approval_snapshots is rebound_snapshots


def test_stage_apply_results_returns_staged_pending_when_stage_helper_rewrites_snapshot(monkeypatch):
    manager = _make_manager()
    record = _make_record(content="apply-result")
    original_stage = manager._stage_pending_change

    def stage_with_rewrite(pending):
        staged = original_stage(pending)
        clone = PendingChange(
            operator_id=staged.operator_id,
            skill_name=staged.skill_name,
            change_type=staged.change_type,
            payload=list(staged.payload),
            created_at=staged.created_at,
            change_id=f"rewritten_{staged.change_id}",
        )
        manager.pending_approval_snapshots.pop(staged.change_id, None)
        manager.pending_approval_snapshots[clone.change_id] = clone
        return clone

    monkeypatch.setattr(manager, "_stage_pending_change", stage_with_rewrite)

    request = manager.stage_apply_results(
        "skill-a",
        [
            ApplyResult(
                operator_id="skill_experience_skill-a",
                target="experiences",
                applied=True,
                mode="append",
                effect="pending_change",
                records=[record],
                change_type="skill_experience_entry",
                lifecycle_stage="local_apply_completed",
            )
        ],
    )

    assert request.pending_change is not None
    assert request.request_id == request.pending_change.change_id
    assert request.request_id.startswith("rewritten_")
    assert request.pending_change is manager.pending_approval_snapshots[request.request_id]


def test_stage_apply_results_exposes_proposal_fields_and_apply_results():
    manager = _make_manager()
    record = _make_record(content="apply-result")
    request = manager.stage_apply_results(
        "skill-a",
        [
            ApplyResult(
                operator_id="skill_experience_skill-a",
                target="experiences",
                applied=True,
                mode="append",
                effect="pending_change",
                records=[record],
                change_type="skill_experience_entry",
                lifecycle_stage="local_apply_completed",
            )
        ],
        user_query="explicit",
        signal_type="user_intent",
        signal_source="explicit_request",
    )

    assert request.proposal.user_query == "explicit"
    assert request.proposal.signal_type == "user_intent"
    assert request.proposal.signal_source == "explicit_request"
    assert len(request.apply_results) == 1


def test_stage_records_uses_manager_apply_updates_semantics(monkeypatch):
    manager = _make_manager()
    record = _make_record()
    captured: dict[str, object] = {}

    def fake_apply_updates(operators, updates):
        captured["operators"] = operators
        captured["updates"] = updates
        return [
            ApplyResult(
                operator_id="skill_experience_skill-a",
                target="experiences",
                applied=True,
                mode="append",
                effect="pending_change",
                value=[record],
                records=[record],
                change_type="skill_experience_entry",
                lifecycle_stage="local_apply_completed",
            )
        ]

    monkeypatch.setattr(manager, "apply_updates", fake_apply_updates)

    request = manager.stage_records("skill-a", [record], requires_approval=True)

    assert summarize_apply_results(request.apply_results) == {"total": 1, "applied": 1, "failed": 0}
    assert list(captured["operators"]) == ["skill_experience_skill-a"]
    assert captured["updates"] == {
        ("skill_experience_skill-a", "experiences"): UpdateValue(
            payload=[record],
            mode="append",
            effect="pending_change",
            change_type="skill_experience_entry",
        )
    }


@pytest.mark.asyncio
async def test_approve_request_applies_pending_snapshot_and_clears_on_success():
    manager = _make_manager()
    record = _make_record()
    pending = manager.stage_records("skill-a", [record], requires_approval=True)

    result = await manager.approve_request(pending.request_id)

    assert result.applied_count == 1
    assert result.pending_count == 0
    assert result.to_host_result(request_id=pending.request_id).status == "persisted"
    assert result.to_host_result(request_id=pending.request_id).applied_count == 1
    assert pending.request_id not in manager.pending_approval_snapshots
    manager._store.append_record.assert_awaited_once_with("skill-a", record, subject_kind=None)


@pytest.mark.asyncio
async def test_approve_request_applies_only_approved_record_ids():
    manager = _make_manager()
    record_1 = _make_record(content="r1")
    record_2 = _make_record(content="r2")
    pending = manager.stage_records("skill-a", [record_1, record_2], requires_approval=True)

    result = await manager.approve_request(pending.request_id, approved_record_ids=[record_1.id])

    assert result.applied_count == 1
    assert result.rejected_count == 1
    assert result.pending_count == 0
    assert pending.request_id not in manager.pending_approval_snapshots
    manager._store.append_record.assert_awaited_once_with("skill-a", record_1, subject_kind=None)


@pytest.mark.asyncio
async def test_approve_request_failure_retries_only_approved_record_ids():
    manager = _make_manager()
    record_1 = _make_record(content="r1")
    record_2 = _make_record(content="r2")
    pending = manager.stage_records("skill-a", [record_1, record_2], requires_approval=True)
    manager._store.append_record = AsyncMock(side_effect=OSError("disk full"))

    result = await manager.approve_request(pending.request_id, approved_record_ids=[record_1.id])

    assert result.applied_count == 0
    assert result.rejected_count == 1
    assert result.pending_count == 1
    assert result.errors == ["disk full"]
    assert manager.pending_approval_snapshots[pending.request_id].payload == [record_1]

    manager._store.append_record = AsyncMock()
    retry = await manager.retry_request(pending.request_id)

    assert retry.applied_count == 1
    assert retry.rejected_count == 0
    assert retry.pending_count == 0
    manager._store.append_record.assert_awaited_once_with("skill-a", record_1, subject_kind=None)


@pytest.mark.asyncio
async def test_approve_request_retains_snapshot_on_record_failure():
    manager = _make_manager()
    record_1 = _make_record(content="r1")
    record_2 = _make_record(content="r2")
    pending = manager.stage_records("skill-a", [record_1, record_2], requires_approval=True)
    manager._store.append_record = AsyncMock(side_effect=[None, OSError("disk full")])

    result = await manager.approve_request(pending.request_id)

    assert result.applied_count == 1
    assert result.pending_count == 1
    assert result.errors == ["disk full"]
    host_result = result.to_host_result(request_id=pending.request_id)
    assert host_result.status == "partial"
    assert host_result.pending_count == 1
    assert pending.request_id in manager.pending_approval_snapshots
    assert manager.pending_approval_snapshots[pending.request_id].payload == [record_2]


@pytest.mark.asyncio
async def test_reject_request_discards_snapshot():
    manager = _make_manager()
    pending = manager.stage_records("skill-a", [_make_record(), _make_record()], requires_approval=True)

    result = await manager.reject_request(pending.request_id)

    assert result.rejected_count == 2
    assert result.to_host_result(request_id=pending.request_id).status == "rejected"
    assert pending.request_id not in manager.pending_approval_snapshots


@pytest.mark.asyncio
async def test_retry_request_reuses_unified_lifecycle_for_pending_batch():
    manager = _make_manager()
    record_1 = _make_record(content="r1")
    record_2 = _make_record(content="r2")
    pending = manager.stage_records("skill-a", [record_1, record_2], requires_approval=True)
    manager._store.append_record = AsyncMock(side_effect=[None, OSError("disk full")])

    first = await manager.approve_request(pending.request_id)

    assert first.applied_count == 1
    assert first.pending_count == 1

    manager._store.append_record = AsyncMock()
    second = await manager.retry_request(pending.request_id)

    assert second.applied_count == 1
    assert second.pending_count == 0
    manager._store.append_record.assert_awaited_once_with("skill-a", record_2, subject_kind=None)


@pytest.mark.asyncio
async def test_request_simplify_stages_governance_and_event():
    manager = _make_manager()
    record = _make_record()
    manager._store.skill_exists = Mock(return_value=True)
    manager._store.skill_definition_exists = Mock(return_value=True)
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.read_skill_content = AsyncMock(return_value="# skill")
    manager._store.extract_description_from_skill_md = Mock(return_value="summary")
    manager._scorer.simplify = AsyncMock(return_value=[{"action": "KEEP", "record_id": record.id, "reason": "good"}])

    request_id = await manager.request_simplify("skill-a")

    assert request_id is not None
    assert request_id in manager.pending_governance
    assert manager.pending_governance[request_id]["kind"] == "simplify"
    assert not hasattr(manager, "pending_approval_events")


@pytest.mark.asyncio
async def test_request_simplify_skips_missing_skill_definition():
    manager = _make_manager()
    manager._store.skill_exists = Mock(return_value=True)
    manager._store.skill_definition_exists = Mock(return_value=False)
    manager._store.load_full_evolution_log = AsyncMock()
    manager._store.read_skill_content = AsyncMock()
    manager._scorer.simplify = AsyncMock()

    request_id = await manager.request_simplify("skill-a")

    assert request_id is None
    manager._store.load_full_evolution_log.assert_not_awaited()
    manager._store.read_skill_content.assert_not_awaited()
    manager._scorer.simplify.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_simplify_executes_actions():
    manager = _make_manager()
    manager.pending_governance["req-1"] = {
        "kind": "simplify",
        "skill_name": "skill-a",
        "actions": [{"action": "KEEP", "record_id": "ev_1", "reason": "good"}],
    }

    result = await manager.approve_simplify("req-1")

    assert result["kept"] == 1
    assert "req-1" not in manager.pending_governance


@pytest.mark.asyncio
async def test_request_rebuild_uses_shared_helper_and_template():
    manager = _make_manager(language="en")
    record = _make_record(content="good experience")
    record.score = 0.8
    manager._store.skill_exists = Mock(return_value=True)
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.clear_evolutions = AsyncMock()

    prompt = await manager.request_rebuild("skill-a", user_intent="optimize skill", min_score=0.5)

    assert prompt is not None
    assert "good experience" in prompt
    assert "skill-creator" in prompt.lower()
    manager.rebuild_service._archive_service.archive_current_pair.assert_awaited_once_with(
        "skill-a", subject_kind="skill"
    )
    manager._store.clear_evolutions.assert_awaited_once_with("skill-a", subject_kind="skill")


@pytest.mark.asyncio
async def test_request_rebuild_inlines_deterministic_context_and_clears_archived_evolutions():
    manager = _make_manager()
    record = _make_record(content="Always validate inputs strictly.")
    record.id = "ev_1"
    record.summary = "Prefer strict validation."
    record.score = 0.9
    manager._store.skill_exists.return_value = True
    manager._store.clear_evolutions = AsyncMock()
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))

    prompt = await manager.request_rebuild(
        "skill-a",
        user_intent="make it stricter",
        min_score=0.5,
        max_context_records=40,
        max_context_chars=20000,
    )

    assert "Deterministic Rebuild Context" in prompt
    assert "Always validate inputs strictly." in prompt
    manager._store.clear_evolutions.assert_awaited_once_with("skill-a", subject_kind="skill")
