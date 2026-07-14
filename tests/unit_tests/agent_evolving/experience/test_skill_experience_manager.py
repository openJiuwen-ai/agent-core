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


def _make_manager(*, language: str = "cn") -> ExperienceManager:
    store = Mock()
    store.append_record = AsyncMock()
    scorer = Mock()
    manager = ExperienceManager(
        store=store,
        scorer=scorer,
        kind="skill",
        language=language,
    )
    return manager


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
    manager._store.append_record.assert_awaited_once_with("skill-a", record)


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
    manager._store.append_record.assert_awaited_once_with("skill-a", record)


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
    manager._store.append_record.assert_awaited_once_with("skill-a", record_1)


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
    manager._store.append_record.assert_awaited_once_with("skill-a", record_1)


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
    manager._store.append_record.assert_awaited_once_with("skill-a", record_2)


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
    manager._store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    manager._store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    manager._store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    manager._store.clear_evolutions = AsyncMock()

    prompt = await manager.request_rebuild("skill-a", user_intent="optimize skill", min_score=0.5)

    assert prompt is not None
    assert "good experience" in prompt
    assert "skill-creator" in prompt.lower()
    manager._store.clear_evolutions.assert_awaited_once_with("skill-a")
