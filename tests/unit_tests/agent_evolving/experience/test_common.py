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
from openjiuwen.agent_evolving.experience.types import PendingChange
from openjiuwen.agent_evolving.experience import (
    ExperienceApprovalRequest,
    ExperienceApplyResult,
    ExperienceProposal,
)
from openjiuwen.agent_evolving.experience import types as experience_types
from openjiuwen.agent_evolving.experience.common import (
    execute_simplify_actions,
    make_pending_change,
    reject_pending_change,
    request_rebuild_context,
)
from openjiuwen.agent_evolving.experience.lifecycle import RebuildRequest


def _make_record() -> EvolutionRecord:
    return EvolutionRecord.make(
        source="signal:skill-a",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content="test content",
            target=EvolutionTarget.BODY,
        ),
    )


def test_public_experience_types_are_importable():
    record = _make_record()
    proposal = ExperienceProposal(skill_name="skill-a", records=[record], requires_approval=True)
    approval_request = ExperienceApprovalRequest(skill_name="skill-a", proposal=proposal)
    apply_result = ExperienceApplyResult(skill_name="skill-a", applied_count=1)
    rebuild_request = RebuildRequest(skill_name="skill-a", min_score=0.5)

    assert proposal.records == [record]
    assert approval_request.proposal is proposal
    assert apply_result.applied_count == 1
    assert rebuild_request.skill_name == "skill-a"


def test_internal_experience_lifecycle_types_are_not_public_contracts():
    assert not hasattr(experience_types, "LocalApplyPreview")
    assert not hasattr(experience_types, "PendingCommitResult")
    assert not hasattr(experience_types, "RebuildRequest")


def test_experience_apply_result_ok_ignores_rejections_but_not_pending_records():
    assert ExperienceApplyResult(skill_name="skill-a", applied_count=1, rejected_count=1).ok is True
    assert ExperienceApplyResult(skill_name="skill-a", applied_count=1, pending_count=1).ok is False
    assert ExperienceApplyResult(skill_name="skill-a", applied_count=1, errors=["disk failed"]).ok is False


def test_experience_apply_result_to_host_result_preserves_mixed_approval_counts():
    host_result = ExperienceApplyResult(
        skill_name="skill-a",
        applied_count=1,
        rejected_count=1,
    ).to_host_result(request_id="req-1")

    assert host_result.status == "partial"
    assert host_result.applied_count == 1
    assert host_result.rejected_count == 1
    assert host_result.pending_count == 0
    assert host_result.errors == []


def test_experience_apply_result_to_host_result_preserves_failed_selective_accept_counts():
    host_result = ExperienceApplyResult(
        skill_name="skill-a",
        rejected_count=1,
        pending_count=1,
        errors=["disk full"],
    ).to_host_result(request_id="req-1")

    assert host_result.status == "partial"
    assert host_result.applied_count == 0
    assert host_result.rejected_count == 1
    assert host_result.pending_count == 1
    assert host_result.errors == ["disk full"]


def test_make_pending_change_uses_checkpointing_snapshot_type():
    record = _make_record()

    pending = make_pending_change("skill-a", [record])

    assert isinstance(pending, PendingChange)
    assert pending.skill_name == "skill-a"
    assert pending.payload == [record]


def test_reject_pending_change_returns_rejected_count():
    pending = make_pending_change("skill-a", [_make_record(), _make_record()])

    result = reject_pending_change(pending)

    assert result.rejected_count == 2
    assert result.skill_name == "skill-a"


class TestExecuteSimplifyActions:
    @pytest.mark.asyncio
    async def test_delete_action(self):
        store = Mock()
        store.delete_records = AsyncMock(return_value=1)

        counts = await execute_simplify_actions(
            store,
            "skill-a",
            [{"action": "DELETE", "record_id": "ev_001", "reason": "old"}],
        )

        store.delete_records.assert_called_once_with("skill-a", ["ev_001"])
        assert counts["deleted"] == 1

    @pytest.mark.asyncio
    async def test_merge_action(self):
        store = Mock()
        store.merge_records = AsyncMock(return_value=_make_record())

        counts = await execute_simplify_actions(
            store,
            "skill-a",
            [
                {
                    "action": "MERGE",
                    "record_id": "ev_001",
                    "merge_remove_ids": ["ev_002", "ev_003"],
                    "new_content": "merged content",
                }
            ],
        )

        store.merge_records.assert_called_once_with("skill-a", "ev_001", ["ev_002", "ev_003"], "merged content")
        assert counts["merged"] == 1

    @pytest.mark.asyncio
    async def test_refine_action(self):
        store = Mock()
        store.update_record_content = AsyncMock(return_value=_make_record())

        counts = await execute_simplify_actions(
            store,
            "skill-a",
            [{"action": "REFINE", "record_id": "ev_001", "new_content": "better"}],
        )

        store.update_record_content.assert_called_once_with("skill-a", "ev_001", "better")
        assert counts["refined"] == 1

    @pytest.mark.asyncio
    async def test_keep_unknown_and_error_paths(self):
        store = Mock()
        store.delete_records = AsyncMock(side_effect=OSError("disk full"))

        counts = await execute_simplify_actions(
            store,
            "skill-a",
            [
                {"action": "KEEP", "record_id": "ev_keep"},
                {"action": "UNKNOWN", "record_id": "ev_unknown"},
                {"action": "DELETE", "record_id": "ev_fail"},
            ],
        )

        assert counts == {"deleted": 0, "merged": 0, "refined": 0, "kept": 1, "errors": 2}


class TestRequestRebuildContext:
    @pytest.mark.asyncio
    async def test_returns_none_when_skill_missing(self):
        store = Mock()
        store.skill_exists.return_value = False

        result = await request_rebuild_context(
            store,
            RebuildRequest(skill_name="missing"),
            format_records=lambda records: "",
            default_intent="default",
            template="{evolution_records}|{user_intent}|{min_score}",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_filters_records_and_clears_evolutions_after_success(self):
        keep = _make_record()
        keep.score = 0.8
        drop_low = _make_record()
        drop_low.score = 0.2
        drop_skip = _make_record()
        drop_skip.score = 0.9
        drop_skip.change.skip_reason = "skip"

        store = Mock()
        store.skill_exists.return_value = True
        store.archive_skill_body = AsyncMock(return_value="body-archive")
        store.archive_evolutions = AsyncMock(return_value="evo-archive")
        store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[keep, drop_low, drop_skip]))
        store.clear_evolutions = AsyncMock()

        result = await request_rebuild_context(
            store,
            RebuildRequest(skill_name="skill-a", user_intent="rebuild me", min_score=0.5),
            format_records=lambda records: ",".join(record.id for record in records),
            default_intent="default intent",
            template="{evolution_records}|{user_intent}|{min_score}",
        )

        assert result is not None
        assert result["filtered_records"] == [keep]
        assert result["prompt"] == f"{keep.id}|rebuild me|0.5"
        store.clear_evolutions.assert_awaited_once_with("skill-a")

    @pytest.mark.asyncio
    async def test_archive_failure_is_reported_but_prompt_still_builds(self):
        keep = _make_record()
        keep.score = 0.8

        store = Mock()
        store.skill_exists.return_value = True
        store.archive_skill_body = AsyncMock(return_value="body-archive")
        store.archive_evolutions = AsyncMock(side_effect=RuntimeError("archive failed"))
        store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[keep]))
        store.clear_evolutions = AsyncMock()

        result = await request_rebuild_context(
            store,
            RebuildRequest(skill_name="skill-a", min_score=0.5),
            format_records=lambda records: "formatted",
            default_intent="default intent",
            template="{evolution_records}|{user_intent}|{min_score}",
        )

        assert result is not None
        assert isinstance(result["archive_error"], RuntimeError)
        assert result["prompt"] == "formatted|default intent|0.5"
        store.clear_evolutions.assert_not_awaited()
